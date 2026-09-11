"""
Какие связи существуют в каждый отсчёт: между аппаратами и между аппаратом и землёй.

Два правила, оба из описания кейса. Наземная связь требует, чтобы аппарат был не
ниже порогового угла места. Межспутниковая связь требует, чтобы аппараты были ближе
предельной дальности **и** чтобы отрезок между ними не пересекал Землю: попасть в
дальность мало, если планета стоит на пути — а именно это и происходит с
аппаратами по разные стороны полярной орбиты.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.config import EARTH_RADIUS_KM
from cosmo_net.geometry.orbit import Trajectory, ground_positions_km
from cosmo_net.scenario.schema import Scenario


@dataclass(frozen=True)
class ContactSeries:
    """Все связи сети, на каждом отсчёте сетки."""

    times_s: np.ndarray
    satellite_ids: list[str]
    ground_ids: list[str]
    ground_roles: list[str]

    active: np.ndarray
    """(T, N) в строю: запущен на выбранной очереди и не находится в отказе."""

    pair_index: np.ndarray
    """(P, 2) пары аппаратов, i < j, одни и те же на весь прогон."""

    isl_geometric: np.ndarray
    """(T, P) в дальности и не сквозь Землю. О том, кто в строю, ничего не говорит."""

    isl_open: np.ndarray
    """(T, P) пригодна к работе: геометрия позволяет и оба конца в строю."""

    isl_distance_km: np.ndarray
    """(T, P) расстояние между аппаратами, независимо от того, работает связь или нет."""

    elevation_deg: np.ndarray
    """(T, G, N) угол места каждого аппарата над каждым наземным пунктом, градусы."""

    elevation_ok: np.ndarray
    """(T, G, N) не ниже порога. Тоже не зависит от того, кто в строю."""

    ground_open: np.ndarray
    """(T, G, N) наземная связь работает: аппарат достаточно высоко, в строю, пункт доступен."""

    ground_distance_km: np.ndarray
    """(T, G, N) наклонная дальность от пункта до аппарата."""

    def with_service(self, active: np.ndarray, offline: np.ndarray) -> ContactSeries:
        """
        Та же геометрия при другом составе аппаратов и станций в строю.

        Положения, расстояния и углы места зависят только от орбит. Поэтому
        исследование, которое меняет лишь состав включённых аппаратов — а анализ
        критичности делает ровно это, 48 раз подряд, — переиспользует их и
        пересчитывает только булевы маски. Геометрия занимает 45 мс из прогона,
        маски — несколько сотен микросекунд.
        """

        i, j = self.pair_index[:, 0], self.pair_index[:, 1]
        return ContactSeries(
            times_s=self.times_s,
            satellite_ids=self.satellite_ids,
            ground_ids=self.ground_ids,
            ground_roles=self.ground_roles,
            active=active,
            pair_index=self.pair_index,
            isl_geometric=self.isl_geometric,
            isl_open=self.isl_geometric & active[:, i] & active[:, j],
            isl_distance_km=self.isl_distance_km,
            elevation_deg=self.elevation_deg,
            elevation_ok=self.elevation_ok,
            ground_open=self.elevation_ok & active[:, None, :] & ~offline[:, :, None],
            ground_distance_km=self.ground_distance_km,
        )


def active_mask(scenario: Scenario, times_s: np.ndarray) -> np.ndarray:
    """
    (T, N) какие аппараты в строю на каждом отсчёте.

    Две независимые причины выбыть: аппарат ещё не запущен на выбранной очереди
    или находится в объявленном отказе. Интервалы отказа полуоткрытые —
    `[start_s, end_s)`, — поэтому аппарат, выключенный с 21 600 с, уже выключен в
    момент 21 600 и снова в строю в момент `end_s`.
    """

    design = scenario.design
    launched = np.array(
        [sat.launch_batch <= design.launch_stage for sat in design.satellites], dtype=bool
    )
    active = np.repeat(launched[None, :], len(times_s), axis=0)

    index = {sat.id: k for k, sat in enumerate(design.satellites)}
    for failure in scenario.failures:
        k = index.get(failure.satellite_id)
        if k is None:
            continue
        down = (times_s >= failure.start_s) & (times_s < failure.end_s)
        active[down, k] = False
    return active


def gateway_offline_mask(scenario: Scenario, times_s: np.ndarray) -> np.ndarray:
    """(T, G) какие наземные пункты недоступны на каждом отсчёте. Недоступным бывает только шлюз."""

    offline = np.zeros((len(times_s), len(scenario.ground_sites)), dtype=bool)
    index = {site.id: g for g, site in enumerate(scenario.ground_sites)}
    for outage in scenario.gateway_outages:
        g = index.get(outage.gateway_id)
        if g is None:
            continue
        down = (times_s >= outage.start_s) & (times_s < outage.end_s)
        offline[down, g] = True
    return offline


def compute_contacts(scenario: Scenario, trajectory: Trajectory) -> ContactSeries:
    """Собрать полный состав связей прогона из положений, которые уже посчитаны в траектории."""

    env = scenario.environment
    times = trajectory.times_s
    ecef = trajectory.ecef_km
    n_sat = ecef.shape[1]

    active = active_mask(scenario, times)
    offline = gateway_offline_mask(scenario, times)

    i, j = np.triu_indices(n_sat, k=1)
    a = ecef[:, i, :]
    b = ecef[:, j, :]
    delta = b - a
    distance = np.linalg.norm(delta, axis=-1)

    # Ближайшее расстояние от отрезка до центра Земли. Ограничение проекции
    # отрезком [0, 1] удерживает точку на самом отрезке: без него пара, у которой
    # через планету проходит бесконечная прямая, была бы отвергнута даже когда оба
    # аппарата находятся по одну сторону от Земли и прекрасно видят друг друга.
    denominator = np.maximum(np.sum(delta * delta, axis=-1), 1e-12)
    lam = np.clip(-np.sum(a * delta, axis=-1) / denominator, 0.0, 1.0)
    closest = np.linalg.norm(a + lam[..., None] * delta, axis=-1)

    isl_geometric = (distance < env.isl_range_km) & (closest > EARTH_RADIUS_KM)
    isl_open = isl_geometric & active[:, i] & active[:, j]

    ground = ground_positions_km(scenario.ground_sites)
    difference = ecef[:, None, :, :] - ground[None, :, None, :]
    slant = np.linalg.norm(difference, axis=-1)

    # Угол места над местным горизонтом: угол между направлением на аппарат и
    # плоскостью, перпендикулярной радиус-вектору самого пункта. На сферической
    # Земле этот радиус-вектор и есть местная вертикаль — поэтому запись ниже в одну
    # строку равна привычному арктангенсу «вверх к горизонтали».
    up = ground / EARTH_RADIUS_KM
    sine = np.sum(difference * up[None, :, None, :], axis=-1) / np.maximum(slant, 1e-12)
    elevation = np.degrees(np.arcsin(np.clip(sine, -1.0, 1.0)))

    elevation_ok = elevation >= env.min_elevation_deg
    ground_open = elevation_ok & active[:, None, :] & ~offline[:, :, None]

    return ContactSeries(
        times_s=times,
        satellite_ids=list(trajectory.satellite_ids),
        ground_ids=[site.id for site in scenario.ground_sites],
        ground_roles=[site.role for site in scenario.ground_sites],
        active=active,
        pair_index=np.stack((i, j), axis=1),
        isl_geometric=isl_geometric,
        isl_open=isl_open,
        isl_distance_km=distance,
        elevation_deg=elevation,
        elevation_ok=elevation_ok,
        ground_open=ground_open,
        ground_distance_km=slant,
    )
