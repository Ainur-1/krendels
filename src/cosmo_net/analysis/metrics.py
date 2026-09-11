"""
Turning a per-step record of "was there a route" into the figures a design decision rests on.

Every share is a count of steps divided by the number of steps, and every duration
is a count of steps multiplied by the step length, exactly as the case defines
them. The one judgement call is about gaps that touch the ends of the horizon: the
case asks for those to be reported separately, because a run that starts mid-outage
tells you where the grid was cut, not how long the network was down.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from cosmo_net.routing.diagnose import Outage


@dataclass(frozen=True)
class Gap:
    """A stretch of consecutive steps with no route, over `[start_s, end_s)`."""

    start_s: int
    end_s: int
    duration_s: int
    steps: int

    cause: Outage
    """The reason that held for most of the gap. A gap can change cause part way through."""

    causes: dict[str, int]
    """Step counts per reason, so a mixed gap can be shown as mixed."""

    at_horizon_edge: bool
    """Touches the first or the last step, so its true length is unknown."""


@dataclass
class ClientMetrics:
    """Everything the case asks to be reported per ground terminal."""

    client_id: str

    steps: int
    visible_steps: int
    routed_steps: int

    gaps: list[Gap] = field(default_factory=list)
    causes: dict[str, int] = field(default_factory=dict)

    hop_counts: list[int] = field(default_factory=list)
    route_lengths_km: list[float] = field(default_factory=list)

    @property
    def visibility_share(self) -> float:
        """Steps with at least one satellite in service overhead. Not the same as reachability."""

        return self.visible_steps / self.steps if self.steps else 0.0

    @property
    def availability_share(self) -> float:
        """Steps with an end-to-end path to a gateway. This is the headline figure."""

        return self.routed_steps / self.steps if self.steps else 0.0

    @property
    def max_gap_s(self) -> int:
        return max((g.duration_s for g in self.gaps), default=0)

    @property
    def max_interior_gap_s(self) -> int:
        """The longest gap that is wholly inside the horizon, so its length is real."""

        return max((g.duration_s for g in self.gaps if not g.at_horizon_edge), default=0)

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    @property
    def mean_hops(self) -> float | None:
        return sum(self.hop_counts) / len(self.hop_counts) if self.hop_counts else None

    @property
    def max_hops(self) -> int | None:
        return max(self.hop_counts) if self.hop_counts else None

    @property
    def mean_route_length_km(self) -> float | None:
        if not self.route_lengths_km:
            return None
        return sum(self.route_lengths_km) / len(self.route_lengths_km)

    def meets(self, target_availability: float) -> bool:
        return self.availability_share >= target_availability

    def summary(self, target_availability: float) -> dict[str, object]:
        """The flat form the API and the reports use."""

        return {
            "client_id": self.client_id,
            "steps": self.steps,
            "visibility_share": self.visibility_share,
            "availability_share": self.availability_share,
            "meets_target": self.meets(target_availability),
            "max_gap_s": self.max_gap_s,
            "max_interior_gap_s": self.max_interior_gap_s,
            "gap_count": self.gap_count,
            "mean_hops": self.mean_hops,
            "max_hops": self.max_hops,
            "mean_route_length_km": self.mean_route_length_km,
            "causes": self.causes,
        }


def collect_gaps(causes: list[Outage], times_s: list[int], step_s: int) -> list[Gap]:
    """
    Group consecutive routeless steps into gaps.

    `causes` is one entry per step, `Outage.NONE` where a route existed. A gap ends
    at the start of the next step rather than at the last routeless one, so a single
    missed step is `step_s` long and not zero.
    """

    gaps: list[Gap] = []
    run_start: int | None = None
    run_causes: Counter[str] = Counter()

    for index, cause in enumerate(causes):
        if cause is not Outage.NONE:
            if run_start is None:
                run_start = index
                run_causes = Counter()
            run_causes[str(cause)] += 1
            continue
        if run_start is not None:
            gaps.append(_close(run_start, index, run_causes, times_s, step_s, len(causes)))
            run_start = None

    if run_start is not None:
        gaps.append(_close(run_start, len(causes), run_causes, times_s, step_s, len(causes)))
    return gaps


def _close(
    first: int,
    stop: int,
    causes: Counter[str],
    times_s: list[int],
    step_s: int,
    total_steps: int,
) -> Gap:
    steps = stop - first
    start_s = times_s[first]
    return Gap(
        start_s=start_s,
        end_s=start_s + steps * step_s,
        duration_s=steps * step_s,
        steps=steps,
        cause=Outage(causes.most_common(1)[0][0]),
        causes=dict(causes),
        at_horizon_edge=first == 0 or stop == total_steps,
    )
