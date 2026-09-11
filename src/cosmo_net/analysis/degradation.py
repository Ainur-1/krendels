"""
Сколько произвольных отказов группировка переносит, не теряя цель.

Кейс задаёт десять конкретных отказов, и это один вопрос. Вопрос об устойчивости
другой: отказать может любой аппарат, а не тот, который выбрали составители. Ответ
получается перебором случайных наборов — приём для оценки надёжности группировок
обычный.

Исследование дополняет анализ критичности, а не повторяет его. Критичность выбивает
аппараты **по одному** и отвечает, есть ли среди них незаменимый. Здесь выбиваются
наборы, и отвечается, сколько потерь выдерживает проект целиком.

Измерено на полной группировке, 40 наборов на каждое число отказов: цель 90 %
удерживается при двух отказах всегда, при трёх — в 77.5 % наборов, при четырёх не
удерживается ни в одном. Падение почти строго линейно — 2.30 пункта за аппарат при
отклонении от прямой не больше 0.56 пункта, — и в решающем диапазоне до четырёх
отказов разброс между лучшим и худшим набором не превышает 2.2 пункта. Дальше он
растёт: на двенадцати отказах это уже 5.1 пункта.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cosmo_net.analysis.reachability import availability_series
from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario

# Зерно записано в коде, а не берётся из часов: числа из этого модуля попадают в
# отчёты и в защиту, и повторный запуск обязан давать те же самые.
DEFAULT_SEED = 20260912

DEFAULT_TRIALS = 40
"""Наборов на каждое число отказов. Проверено повтором с другим зерном: средние
расходятся не больше чем на 0.25 процентного пункта, а вывод о числе переносимых
отказов не меняется вовсе. Сорока наборов достаточно."""

DEFAULT_MAX_FAILURES = 12


@dataclass(frozen=True)
class DegradationPoint:
    """Что даёт потеря заданного числа произвольных аппаратов."""

    failures: int
    trials: int

    mean_worst_availability: float
    best_worst_availability: float
    worst_worst_availability: float
    """Худший пункт в самом неудачном из разыгранных наборов. Это и есть оценка снизу."""

    meets_target_share: float
    """Доля наборов, в которых цель удержана. Именно она отвечает на вопрос «выдержим ли»."""

    def to_dict(self) -> dict[str, object]:
        return {
            "failures": self.failures,
            "trials": self.trials,
            "mean_worst_availability": self.mean_worst_availability,
            "best_worst_availability": self.best_worst_availability,
            "worst_worst_availability": self.worst_worst_availability,
            "meets_target_share": self.meets_target_share,
        }

    @property
    def spread_pp(self) -> float:
        return (self.best_worst_availability - self.worst_worst_availability) * 100


@dataclass
class DegradationCurve:
    """Кривая «сколько потеряно — что осталось» и то, что из неё следует."""

    target_availability: float
    satellites_in_service: int
    seed: int
    points: list[DegradationPoint] = field(default_factory=list)

    @property
    def tolerated_failures(self) -> int:
        """
        Наибольшее число отказов, при котором цель удержана **в каждом** разыгранном наборе.

        Осторожная формулировка выбрана намеренно: проект, который держится в среднем
        и рассыпается на неудачном наборе, устойчивым не является.
        """

        tolerated = 0
        for point in sorted(self.points, key=lambda p: p.failures):
            if point.meets_target_share < 1.0:
                break
            tolerated = point.failures
        return tolerated

    @property
    def slope_pp_per_satellite(self) -> float:
        """
        Насколько пунктов падает доступность за каждый потерянный аппарат.

        Метод наименьших квадратов по средним значениям. Число имеет смысл ровно
        постольку, поскольку кривая близка к прямой, — а близость проверяется
        отдельно, через `linear_fit_error_pp`.
        """

        if len(self.points) < 2:
            return 0.0
        x = np.array([p.failures for p in self.points], dtype=float)
        y = np.array([p.mean_worst_availability * 100 for p in self.points], dtype=float)
        slope, _ = np.polyfit(x, y, 1)
        return float(-slope)

    @property
    def linear_fit_error_pp(self) -> float:
        """Наибольшее отклонение средних от прямой, в процентных пунктах."""

        if len(self.points) < 2:
            return 0.0
        x = np.array([p.failures for p in self.points], dtype=float)
        y = np.array([p.mean_worst_availability * 100 for p in self.points], dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        return float(np.abs(y - (slope * x + intercept)).max())

    def predicted_availability(self, failures: int, intact_share: float = 0.0) -> float:
        """
        Что кривая предсказывает для отказа, который занимает не все сутки.

        `intact_share` — доля горизонта, на которой группировка ещё цела. Отказы в
        сценарии 03 начинаются на шестом часу, то есть четверть суток целы, и предсказание
        получается смешиванием двух точек кривой. Это способ проверить кривую на том,
        чего она не видела: предсказано 79.8 %, измерено в сценарии 03 — 79.3 %.
        """

        by_count = {p.failures: p.mean_worst_availability for p in self.points}
        if 0 not in by_count or failures not in by_count:
            raise ValueError("кривая не содержит нужных точек")
        return intact_share * by_count[0] + (1 - intact_share) * by_count[failures]

    def to_dict(self) -> dict[str, object]:
        return {
            "target_availability": self.target_availability,
            "satellites_in_service": self.satellites_in_service,
            "seed": self.seed,
            "tolerated_failures": self.tolerated_failures,
            "slope_pp_per_satellite": self.slope_pp_per_satellite,
            "linear_fit_error_pp": self.linear_fit_error_pp,
            "points": [p.to_dict() for p in self.points],
        }


def degradation_curve(
    scenario: Scenario,
    max_failures: int = DEFAULT_MAX_FAILURES,
    trials: int = DEFAULT_TRIALS,
    seed: int = DEFAULT_SEED,
    contacts: ContactSeries | None = None,
) -> DegradationCurve:
    """
    Разыграть случайные наборы отказов и измерить, что остаётся от доступности.

    Выбивать можно только то, что в строю: аппарат, не запущенный на выбранной
    очереди или уже находящийся в отказе по сценарию, в набор не попадает, иначе
    часть разыгранных отказов ничего не меняла бы и кривая оказалась бы оптимистичнее
    правды.

    Геометрия связей строится один раз и переиспользуется через `with_service`,
    поэтому 481 прогон занимает 10.5 с, а не десять минут.
    """

    if contacts is None:
        contacts = compute_contacts(scenario, compute_trajectory(scenario))
    offline = gateway_offline_mask(scenario, np.asarray(contacts.times_s, dtype=float))

    in_service = [n for n in range(contacts.active.shape[1]) if contacts.active[:, n].any()]
    limit = min(max_failures, len(in_service))
    target = scenario.environment.target_availability

    rng = np.random.default_rng(seed)
    points: list[DegradationPoint] = []

    for failures in range(limit + 1):
        # Ноль отказов разыгрывать нечего: набор ровно один, и он пустой.
        draws = 1 if failures == 0 else trials
        values: list[float] = []
        for _ in range(draws):
            active = contacts.active.copy()
            if failures:
                for n in rng.choice(in_service, size=failures, replace=False):
                    active[:, int(n)] = False
            series = availability_series(scenario, contacts.with_service(active, offline))
            values.append(min((float(v.mean()) for v in series.values()), default=0.0))

        sample = np.array(values)
        points.append(
            DegradationPoint(
                failures=failures,
                trials=draws,
                mean_worst_availability=float(sample.mean()),
                best_worst_availability=float(sample.max()),
                worst_worst_availability=float(sample.min()),
                meets_target_share=float((sample >= target).mean()),
            )
        )

    return DegradationCurve(
        target_availability=target,
        satellites_in_service=len(in_service),
        seed=seed,
        points=points,
    )
