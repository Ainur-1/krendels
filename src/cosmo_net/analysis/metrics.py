"""
Как из записи «был ли маршрут» получаются числа, на которых стоит проектное решение.

Каждая доля — это число отсчётов, делённое на общее число отсчётов, а каждая
длительность — число отсчётов, умноженное на длину шага, ровно так, как определяет
кейс. Единственное место, где приходится решать самим, — перерывы, упирающиеся в
края горизонта. Кейс просит показывать их отдельно, и не зря: расчёт, начавшийся
посреди перерыва, говорит о том, где обрезали сетку, а не о том, сколько сеть была
без связи.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from cosmo_net.routing.diagnose import Outage


@dataclass(frozen=True)
class Gap:
    """Непрерывная череда отсчётов без маршрута на промежутке `[start_s, end_s)`."""

    start_s: int
    end_s: int
    duration_s: int
    steps: int

    cause: Outage
    """Причина, продержавшаяся большую часть перерыва. По ходу перерыва причина может смениться."""

    causes: dict[str, int]
    """Сколько отсчётов пришлось на каждую причину, чтобы смешанный перерыв так и показать."""

    at_horizon_edge: bool
    """Упирается в первый или последний отсчёт, поэтому настоящая длительность неизвестна."""


@dataclass
class ClientMetrics:
    """Всё, что кейс просит показывать по каждому наземному терминалу."""

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
        """Отсчёты, на которых над пунктом есть хотя бы один работающий аппарат.
        Это не то же самое, что достижимость."""

        return self.visible_steps / self.steps if self.steps else 0.0

    @property
    def availability_share(self) -> float:
        """Отсчёты, на которых есть сквозной путь до шлюза. Это главный показатель."""

        return self.routed_steps / self.steps if self.steps else 0.0

    @property
    def max_gap_s(self) -> int:
        return max((g.duration_s for g in self.gaps), default=0)

    @property
    def max_interior_gap_s(self) -> int:
        """Самый долгий перерыв, целиком лежащий внутри горизонта, — его длительность настоящая."""

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
        """Плоское представление, которым пользуются API и отчёты."""

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
    Собрать идущие подряд отсчёты без маршрута в перерывы.

    В `causes` по одной записи на отсчёт, `Outage.NONE` там, где маршрут был.
    Перерыв заканчивается началом следующего отсчёта, а не последним отсчётом без
    маршрута, поэтому один пропущенный отсчёт длится `step_s`, а не ноль.
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
