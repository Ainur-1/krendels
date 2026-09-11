"""
Где находится каждый аппарат в каждый отсчёт сетки — одним массивом.

Горизонт считается целиком, а не по шагу за раз. Базовый прогон — это 720
отсчётов по 48 аппаратов: циклом получилось бы 34 560 вычислений одних и тех же
шести тригонометрических функций, массивом — шесть вызовов. Подбор конфигурации
запускает сотню таких прогонов по одному нажатию, и ради этого разница стоит того,
а не ради аккуратности как таковой.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from cosmo_net.config import (
    EARTH_ANGULAR_RATE_RAD_S,
    EARTH_RADIUS_KM,
    MU_KM3_S2,
)
from cosmo_net.scenario.schema import GroundSite, Scenario


@dataclass(frozen=True)
class Trajectory:
    """Положения аппаратов на сетке расчёта, в обеих системах координат, нужных модели."""

    times_s: np.ndarray
    """(T,) сетка отсчётов, секунды от начала расчёта."""

    satellite_ids: list[str]
    """(N,) идентификаторы аппаратов в том порядке, в каком их объявляет сценарий."""

    eci_km: np.ndarray
    """(T, N, 3) инерциальная система. Орбиты здесь — окружности, вращается Земля."""

    ecef_km: np.ndarray
    """(T, N, 3) система, связанная с Землёй. Наземные пункты в ней неподвижны, поэтому
    связи считаются именно здесь."""

    @property
    def orbital_period_s(self) -> float:
        """Период обращения. На высоте 550 км это 5730 с, то есть 15.08 витка за сутки."""

        r = float(np.linalg.norm(self.eci_km[0, 0]))
        return 2 * math.pi * math.sqrt(r**3 / MU_KM3_S2)


def orbit_radius_km(altitude_km: float) -> float:
    """Радиус орбиты: радиус Земли плюс высота."""

    return EARTH_RADIUS_KM + altitude_km


def mean_motion_rad_s(altitude_km: float) -> float:
    """Угловая скорость движения по круговой орбите, √(μ/r³)."""

    return math.sqrt(MU_KM3_S2 / orbit_radius_km(altitude_km) ** 3)


def compute_trajectory(scenario: Scenario, times_s: np.ndarray | None = None) -> Trajectory:
    """
    Положения всех аппаратов на сетке — независимо от того, в строю аппарат или нет.

    У аппарата, который ещё не запущен или находится в отказе, положение всё равно
    есть: кейс прямо говорит, что отказавший аппарат сохраняет расчётное положение и
    выбывает только из состава связей. Кто из них участвует в связях, решается в
    модуле contacts, а не здесь.
    """

    env = scenario.environment
    grid = scenario.times if times_s is None else times_s
    t = np.asarray(grid, dtype=float)

    planes = {p.id: p for p in scenario.design.planes}
    r = orbit_radius_km(env.altitude_km)
    n = mean_motion_rad_s(env.altitude_km)
    cos_i = math.cos(math.radians(env.inclination_deg))
    sin_i = math.sin(math.radians(env.inclination_deg))

    # Аргумент широты в нулевой момент, свой у каждого аппарата: его собственное
    # место в плоскости плюс общий сдвиг всей плоскости. Фазирование двигает
    # аппараты вдоль орбиты, RAAN ниже поворачивает саму орбиту. Это и есть два
    # рычага, которые интерфейс отдаёт пользователю.
    u0 = np.array(
        [
            math.radians(sat.slot_deg + planes[sat.plane_id].phase_deg)
            for sat in scenario.design.satellites
        ]
    )
    raan = np.array(
        [math.radians(planes[sat.plane_id].raan_deg) for sat in scenario.design.satellites]
    )

    u = u0[None, :] + n * t[:, None]
    cu, su = np.cos(u), np.sin(u)
    co, so = np.cos(raan)[None, :], np.sin(raan)[None, :]

    eci = r * np.stack(
        (co * cu - so * su * cos_i, so * cu + co * su * cos_i, su * sin_i),
        axis=-1,
    )

    # С начала расчёта Земля повернулась на угол θ, поэтому одна и та же точка
    # инерциального пространства оказывается над другой долготой. Повернуть на −θ
    # аппараты дешевле, чем всё остальное, и наземные пункты остаются неподвижными.
    theta = math.radians(env.earth_angle0_deg) + EARTH_ANGULAR_RATE_RAD_S * t
    ct, st = np.cos(theta)[:, None], np.sin(theta)[:, None]
    ecef = np.stack(
        (
            ct * eci[..., 0] + st * eci[..., 1],
            -st * eci[..., 0] + ct * eci[..., 1],
            eci[..., 2],
        ),
        axis=-1,
    )

    return Trajectory(
        times_s=t,
        satellite_ids=[sat.id for sat in scenario.design.satellites],
        eci_km=eci,
        ecef_km=ecef,
    )


def ground_position_km(site: GroundSite) -> np.ndarray:
    """Наземный пункт в системе, связанной с Землёй. Земля сферическая — широта геоцентрическая."""

    lat, lon = math.radians(site.lat_deg), math.radians(site.lon_deg)
    return EARTH_RADIUS_KM * np.array(
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)]
    )


def ground_positions_km(sites: list[GroundSite]) -> np.ndarray:
    """(G, 3) для списка пунктов, в том же порядке."""

    if not sites:
        return np.zeros((0, 3))
    return np.stack([ground_position_km(s) for s in sites])
