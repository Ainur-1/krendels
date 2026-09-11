"""
Почему у клиента нет маршрута на этом отсчёте.

Кейс называет четыре причины и требует показывать, какая из них сработала: не виден
ни один аппарат, разорвана межспутниковая сеть, нет контакта со шлюзом или сам шлюз
недоступен. Разделение причин превращает голую цифру доступности в то, с чем инженер
может что-то сделать: одни и те же 80 % в одном сценарии означают «добавьте
аппараты», а в другом — «добавьте наземную станцию». На выданных данных встречается
и то, и другое, в разных сценариях.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum

from cosmo_net.routing.graph import SliceGraph


class Outage(StrEnum):
    """Что мешает трафику на одном отсчёте. `NONE` означает, что маршрут есть."""

    NONE = "none"

    NO_CLIENT_CONTACT = "no_client_contact"
    """Над горизонтом терминала нет ни одного работающего аппарата."""

    GATEWAY_OFFLINE = "gateway_offline"
    """Все шлюзы находятся в объявленном отказе, доставлять некуда."""

    NO_GATEWAY_CONTACT = "no_gateway_contact"
    """Шлюзы работают, но ни над одним из них нет работающего аппарата."""

    NETWORK_SPLIT = "network_split"
    """Над обоими концами есть аппараты, но цепочки связей между ними не существует."""


def diagnose(
    graph: SliceGraph,
    client_id: str,
    gateway_ids: list[str],
    offline_gateway_ids: set[str],
) -> Outage:
    """
    Определить причину для одного клиента на одном отсчёте.

    Порядок проверок — это порядок, в котором причины приходится исключать: разрыв
    сети нельзя увидеть, пока не известно, что аппараты есть над обоими концами.
    Назвать разрыв первым значило бы обвинить группировку в наземной проблеме.
    """

    if not graph.uplink.get(client_id):
        return Outage.NO_CLIENT_CONTACT

    reachable_gateways = [g for g in gateway_ids if g not in offline_gateway_ids]
    if not reachable_gateways:
        return Outage.GATEWAY_OFFLINE

    landing = {s for g in reachable_gateways for s in graph.uplink.get(g, [])}
    if not landing:
        return Outage.NO_GATEWAY_CONTACT

    seen = set(graph.uplink[client_id])
    queue = deque(seen)
    while queue:
        node = queue.popleft()
        if node in landing:
            return Outage.NONE
        for neighbour in graph.neighbours[node]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return Outage.NETWORK_SPLIT
