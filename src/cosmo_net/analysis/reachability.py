"""
Есть ли маршрут — без того, чтобы его искать.

Доступность — это вопрос о связности: путь от клиента до шлюза существует ровно
тогда, когда какой-нибудь аппарат, видимый клиенту, лежит в той же связной
компоненте, что и какой-нибудь аппарат, видимый шлюзу. Такой ответ стоит одного
прохода системы непересекающихся множеств на отсчёт вместо отдельного поиска для
каждого клиента, и он совпадает с полным расчётом — что тесты проверяют на полном
прогоне, а не принимают на веру.

Модуль нужен потому, что два исследования вызывают его массово: анализ критичности
прогоняет горизонт по разу на каждый аппарат, а подбор конфигурации — по разу на
каждого кандидата. Измерено на сценарии 01: полный прогон с маршрутизацией — 124 мс,
этот путь — 68 мс. Остаток приходится на `compute_contacts`, общий для обоих, —
поэтому анализ критичности передаёт сюда состав связей, построенный один раз и
перемаскированный через `ContactSeries.with_service`, и платит 45 мс только на
первом прогоне.
"""

from __future__ import annotations

import numpy as np

from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario


def availability_series(
    scenario: Scenario, contacts: ContactSeries | None = None, stride: int = 1
) -> dict[str, np.ndarray]:
    """
    (T,) по флагу на отсчёт для каждого клиента: был ли путь до доступного шлюза.

    Передача готовых `contacts` позволяет вызывающей стороне пропустить дорогую
    половину работы — анализ критичности между прогонами меняет только маску
    состава в строю.

    `stride` берёт каждый n-й отсчёт вместо всех. Так работает подбор конфигурации,
    когда ранжирует сотню кандидатов между собой. Результат при этом становится
    оценкой, а не измерением, поэтому ничего посчитанного таким способом нельзя
    показывать как число: подбор перемеряет короткий список на полной сетке, прежде
    чем что-то вывести.
    """

    if stride > 1 and contacts is not None:
        raise ValueError("a subsampled series cannot reuse a full-grid contact series")

    if contacts is None:
        sampled = np.asarray(scenario.times[::stride], dtype=float)
        contacts = compute_contacts(scenario, compute_trajectory(scenario, sampled))

    times = np.asarray(contacts.times_s, dtype=float)
    offline = gateway_offline_mask(scenario, times)

    client_slots = [
        g for g, site in enumerate(scenario.ground_sites) if site.role == "client"
    ]
    gateway_slots = [
        g for g, site in enumerate(scenario.ground_sites) if site.role == "gateway"
    ]
    client_ids = [scenario.ground_sites[g].id for g in client_slots]

    steps = len(times)
    result = {client_id: np.zeros(steps, dtype=bool) for client_id in client_ids}
    pairs = contacts.pair_index

    for step in range(steps):
        # Завершить путь может только работающий шлюз, поэтому станция в отказе не
        # добавляет ни одного своего видимого аппарата в множество точек приземления.
        landing: set[int] = set()
        for g in gateway_slots:
            if offline[step, g]:
                continue
            landing.update(np.flatnonzero(contacts.ground_open[step, g]).tolist())
        if not landing:
            continue

        labels = connected_components(contacts.isl_open[step], pairs, len(contacts.satellite_ids))
        landing_roots = {int(labels[n]) for n in landing}
        for client_id, g in zip(client_ids, client_slots, strict=True):
            visible = np.flatnonzero(contacts.ground_open[step, g])
            if visible.size and any(int(labels[n]) in landing_roots for n in visible):
                result[client_id][step] = True

    return result


def connected_components(
    isl_open_step: np.ndarray, pair_index: np.ndarray, count: int
) -> np.ndarray:
    """
    (N,) метка компоненты связности для каждого аппарата на одном отсчёте.

    Вынесено отдельно, потому что вопросов, которые сводятся к связности сети на
    срезе времени, оказалось два. Достижимость спрашивает, лежат ли видимый клиенту
    и видимый шлюзу аппараты в одной компоненте. Доставка с допустимой задержкой
    спрашивает, какой лучший результат доступен всей компоненте сразу. Считаются они
    одним и тем же объединением множеств, и держать его в одном месте дешевле, чем
    следить за тем, чтобы две копии не разошлись.

    Метка — это корень, выбранный объединением, а не порядковый номер: сравнивать её
    можно только на равенство.
    """

    parent = list(range(count))
    for p in np.flatnonzero(isl_open_step):
        _union(parent, int(pair_index[p, 0]), int(pair_index[p, 1]))
    return np.array([_find(parent, n) for n in range(count)], dtype=np.int64)


def worst_availability(scenario: Scenario, contacts: ContactSeries | None = None) -> float:
    """
    Доля достижимых отсчётов у клиента, обслуженного хуже всех.

    Целевой ориентир задан на каждый терминал, поэтому судить о конфигурации надо
    именно по этому числу, а не по среднему по пунктам.
    """

    series = availability_series(scenario, contacts)
    if not series:
        return 0.0
    return min(float(reachable.mean()) for reachable in series.values())


def longest_gap_steps(reachable: np.ndarray) -> int:
    """Самая длинная череда идущих подряд недостижимых отсчётов."""

    longest = current = 0
    for value in reachable:
        current = 0 if value else current + 1
        longest = max(longest, current)
    return longest


def _find(parent: list[int], node: int) -> int:
    root = node
    while parent[root] != root:
        root = parent[root]
    while parent[node] != root:
        parent[node], node = root, parent[node]
    return root


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra
