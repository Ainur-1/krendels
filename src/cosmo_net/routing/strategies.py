"""
Три способа дойти от клиентского терминала до шлюза и то, что каждый из них улучшает.

Они существуют потому, что кейс просит объяснить выбранный подход к маршрутизации и
показать его работу, а один-единственный поиск сам по себе ничего не говорит о том,
почему выбрали именно его. Чего они **не** делают — так это не меняют доступность:
есть ли путь на данном отсчёте, определяется графом, а не поиском по нему, и все три
дают одинаковый результат с точностью до 0.1 п.п. на каждом выданном сценарии.
Различаются они выбранным маршрутом — числом ретрансляций, длиной трассы, тем,
насколько близко слабейшее звено подошло к своему пределу. Вот это про них и честно
утверждать.
"""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from cosmo_net.routing.graph import SliceGraph

# Наземный запас измеряется в градусах над порогом, запас по дальности — в
# километрах. Чтобы сравнить их на одном маршруте, оба выражаются долей от того,
# что было доступно: запас по углу места — от промежутка между порогом и зенитом,
# запас по дальности — от самого предела дальности.
_ZENITH_DEG = 90.0

# До скольких знаков после запятой сравниваются запасы при поиске широчайшего пути.
# Девять значащих цифр в доле от нуля до единицы — это заведомо тоньше, чем точность
# любого исходного числа, и заведомо грубее, чем расхождение в последнем разряде
# между машинами. Именно поэтому маршрут получается один и тот же везде.
MARGIN_RESOLUTION = 12


class Strategy(StrEnum):
    """Какой маршрут предпочесть, когда их несколько."""

    MIN_HOPS = "min_hops"
    """Минимум ретрансляций. Вариант по умолчанию: каждый переход — это ретранслятор,
    который может отказать."""

    MIN_DISTANCE = "min_distance"
    """Кратчайшая трасса в километрах, то есть наименьшая задержка распространения."""

    MAX_MARGIN = "max_margin"
    """Широчайший путь: маршрут, у которого слабейшее звено дальше всего от своего предела."""


@dataclass(frozen=True)
class Route:
    """Маршрут, существовавший на одном отсчёте, и то, по чему его имеет смысл сравнивать."""

    path: list[str]
    """Идентификатор клиента, затем идентификаторы аппаратов, затем идентификатор шлюза."""

    gateway_id: str

    hops: int
    """Число рёбер, обе наземные линии включены, поэтому самый короткий маршрут — это 2."""

    length_km: float

    min_elevation_margin_deg: float
    """Превышение угла места над порогом на худшей из двух наземных линий, градусы."""

    min_range_margin_km: float
    """Запас по дальности на самой напряжённой межспутниковой связи.
    Бесконечность, если таких связей нет."""


def find_route(
    graph: SliceGraph,
    client_id: str,
    gateway_ids: list[str],
    strategy: Strategy = Strategy.MIN_HOPS,
) -> Route | None:
    """
    Лучший маршрут от `client_id` до любого из `gateway_ids`, либо None, если маршрута нет.

    Любой шлюз — допустимая цель, и поиск считает их одним пунктом назначения:
    оператору всё равно, через какую наземную станцию уходит трафик, важно, что он
    уходит.
    """

    uplinks = graph.uplink.get(client_id, [])
    if not uplinks:
        return None

    downlink = _downlinks(graph, gateway_ids)
    if not downlink:
        return None

    if strategy is Strategy.MIN_HOPS:
        chain = _search_min_hops(graph, client_id, downlink)
    elif strategy is Strategy.MIN_DISTANCE:
        chain = _search_min_distance(graph, client_id, downlink)
    else:
        chain = _search_max_margin(graph, client_id, downlink)

    if chain is None:
        return None
    satellites, gateway_id = chain
    return _describe(graph, client_id, satellites, gateway_id)


def _downlinks(graph: SliceGraph, gateway_ids: list[str]) -> dict[int, tuple[str, float]]:
    """
    Индекс аппарата → шлюз, которому он передаёт трафик, и наклонная дальность до него.

    Аппарат, видящий сразу два доступных шлюза, выбирает ближний; при равенстве
    решает идентификатор, поэтому одна и та же сеть всегда даёт один и тот же маршрут.
    """

    best: dict[int, tuple[str, float]] = {}
    for gateway_id in sorted(gateway_ids):
        distances = graph.uplink_distance_km.get(gateway_id, [])
        for slot, satellite in enumerate(graph.uplink.get(gateway_id, [])):
            distance = distances[slot]
            current = best.get(satellite)
            if current is None or distance < current[1]:
                best[satellite] = (gateway_id, distance)
    return best


