"""
Где стоило бы поставить вторую точку приземления трафика: перебор по сетке.

Анализ уже показал, что наземный сегмент — самый сильный рычаг, и назвал Норильск.
Но Норильск был выбран соображением: он примерно под средним клиентом. Соображение
— не обоснование, поэтому здесь оно проверяется перебором: кандидат ставится в
каждый узел сетки по широте и долготе, и для каждого измеряется, что получит худший
пункт.

Выданные данные при этом не меняются. Каждая точка сетки — это отдельный прогон
на копии сценария, и результат подаётся как измеренное наблюдение о том, чего стоит
второй шлюз, а не как предложение переписать вход.

Измерено на сетке из 192 точек, и главное здесь — не выигрыш, а то, что форма
поверхности зависит от состояния межспутниковой сети.

На сценарии 01 сеть цела, и второй шлюз даёт 97.8 % **из любой точки сетки**, хоть
посреди Тихого океана: трафику всё равно, где приземляться, если сеть довезёт его
куда угодно. На сценарии 04, где внутриплоскостные связи распались, место решает
всё — 94.4 % на 70° с. ш. против 65.8 % на 50°. И там же видно, что выбранный
соображением Норильск даёт 95.8 %, то есть лучше любого узла сетки: перебор ничего
не улучшил, а подтвердил.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import partial

import numpy as np

from cosmo_net.analysis.reachability import worst_availability
from cosmo_net.analysis.resources import usable_workers
from cosmo_net.scenario.schema import GroundSite, Scenario

CANDIDATE_ID = "G_CAND"
"""Идентификатор пробного шлюза. Свой, чтобы он заведомо не совпал с выданным."""

DEFAULT_LAT_RANGE = (50.0, 85.0)
DEFAULT_LAT_STEP = 5.0
DEFAULT_LON_STEP = 15.0


@dataclass(frozen=True)
class PlacementPoint:
    """Одна точка сетки и то, что она даёт худшему пункту."""

    lat_deg: float
    lon_deg: float
    worst_availability: float
    gain_pp: float
    """Прирост к базовой линии в процентных пунктах."""

    def to_dict(self) -> dict[str, object]:
        return {
            "lat_deg": self.lat_deg,
            "lon_deg": self.lon_deg,
            "worst_availability": self.worst_availability,
            "gain_pp": self.gain_pp,
        }


@dataclass
class PlacementReport:
    """Вся поверхность целиком и то, что из неё читается."""

    baseline_worst_availability: float
    lat_deg: list[float] = field(default_factory=list)
    lon_deg: list[float] = field(default_factory=list)
    points: list[PlacementPoint] = field(default_factory=list)

    @property
    def best(self) -> PlacementPoint | None:
        return max(self.points, key=lambda p: p.worst_availability, default=None)

    @property
    def grid(self) -> np.ndarray:
        """(широты, долготы) значения для карты. Порядок точек — по строкам."""

        return np.array([p.worst_availability for p in self.points]).reshape(
            len(self.lat_deg), len(self.lon_deg)
        )

    def best_per_latitude(self) -> list[tuple[float, float]]:
        """
        Лучшее, чего можно добиться на каждой широте.

        Так видно главное свойство поверхности: у высоких широт выбор долготы почти
        ничего не решает, а у низких не решает ничего вовсе.
        """

        table = self.grid
        return [(lat, float(row.max())) for lat, row in zip(self.lat_deg, table, strict=True)]

    def to_dict(self) -> dict[str, object]:
        best = self.best
        return {
            "baseline_worst_availability": self.baseline_worst_availability,
            "lat_deg": self.lat_deg,
            "lon_deg": self.lon_deg,
            "best": best.to_dict() if best else None,
            "points": [p.to_dict() for p in self.points],
        }


def with_gateway_at(scenario: Scenario, lat_deg: float, lon_deg: float) -> Scenario:
    """Копия сценария с добавленной второй точкой приземления. Оригинал не трогается."""

    changed = scenario.model_copy(deep=True)
    changed.ground_sites.append(
        GroundSite(
            id=CANDIDATE_ID,
            name=f"Кандидат {lat_deg:.1f}/{lon_deg:.1f}",
            role="gateway",
            lat_deg=float(lat_deg),
            lon_deg=float(lon_deg),
        )
    )
    return changed


def evaluate_site(scenario: Scenario, position: tuple[float, float]) -> float:
    """Доступность худшего пункта, если поставить второй шлюз в этой точке."""

    lat_deg, lon_deg = position
    return worst_availability(with_gateway_at(scenario, lat_deg, lon_deg))


def placement_grid(
    scenario: Scenario,
    lat_range: tuple[float, float] = DEFAULT_LAT_RANGE,
    lat_step: float = DEFAULT_LAT_STEP,
    lon_step: float = DEFAULT_LON_STEP,
    workers: int | None = None,
) -> PlacementReport:
    """
    Перебрать сетку кандидатов и вернуть поверхность целиком.

    Сетка по умолчанию — широты 50–85° через 5°, долготы через 15°, то есть 192
    прогона. Это 13 с на одном ядре и около трёх на восьми. Ограничение числа
    процессов берётся из `usable_workers`: на сервере с 512 МБ восемь процессов с
    собственными копиями прогона не помещаются.

    Долгота идёт от −180 до 180 без последней точки: 180° и −180° — это один и тот же
    меридиан, и считать его дважды значило бы получить лишний столбец на карте.
    """

    low, high = lat_range
    lats = [float(x) for x in np.arange(low, high + lat_step / 2, lat_step)]
    lons = [float(x) for x in np.arange(-180.0, 180.0, lon_step)]
    positions = [(lat, lon) for lat in lats for lon in lons]

    baseline = worst_availability(scenario)

    parallel = usable_workers(workers)
    if parallel > 1 and len(positions) > 8:
        with ProcessPoolExecutor(max_workers=parallel) as pool:
            values = list(pool.map(partial(evaluate_site, scenario), positions))
    else:
        values = [evaluate_site(scenario, position) for position in positions]

    points = [
        PlacementPoint(
            lat_deg=lat,
            lon_deg=lon,
            worst_availability=value,
            gain_pp=(value - baseline) * 100,
        )
        for (lat, lon), value in zip(positions, values, strict=True)
    ]

    return PlacementReport(
        baseline_worst_availability=baseline, lat_deg=lats, lon_deg=lons, points=points
    )
