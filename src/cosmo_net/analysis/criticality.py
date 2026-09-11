"""
Which satellite would be missed most: take each one out for the whole horizon and re-measure.

The case asks the team to name the vulnerabilities it found. On the supplied full
constellation the honest answer turns out to be that there are none of this kind —
every one of the 48 costs the worst-served client between 2.2 and 2.4 percentage
points, so the design degrades gracefully and has no single satellite holding it
up. That is a result worth showing rather than an absence of one, and it is what
points the same question at the ground segment, where there *is* a single point of
failure: one gateway, blind 1.1 % of the day, and every client loses it at once.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.analysis.reachability import availability_series, longest_gap_steps
from cosmo_net.geometry.contacts import compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario


@dataclass(frozen=True)
class Knockout:
    """What removing one satellite for the whole horizon does to the worst-served client."""

    satellite_id: str
    plane_id: str

    worst_availability: float
    drop_pp: float
    """Percentage points lost against the same scenario with this craft in service."""

    worst_max_gap_s: int

    def to_dict(self) -> dict[str, object]:
        return {
            "satellite_id": self.satellite_id,
            "plane_id": self.plane_id,
            "worst_availability": self.worst_availability,
            "drop_pp": self.drop_pp,
            "worst_max_gap_s": self.worst_max_gap_s,
        }


@dataclass(frozen=True)
class CriticalityReport:
    baseline_worst_availability: float
    baseline_worst_max_gap_s: int
    knockouts: list[Knockout]

    @property
    def spread_pp(self) -> float:
        """
        Difference between the most and least costly satellite.

        A wide spread means some craft matter far more than others and the design has
        a weak point to protect. A narrow one — 0.2 pp on scenario 01 — means it does not.
        """

        if not self.knockouts:
            return 0.0
        drops = [k.drop_pp for k in self.knockouts]
        return max(drops) - min(drops)

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_worst_availability": self.baseline_worst_availability,
            "baseline_worst_max_gap_s": self.baseline_worst_max_gap_s,
            "spread_pp": self.spread_pp,
            "knockouts": [k.to_dict() for k in self.knockouts],
        }


def rank_satellites(scenario: Scenario) -> CriticalityReport:
    """
    Rank every in-service satellite by what its loss costs, most costly first.

    Craft that have not launched at the selected stage are skipped: removing
    something that is not there measures nothing. Each knockout replaces the
    satellite's own outage record rather than adding to it, so a scenario that
    already contains failures is measured against itself with one more.
    """

    times = np.asarray(scenario.times, dtype=float)
    step_s = scenario.environment.step_s

    trajectory = compute_trajectory(scenario)
    contacts = compute_contacts(scenario, trajectory)
    offline = gateway_offline_mask(scenario, times)

    baseline_series = availability_series(scenario, contacts)
    baseline_worst = _worst(baseline_series)
    baseline_gap = _worst_gap(baseline_series, step_s)

    knockouts: list[Knockout] = []
    for index, satellite in enumerate(scenario.design.satellites):
        if not contacts.active[:, index].any():
            continue

        # Everything except this craft's own availability is unchanged, so the link
        # geometry is reused and only the boolean masks are rebuilt.
        active = contacts.active.copy()
        active[:, index] = False
        series = availability_series(scenario, contacts.with_service(active, offline))

        worst = _worst(series)
        knockouts.append(
            Knockout(
                satellite_id=satellite.id,
                plane_id=satellite.plane_id,
                worst_availability=worst,
                drop_pp=(baseline_worst - worst) * 100,
                worst_max_gap_s=_worst_gap(series, step_s),
            )
        )

    knockouts.sort(key=lambda k: (-k.drop_pp, k.satellite_id))
    return CriticalityReport(
        baseline_worst_availability=baseline_worst,
        baseline_worst_max_gap_s=baseline_gap,
        knockouts=knockouts,
    )


def _worst(series: dict[str, np.ndarray]) -> float:
    if not series:
        return 0.0
    return min(float(reachable.mean()) for reachable in series.values())


def _worst_gap(series: dict[str, np.ndarray], step_s: int) -> int:
    return max((longest_gap_steps(r) for r in series.values()), default=0) * step_s