def _search_min_hops(
    graph: SliceGraph, client_id: str, downlink: dict[int, tuple[str, float]]
) -> tuple[list[int], str] | None:
    """Поиск в ширину: первый же найденный аппарат, способный передать трафик на
    землю, лежит на кратчайшем маршруте."""

    previous: dict[int, int | None] = {}
    queue: deque[int] = deque()
    for satellite in sorted(graph.uplink[client_id]):
        if satellite not in previous:
            previous[satellite] = None
            queue.append(satellite)

    while queue:
        node = queue.popleft()
        if node in downlink:
            return _unwind(previous, node), downlink[node][0]
        for neighbour in graph.neighbours[node]:
            if neighbour not in previous:
                previous[neighbour] = node
                queue.append(neighbour)
    return None


def _search_min_distance(
    graph: SliceGraph, client_id: str, downlink: dict[int, tuple[str, float]]
) -> tuple[list[int], str] | None:
    """
    Дейкстра по километрам, причём последняя наземная линия входит в стоимость поиска.

    Если не учитывать её и выбирать ближайший шлюз уже потом, получится маршрут,
    кратчайший до какого-то аппарата, а не до земли. А это не одно и то же, как только
    более длинная цепочка заканчивается ближе к станции.
    """

    uplink_distance = graph.uplink_distance_km[client_id]
    best: dict[int, float] = {}
    previous: dict[int, int | None] = {}
    heap: list[tuple[float, int, int | None]] = []
    for slot, satellite in enumerate(graph.uplink[client_id]):
        heapq.heappush(heap, (uplink_distance[slot], satellite, None))

    finished: tuple[float, list[int], str] | None = None
    while heap:
        cost, node, parent = heapq.heappop(heap)
        if node in best:
            continue
        best[node] = cost
        previous[node] = parent

        if node in downlink:
            gateway_id, downlink_km = downlink[node]
            total = cost + downlink_km
            if finished is None or total < finished[0]:
                finished = (total, _unwind(previous, node), gateway_id)

        # Всё, что ещё в очереди, стоит уже не меньше `cost`. Поэтому как только
        # лучший законченный маршрут оказывается короче самого дешёвого из
        # оставшихся аппаратов, побить его нечем и поиск останавливается.
        if finished is not None and finished[0] <= cost:
            break

        for slot, neighbour in enumerate(graph.neighbours[node]):
            if neighbour not in best:
                heapq.heappush(
                    heap, (cost + graph.neighbour_distance_km[node][slot], neighbour, node)
                )

    return (finished[1], finished[2]) if finished else None


