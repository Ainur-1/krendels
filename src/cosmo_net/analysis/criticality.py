"""
Без какого аппарата хуже всего: выключаем каждый на весь горизонт и меряем заново.

Кейс просит назвать найденные уязвимости. На выданной полной группировке честный
ответ оказывается таким: уязвимостей этого рода нет. Каждый из 48 аппаратов стоит
худшему пункту от 2.2 до 2.4 процентного пункта, то есть проект деградирует плавно
и ни на одном аппарате не держится. Это результат, который стоит показать, а не
отсутствие результата, — и именно он разворачивает тот же вопрос к наземному
сегменту, где единая точка отказа как раз **есть**: один шлюз, слепой 1.1 % суток,
и теряют его сразу все клиенты.
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
    """Что даёт удаление одного аппарата на весь горизонт для худшего пункта."""

    satellite_id: str
    plane_id: str

    worst_availability: float
    drop_pp: float
    """Сколько процентных пунктов потеряно против того же сценария с этим аппаратом в строю."""

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
        Разница между самым и наименее значимым аппаратом.

        Широкий разброс означает, что одни аппараты важнее других и у проекта есть
        слабое место, которое надо защищать. Узкий — на сценарии 01 меньше пункта —
        означает, что такого места нет.
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
    Упорядочить аппараты в строю по цене их потери, самые дорогие первыми.

    Аппараты, не запущенные на выбранной очереди, пропускаются: удалять то, чего нет,
    — значит ничего не измерить. Каждое выключение заменяет собственную запись об
    отказе этого аппарата, а не добавляется к ней, поэтому сценарий, в котором отказы
    уже есть, сравнивается сам с собой плюс один.
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

        # Кроме состава в строю ничего не меняется, поэтому геометрия связей
        # переиспользуется и пересчитываются только булевы маски.
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
