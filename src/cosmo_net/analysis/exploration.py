"""
Разведочный анализ: что лежит во входных данных, до того как мы что-либо посчитали.

Сначала данные, потом результаты. Здесь нет ни одного числа, полученного из наших
расчётов доступности: всё считается из выданных файлов и из геометрии, которую они
задают. Смысл в том, чтобы проверить предположения, на которые эти файлы наводят, и
найти те из них, которые неверны, — потому что неверное предположение о входе портит
любой вывод на выходе.

Модуль только считает. Рисует `scripts/make_figures.py`, и это разделение не
косметическое: числа отсюда попадают и в графики, и в тесты, и расходиться они не
должны.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from cosmo_net.config import EARTH_RADIUS_KM
from cosmo_net.geometry.contacts import compute_contacts
from cosmo_net.geometry.orbit import compute_trajectory, orbit_radius_km
from cosmo_net.scenario.schema import GroundSite, Scenario


@dataclass(frozen=True)
class PlaneComposition:
    """Как очереди запуска разложены по орбитальным плоскостям."""

    plane_id: str
    satellites: int
    batches: list[int]
    """Номера очередей, встречающихся в этой плоскости."""

    @property
    def single_batch(self) -> bool:
        return len(self.batches) == 1


@dataclass(frozen=True)
class RingGeometry:
    """Замыкается ли кольцо связей внутри плоскости при заданной дальности."""

    satellites_per_plane: int
    spacing_deg: float
    chord_km: float
    """Расстояние по прямой между соседями в плоскости."""

    isl_range_km: float
    margin_km: float
    satellites_needed: int
    """Сколько аппаратов на плоскость нужно, чтобы соседи попали в дальность."""

    @property
    def closes(self) -> bool:
        return self.chord_km < self.isl_range_km


@dataclass(frozen=True)
class SiteGeometry:
    """Насколько далеко наземный пункт от шлюза в единицах того, что перекрывает один аппарат."""

    client_id: str
    gateway_id: str
    distance_km: float
    footprint_km: float
    """Радиус зоны, из которой аппарат виден выше порогового угла места."""

    @property
    def bridging_ratio(self) -> float:
        """
        Доля от предела, на котором один аппарат ещё виден обоим концам сразу.

        Предел — это два радиуса зоны видимости: аппарат ровно посередине. Значение
        около единицы означает, что одиночный аппарат перекрывает пару лишь в
        исключительном положении, и связь почти всегда требует транзита через сеть.
        """

        return self.distance_km / (2 * self.footprint_km)


@dataclass
class Exploration:
    """Всё, что разведка нашла в одном сценарии."""

    scenario_id: str
    steps: int
    step_s: int

    planes: list[PlaneComposition] = field(default_factory=list)
    ring: RingGeometry | None = None
    sites: list[SiteGeometry] = field(default_factory=list)
    visible_per_site: dict[str, np.ndarray] = field(default_factory=dict)
    failures_per_plane: dict[str, int] = field(default_factory=dict)
    satellites_per_plane: dict[str, int] = field(default_factory=dict)

    @property
    def batches_match_planes(self) -> bool:
        """
        Совпадает ли очередь запуска с плоскостью один в один.

        В выданных файлах совпадает, и это не мелочь: значит первая очередь — это одна
        плоскость, а не треть группировки, и разносить между плоскостями на ней нечего.
        Свойством формата это не является, поэтому и проверяется, а не предполагается.
        """

        seen = [p.batches[0] for p in self.planes if p.single_batch]
        return len(seen) == len(self.planes) and len(set(seen)) == len(self.planes)


def footprint_radius_km(altitude_km: float, min_elevation_deg: float) -> float:
    """
    Радиус круга на земле, из которого аппарат виден не ниже порогового угла места.

    Центральный угол λ считается из треугольника «центр Земли — пункт — аппарат»:
    λ = arccos(R·cos(el) / r) − el. При высоте 550 км и пороге 10° это 15.0° дуги,
    то есть около 1666 км по поверхности.
    """

    r = orbit_radius_km(altitude_km)
    el = math.radians(min_elevation_deg)
    central = math.acos(EARTH_RADIUS_KM * math.cos(el) / r) - el
    return EARTH_RADIUS_KM * central


def great_circle_km(a: GroundSite, b: GroundSite) -> float:
    """Расстояние по поверхности сферической Земли между двумя пунктами."""

    lat1, lon1 = math.radians(a.lat_deg), math.radians(a.lon_deg)
    lat2, lon2 = math.radians(b.lat_deg), math.radians(b.lon_deg)
    cosine = math.sin(lat1) * math.sin(lat2) + math.cos(lat1) * math.cos(lat2) * math.cos(
        lon1 - lon2
    )
    return EARTH_RADIUS_KM * math.acos(max(-1.0, min(1.0, cosine)))


def ring_geometry(scenario: Scenario) -> RingGeometry:
    """
    Дотягиваются ли соседи по плоскости друг до друга.

    Аппараты в плоскости стоят на круговой орбите равномерно, поэтому расстояние до
    соседа — это хорда: 2·r·sin(π/n). При 16 аппаратах и высоте 550 км это 2700 км.
    Предел 3000 км оставляет всего 11 % запаса, а предел 2000 км кольцо не замыкает
    вовсе — и тогда от внутриплоскостных связей не остаётся ни одной.
    """

    counts = Counter(sat.plane_id for sat in scenario.design.satellites)
    n = max(counts.values(), default=1)
    r = orbit_radius_km(scenario.environment.altitude_km)
    chord = 2 * r * math.sin(math.pi / n) if n > 1 else 0.0
    limit = scenario.environment.isl_range_km

    # Сколько аппаратов понадобилось бы, чтобы хорда влезла в предел дальности.
    ratio = limit / (2 * r)
    needed = math.ceil(math.pi / math.asin(ratio)) if 0 < ratio < 1 else n

    return RingGeometry(
        satellites_per_plane=n,
        spacing_deg=360.0 / n if n else 0.0,
        chord_km=chord,
        isl_range_km=limit,
        margin_km=limit - chord,
        satellites_needed=needed,
    )


def site_geometry(scenario: Scenario) -> list[SiteGeometry]:
    """Для каждого клиента — расстояние до ближайшего шлюза в долях зоны видимости."""

    footprint = footprint_radius_km(
        scenario.environment.altitude_km, scenario.environment.min_elevation_deg
    )
    result = []
    for client in scenario.clients:
        nearest = min(
            scenario.gateways, key=lambda g: great_circle_km(client, g), default=None
        )
        if nearest is None:
            continue
        result.append(
            SiteGeometry(
                client_id=client.id,
                gateway_id=nearest.id,
                distance_km=great_circle_km(client, nearest),
                footprint_km=footprint,
            )
        )
    return result


def visible_counts(scenario: Scenario) -> dict[str, np.ndarray]:
    """(T,) сколько работающих аппаратов видно над каждым наземным пунктом на каждом отсчёте."""

    contacts = compute_contacts(scenario, compute_trajectory(scenario))
    return {
        site_id: contacts.ground_open[:, g, :].sum(axis=1)
        for g, site_id in enumerate(contacts.ground_ids)
    }


def contact_durations_s(scenario: Scenario, sample_s: float = 10.0) -> dict[str, np.ndarray]:
    """
    Длительности всех сеансов видимости, измеренные на мелкой сетке.

    Мелкая сетка нужна именно здесь: чтобы узнать, не короче ли часть сеансов, чем шаг
    расчёта, измерять их шагом расчёта бессмысленно. На выданных данных 4–10 % сеансов
    короче 120 с, то есть предписанная сетка их не видит. На ответ это почти не влияет
    — покрытие перекрывается, — но знать об этом стоит.
    """

    times = np.arange(0, scenario.environment.horizon_s, sample_s)
    contacts = compute_contacts(scenario, compute_trajectory(scenario, times))

    result: dict[str, np.ndarray] = {}
    for g, site_id in enumerate(contacts.ground_ids):
        spans: list[float] = []
        for k in range(contacts.ground_open.shape[2]):
            visible = contacts.ground_open[:, g, k].astype(np.int8)
            edges = np.diff(np.concatenate(([0], visible, [0])))
            starts = np.flatnonzero(edges == 1)
            ends = np.flatnonzero(edges == -1)
            spans.extend(((ends - starts) * sample_s).tolist())
        result[site_id] = np.array(spans)
    return result


def failures_per_plane(scenario: Scenario) -> dict[str, int]:
    """Сколько аппаратов каждой плоскости выведено из строя объявленными отказами."""

    plane_of = {sat.id: sat.plane_id for sat in scenario.design.satellites}
    counts = Counter(
        plane_of[f.satellite_id] for f in scenario.failures if f.satellite_id in plane_of
    )
    return {plane.id: counts.get(plane.id, 0) for plane in scenario.design.planes}


def explore(scenario: Scenario) -> Exploration:
    """Собрать все наблюдения по одному сценарию."""

    counts = Counter(sat.plane_id for sat in scenario.design.satellites)
    batches: dict[str, set[int]] = {plane.id: set() for plane in scenario.design.planes}
    for satellite in scenario.design.satellites:
        batches[satellite.plane_id].add(satellite.launch_batch)

    return Exploration(
        scenario_id=scenario.meta.id,
        steps=len(scenario.times),
        step_s=scenario.environment.step_s,
        planes=[
            PlaneComposition(
                plane_id=plane.id,
                satellites=counts.get(plane.id, 0),
                batches=sorted(batches[plane.id]),
            )
            for plane in scenario.design.planes
        ],
        ring=ring_geometry(scenario),
        sites=site_geometry(scenario),
        visible_per_site=visible_counts(scenario),
        failures_per_plane=failures_per_plane(scenario),
        satellites_per_plane=dict(counts),
    )
