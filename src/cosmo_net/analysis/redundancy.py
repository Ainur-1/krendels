"""
Сколько независимых маршрутов есть прямо сейчас — мера запаса, а не наличия связи.

Доступность отвечает, есть ли путь. Инженера волнует другое: если путь один, то
отказ любого аппарата на нём рвёт связь немедленно, и доступность 96.7 % про это
ничего не говорит. Поэтому здесь считается число маршрутов, **не имеющих общих
аппаратов**: это связность по вершинам между пунктом и шлюзами, и считается она
потоком в графе с расщеплением узлов.

Расщепление и нужно именно потому, что отказывают аппараты, а не линии. Каждый
аппарат разрезается на вход и выход, между ними ставится пропускная способность 1 —
и тогда максимальный поток равен числу маршрутов, которые можно проложить, ни разу
не задев один и тот же аппарат дважды.

Исследование дополняет анализ критичности и объясняет кажущееся противоречие с ним.
Критичность показала, что аппарата, важного **весь день**, нет. Здесь видно, что почти
в каждый отдельный момент есть аппарат, важный **именно сейчас**: измерено на полной
группировке, что у C65 ровно один маршрут на 80.1 % отсчётов. Это два разных вопроса,
и ответы у них разные.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario

UNLIMITED = 1 << 30
"""Пропускная способность линии. Ограничивать надо аппараты, а не связи между ними."""


@dataclass(frozen=True)
class ClientRedundancy:
    """Запас маршрутов одного пункта за весь горизонт."""

    client_id: str

    disjoint_paths: np.ndarray
    """(T,) сколько маршрутов без общих аппаратов существует на каждом отсчёте."""

    @property
    def mean(self) -> float:
        return float(self.disjoint_paths.mean()) if self.disjoint_paths.size else 0.0

    @property
    def no_path_share(self) -> float:
        """Отсчёты без маршрута вовсе. Обязано совпасть с недоступностью."""

        return self._share(lambda k: k == 0)

    @property
    def single_path_share(self) -> float:
        """Отсчёты, на которых маршрут ровно один: связь держится на одном аппарате."""

        return self._share(lambda k: k == 1)

    @property
    def redundant_share(self) -> float:
        """Отсчёты с настоящим запасом: маршрутов два или больше."""

        return self._share(lambda k: k >= 2)

    def _share(self, predicate) -> float:
        if self.disjoint_paths.size == 0:
            return 0.0
        return float(predicate(self.disjoint_paths).mean())

    def summary(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "mean_disjoint_paths": self.mean,
            "no_path_share": self.no_path_share,
            "single_path_share": self.single_path_share,
            "redundant_share": self.redundant_share,
            "max_disjoint_paths": int(self.disjoint_paths.max())
            if self.disjoint_paths.size
            else 0,
        }


@dataclass
class RedundancyReport:
    clients: list[ClientRedundancy] = field(default_factory=list)

    @property
    def worst_single_path_share(self) -> float:
        """Наибольшая доля отсчётов с единственным маршрутом среди всех пунктов."""

        return max((c.single_path_share for c in self.clients), default=0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "worst_single_path_share": self.worst_single_path_share,
            "clients": [c.summary() for c in self.clients],
        }


def disjoint_path_counts(
    scenario: Scenario, contacts: ContactSeries | None = None
) -> list[ClientRedundancy]:
    """
    Для каждого пункта — число маршрутов без общих аппаратов на каждом отсчёте.

    Часть графа на отсчёте общая для всех пунктов: сами аппараты, связи между ними и
    точки приземления у работающих шлюзов. Она строится один раз на отсчёт, и для
    каждого пункта к ней добавляются только его подъёмы. Весь горизонт на выданных
    данных считается за секунду.
    """

    if contacts is None:
        contacts = compute_contacts(scenario, compute_trajectory(scenario))

    times = np.asarray(contacts.times_s, dtype=float)
    offline = gateway_offline_mask(scenario, times)
    steps = len(times)
    count = len(contacts.satellite_ids)
    pairs = contacts.pair_index

    gateway_slots = [g for g, site in enumerate(scenario.ground_sites) if site.role == "gateway"]
    client_slots = [
        (g, site.id) for g, site in enumerate(scenario.ground_sites) if site.role == "client"
    ]

    source, sink = 2 * count, 2 * count + 1
    size = 2 * count + 2

    result = {client_id: np.zeros(steps, dtype=int) for _, client_id in client_slots}
    for step in range(steps):
        base: list[dict[int, int]] = [{} for _ in range(size)]
        for n in range(count):
            # Аппарат можно пройти ровно один раз — в этом весь смысл расщепления.
            _add(base, n, count + n, 1)
        for p in np.flatnonzero(contacts.isl_open[step]):
            a, b = int(pairs[p, 0]), int(pairs[p, 1])
            _add(base, count + a, b, UNLIMITED)
            _add(base, count + b, a, UNLIMITED)
        for g in gateway_slots:
            if offline[step, g]:
                continue
            for n in np.flatnonzero(contacts.ground_open[step, g]):
                _add(base, count + int(n), sink, UNLIMITED)

        for g, client_id in client_slots:
            visible = np.flatnonzero(contacts.ground_open[step, g])
            if visible.size == 0:
                continue
            residual = [dict(edges) for edges in base]
            for n in visible:
                _add(residual, source, int(n), UNLIMITED)
            result[client_id][step] = _max_flow(residual, source, sink)

    return [
        ClientRedundancy(client_id=client_id, disjoint_paths=result[client_id])
        for _, client_id in client_slots
    ]


def redundancy_report(
    scenario: Scenario, contacts: ContactSeries | None = None
) -> RedundancyReport:
    return RedundancyReport(clients=disjoint_path_counts(scenario, contacts))


def _add(graph: list[dict[int, int]], a: int, b: int, capacity: int) -> None:
    """Дуга и её обратная сторона. Обратная нужна потоку, чтобы отменять свои решения."""

    graph[a][b] = graph[a].get(b, 0) + capacity
    graph[b].setdefault(a, 0)


def _max_flow(residual: list[dict[int, int]], source: int, sink: int) -> int:
    """
    Максимальный поток поиском в ширину по кратчайшим дополняющим путям.

    Взят самый простой из корректных способов, и намеренно: поток здесь никогда не
    превышает числа аппаратов, видимых пункту, то есть единиц, поэтому итераций
    заведомо мало, а читаемость важнее скорости.
    """

    flow = 0
    while True:
        previous = {source: source}
        queue = deque([source])
        while queue and sink not in previous:
            node = queue.popleft()
            for neighbour, capacity in residual[node].items():
                if capacity > 0 and neighbour not in previous:
                    previous[neighbour] = node
                    queue.append(neighbour)
        if sink not in previous:
            return flow

        bottleneck = UNLIMITED
        node = sink
        while node != source:
            bottleneck = min(bottleneck, residual[previous[node]][node])
            node = previous[node]

        node = sink
        while node != source:
            parent = previous[node]
            residual[parent][node] -= bottleneck
            residual[node][parent] += bottleneck
            node = parent
        flow += bottleneck