def _search_max_margin(
    graph: SliceGraph, client_id: str, downlink: dict[int, tuple[str, float]]
) -> tuple[list[int], str] | None:
    """
    Широчайший путь: максимизировать наименьший запас, а при равном запасе брать
    маршрут покороче.

    Максимизируется доля, а не физическая величина, потому что на маршруте
    смешаны два вида запаса — градусы угла места на наземных линиях и километры
    дальности между аппаратами. Чтобы вообще говорить о слабейшем звене, их надо
    привести к сопоставимому виду.

    Второй критерий, длина, — не украшение, и появился он из измерения. Без него
    поиск брал **любой** из маршрутов с одинаковым слабейшим звеном, и «любой»
    оказывался буквально любым: на сценарии 01 находились маршруты в 22 перехода и
    50 529 км при том, что кратчайший на том же отсчёте укладывался в 10 011 км.
    Вдобавок выбор между почти равными запасами зависел от последнего разряда
    вычисленных чисел, поэтому на другой машине получался другой маршрут — это и
    поймали тесты, запущенные на Linux после macOS. Длина различает маршруты
    сотнями километров, а не долями, и ответ перестаёт зависеть от машины.

    Порядок в очереди лексикографический: сначала больший запас, при равном —
    меньшая длина. Оба свойства монотонны вдоль маршрута (запас может только падать,
    длина только расти), поэтому обычный разбор по возрастанию метки остаётся верным.

    Сравнивается при этом не сам запас, а округлённый до `MARGIN_RESOLUTION`. Запас —
    это доля от нуля до единицы, и различать в ней больше девяти значащих цифр
    бессмысленно: столько точности нет ни в одном исходном числе. Зато без округления
    два почти равных запаса упорядочиваются по последнему разряду, а он на разных
    машинах разный — и маршрут получается разный.
    """

    # Очередь сравнивает кортежи поэлементно, поэтому «родителя нет» надо записать
    # числом: None рядом с int сравнить нельзя, и на редком совпадении первых трёх
    # полей поиск падал бы с ошибкой типов.
    no_parent = -1

    best: dict[int, tuple[float, float]] = {}
    previous: dict[int, int] = {}
    heap: list[tuple[float, float, int, int]] = []
    for slot, satellite in enumerate(graph.uplink[client_id]):
        margin = _coarse(_elevation_fraction(graph.uplink_margin_deg[client_id][slot]))
        heapq.heappush(
            heap, (-margin, graph.uplink_distance_km[client_id][slot], satellite, no_parent)
        )

    finished: tuple[float, float, list[int], str] | None = None
    while heap:
        negative, length, node, parent = heapq.heappop(heap)
        margin = -negative
        if node in best:
            continue
        best[node] = (margin, length)
        previous[node] = parent

        if node in downlink:
            gateway_id, _ = downlink[node]
            slot = graph.uplink[gateway_id].index(node)
            closing = _coarse(_elevation_fraction(graph.uplink_margin_deg[gateway_id][slot]))
            total = min(margin, closing)
            reach = length + graph.uplink_distance_km[gateway_id][slot]
            if finished is None or (total, -reach) > (finished[0], -finished[1]):
                finished = (total, reach, _unwind(previous, node), gateway_id)

        # Запас у всего оставшегося в очереди не больше текущего, поэтому маршрут с
        # бо́льшим запасом уже не появится. А вот с таким же — ещё может, и он может
        # оказаться короче, поэтому сравнение строгое.
        if finished is not None and finished[0] > margin:
            break

        for slot, neighbour in enumerate(graph.neighbours[node]):
            if neighbour not in best:
                link = graph.neighbour_margin_km[node][slot]
                hop = graph.neighbour_distance_km[node][slot]
                limit = link + hop
                widened = min(margin, _coarse(link / limit if limit else 0.0))
                heapq.heappush(heap, (-widened, length + hop, neighbour, node))

    return (finished[2], finished[3]) if finished else None


def _coarse(fraction: float) -> float:
    """
    Огрубить запас до разрешения, ниже которого различать его нет смысла.

    Запас — доля от нуля до единицы. Девяти значащих цифр в исходных данных нет и
    близко, а вот последний разряд вычисленного числа на разных машинах отличается,
    и без огрубления он решает, какой маршрут выиграет.
    """

    return round(fraction, MARGIN_RESOLUTION)


def _elevation_fraction(margin_deg: float) -> float:
    """Запас по углу места как доля промежутка между порогом и зенитом."""

    return max(margin_deg, 0.0) / _ZENITH_DEG


def _unwind(previous: dict[int, int | None], node: int) -> list[int]:
    """
    Пройти по цепочке предков до первого аппарата и вернуть её от начала к концу.

    Конец цепочки помечают двумя способами: `None` у поисков, где родителя может не
    быть, и −1 у широчайшего пути, где очередь обязана уметь сравнивать это поле.
    """

    chain = [node]
    while previous[chain[-1]] not in (None, -1):
        chain.append(previous[chain[-1]])  # type: ignore[arg-type]
    chain.reverse()
    return chain


def _describe(
    graph: SliceGraph, client_id: str, satellites: list[int], gateway_id: str
) -> Route:
    """Измерить уже найденный маршрут."""

    uplink_slot = graph.uplink[client_id].index(satellites[0])
    downlink_slot = graph.uplink[gateway_id].index(satellites[-1])

    length = (
        graph.uplink_distance_km[client_id][uplink_slot]
        + graph.uplink_distance_km[gateway_id][downlink_slot]
    )
    range_margin = float("inf")
    for a, b in zip(satellites, satellites[1:], strict=False):
        slot = graph.neighbours[a].index(b)
        length += graph.neighbour_distance_km[a][slot]
        range_margin = min(range_margin, graph.neighbour_margin_km[a][slot])

    path = [client_id] + [graph.satellite_ids[n] for n in satellites] + [gateway_id]
    return Route(
        path=path,
        gateway_id=gateway_id,
        hops=len(path) - 1,
        length_km=length,
        min_elevation_margin_deg=min(
            graph.uplink_margin_deg[client_id][uplink_slot],
            graph.uplink_margin_deg[gateway_id][downlink_slot],
        ),
        min_range_margin_km=range_margin,
    )
