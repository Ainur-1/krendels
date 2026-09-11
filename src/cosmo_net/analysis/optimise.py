"""
Searching the configuration space the interface exposes: plane orientation and phasing.

Two searches, because they answer different questions. The spacing sweep walks the
family of evenly spread designs — every plane offset from the last by the same
angle — which is how a constellation is normally specified and which makes the
result something an engineer can read off as a rule. The coordinate refinement then
lets individual planes move, which finds designs the even family cannot express but
which are harder to justify.

Both are cheap enough to sit behind a button: an evaluation is 68 ms, so a 100-point
sweep is seven seconds on one core and about one across eight.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from cosmo_net.analysis.reachability import availability_series, longest_gap_steps
from cosmo_net.scenario.schema import Scenario


@dataclass(frozen=True)
class Candidate:
    """One configuration and what it scores."""

    raan_deg: list[float]
    phase_deg: list[float]
    launch_stage: int

    worst_availability: float
    worst_max_gap_s: int
    availability: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "raan_deg": self.raan_deg,
            "phase_deg": self.phase_deg,
            "launch_stage": self.launch_stage,
            "worst_availability": self.worst_availability,
            "worst_max_gap_s": self.worst_max_gap_s,
            "availability": self.availability,
        }


@dataclass
class SweepReport:
    """Everything a search tried, plus the shortlist worth looking at."""

    baseline: Candidate
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def best(self) -> Candidate:
        """Highest availability for the worst-served client; the shortest worst gap breaks ties."""

        return max(
            self.candidates or [self.baseline],
            key=lambda c: (c.worst_availability, -c.worst_max_gap_s),
        )

    @property
    def frontier(self) -> list[Candidate]:
        """
        The designs not beaten on both counts at once.

        Availability and the longest single interruption are not the same goal, and
        scenario 04 is where they come apart: the sweep's front there runs 73.1 % at
        a 12 720 s worst gap, 68.2 % at 5 880 s, 67.9 % at 3 360 s, 63.9 % at 2 760 s.
        Five points of availability against four hours of continuous silence is a
        choice about what the service is for, not something one number can settle.
        On scenario 01 the front collapses to a single design, because there one
        configuration is better on both counts at once.
        """

        ordered = sorted(
            self.candidates, key=lambda c: (-c.worst_availability, c.worst_max_gap_s)
        )
        front: list[Candidate] = []
        best_gap = float("inf")
        for candidate in ordered:
            if candidate.worst_max_gap_s < best_gap:
                front.append(candidate)
                best_gap = candidate.worst_max_gap_s
        return front

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.to_dict(),
            "best": self.best.to_dict(),
            "frontier": [c.to_dict() for c in self.frontier],
            "candidates": [c.to_dict() for c in self.candidates],
        }


def evaluate(scenario: Scenario) -> Candidate:
    """Score one configuration. The unit of work every search here is built from."""

    series = availability_series(scenario)
    step_s = scenario.environment.step_s
    availability = {client: float(r.mean()) for client, r in series.items()}
    return Candidate(
        raan_deg=[p.raan_deg for p in scenario.design.planes],
        phase_deg=[p.phase_deg for p in scenario.design.planes],
        launch_stage=scenario.design.launch_stage,
        worst_availability=min(availability.values()) if availability else 0.0,
        worst_max_gap_s=max((longest_gap_steps(r) for r in series.values()), default=0) * step_s,
        availability=availability,
    )


def variant(
    scenario: Scenario,
    raan_deg: list[float] | None = None,
    phase_deg: list[float] | None = None,
    launch_stage: int | None = None,
) -> Scenario:
    """A copy of `scenario` with the plane angles and stage replaced. Angles wrap into [0, 360)."""

    changed = scenario.model_copy(deep=True)
    for index, plane in enumerate(changed.design.planes):
        if raan_deg is not None:
            plane.raan_deg = float(raan_deg[index]) % 360.0
        if phase_deg is not None:
            plane.phase_deg = float(phase_deg[index]) % 360.0
    if launch_stage is not None:
        changed.design.launch_stage = launch_stage
    return changed


def sweep_spacing(
    scenario: Scenario,
    raan_spacings: list[float] | None = None,
    phase_spacings: list[float] | None = None,
    workers: int | None = None,
) -> SweepReport:
    """
    Walk the evenly spread family: plane k sits at `k × raan_spacing`, phased by the same rule.

    The default RAAN grid stops at 120° because beyond that the family repeats for
    three planes — 130° spacing places the same three orbits as 110° does, in a
    different order. The phase grid is a quarter of the in-plane spacing, which for
    16 satellites per plane is 22.5°/4.
    """

    planes = len(scenario.design.planes)
    per_plane = _in_plane_spacing(scenario)

    if raan_spacings is None:
        raan_spacings = [float(x) for x in range(0, 121, 5)]
    if phase_spacings is None:
        phase_spacings = [round(per_plane * k / 4, 4) for k in range(4)]

    base_raan = scenario.design.planes[0].raan_deg
    base_phase = scenario.design.planes[0].phase_deg

    designs = [
        variant(
            scenario,
            raan_deg=[base_raan + k * raan for k in range(planes)],
            phase_deg=[base_phase + k * phase for k in range(planes)],
        )
        for raan in raan_spacings
        for phase in phase_spacings
    ]

    # The scenario as it stands is always a candidate, whether or not the grid
    # happens to land on it — the supplied design phases its planes by 7.5° and the
    # default grid steps in quarters of 22.5°, which misses it. Without this, "best"
    # could be worse than changing nothing and the interface would still report it
    # as an improvement.
    baseline = evaluate(scenario)
    return SweepReport(baseline=baseline, candidates=[baseline] + _score_all(designs, workers))


def refine(
    scenario: Scenario,
    raan_step_deg: float = 10.0,
    rounds: int = 2,
    workers: int | None = None,
) -> SweepReport:
    """
    Coordinate refinement: move one plane at a time, keep a move only if it helps.

    Started from whatever the scenario already is, so it improves a design the user
    arrived at rather than replacing it. Planes are visited in order and the first
    one is held still — rotating every plane together turns the whole constellation
    and changes nothing about its internal geometry, only when in the day each
    ground site passes under it.
    """

    baseline = evaluate(scenario)
    current = scenario
    best = baseline
    tried: list[Candidate] = [baseline]

    offsets = [x for x in np.arange(-180, 180, raan_step_deg) if x]
    for _ in range(rounds):
        for index in range(1, len(scenario.design.planes)):
            designs = []
            for offset in offsets:
                raan = [p.raan_deg for p in current.design.planes]
                raan[index] = raan[index] + float(offset)
                designs.append(variant(current, raan_deg=raan))

            scored = _score_all(designs, workers)
            tried.extend(scored)
            winner = max(scored, key=lambda c: (c.worst_availability, -c.worst_max_gap_s))
            if (winner.worst_availability, -winner.worst_max_gap_s) > (
                best.worst_availability,
                -best.worst_max_gap_s,
            ):
                best = winner
                current = variant(current, raan_deg=winner.raan_deg)

    return SweepReport(baseline=baseline, candidates=tried)


def _score_all(designs: list[Scenario], workers: int | None) -> list[Candidate]:
    """Evaluate a batch, in parallel when there is enough of it to be worth the processes."""

    if workers is not None and workers > 1 and len(designs) > 8:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(evaluate, designs))
    return [evaluate(design) for design in designs]


def _in_plane_spacing(scenario: Scenario) -> float:
    """
    The angle between neighbouring satellites in the busiest plane.

    Read from the design rather than assumed, so a judge's scenario with a different
    number of craft per plane gets a phase grid that means something for it.
    """

    counts: dict[str, int] = {}
    for satellite in scenario.design.satellites:
        counts[satellite.plane_id] = counts.get(satellite.plane_id, 0) + 1
    largest = max(counts.values(), default=1)
    return 360.0 / largest if largest else 360.0
