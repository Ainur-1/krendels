"""
Один отсчёт прогона в виде графа, по которому может пройти поиск маршрута.

Именно здесь приходится соблюдать правило кейса о том, кто имеет право
ретранслировать, и ошибиться тут легко. Функция `snapshot()` эталонного модуля
выдаёт наземное ребро для каждого наземного пункта, включая клиентские. Граф,
собранный простым переносом её списка `edges` в таблицу смежности, разрешит
трафику перепрыгнуть с одного северного терминала на другой, и сеть будет
выглядеть здоровее, чем она есть. Здесь наземный пункт — всегда только конец
пути: клиент там, где маршрут начинается, шлюз — где заканчивается, а между ними
только аппараты.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.geometry.contacts import ContactSeries


@dataclass(frozen=True)
class SliceGraph:
    """Работающие связи на одном отсчёте, разложенные для поиска."""

    step: int
    """Номер отсчёта в сетке расчёта, а не время в секундах."""

    satellite_ids: list[str]

    neighbours: list[list[int]]
    """Индекс аппарата → аппараты, до которых он дотягивается напрямую."""

    neighbour_distance_km: list[list[float]]
    """Параллельно `neighbours`."""

    neighbour_margin_km: list[list[float]]
    """Сколько дальности осталось в запасе на каждой связи. Ноль — связь вот-вот оборвётся."""

    uplink: dict[str, list[int]]
    """Идентификатор наземного пункта → аппараты, с которыми он может обмениваться трафиком."""

    uplink_distance_km: dict[str, list[float]]

    uplink_margin_deg: dict[str, list[float]]
    """Превышение угла места над порогом, градусы. Наземный аналог запаса по дальности."""


def build_slice(contacts: ContactSeries, step: int, isl_range_km: float,
                min_elevation_deg: float) -> SliceGraph:
    """Собрать граф для одного отсчёта из уже посчитанного состава связей."""

    n_sat = len(contacts.satellite_ids)
    neighbours: list[list[int]] = [[] for _ in range(n_sat)]
    distances: list[list[float]] = [[] for _ in range(n_sat)]
    margins: list[list[float]] = [[] for _ in range(n_sat)]

    open_pairs = np.where(contacts.isl_open[step])[0]
    for p in open_pairs:
        i, j = contacts.pair_index[p]
        d = float(contacts.isl_distance_km[step, p])
        margin = isl_range_km - d
        neighbours[i].append(int(j))
        distances[i].append(d)
        margins[i].append(margin)
        neighbours[j].append(int(i))
        distances[j].append(d)
        margins[j].append(margin)

    uplink: dict[str, list[int]] = {}
    uplink_distance: dict[str, list[float]] = {}
    uplink_margin: dict[str, list[float]] = {}
    for g, site_id in enumerate(contacts.ground_ids):
        visible = np.where(contacts.ground_open[step, g])[0]
        uplink[site_id] = [int(n) for n in visible]
        uplink_distance[site_id] = [float(contacts.ground_distance_km[step, g, n]) for n in visible]
        uplink_margin[site_id] = [
            float(contacts.elevation_deg[step, g, n] - min_elevation_deg) for n in visible
        ]

    return SliceGraph(
        step=step,
        satellite_ids=list(contacts.satellite_ids),
        neighbours=neighbours,
        neighbour_distance_km=distances,
        neighbour_margin_km=margins,
        uplink=uplink,
        uplink_distance_km=uplink_distance,
        uplink_margin_deg=uplink_margin,
    )
