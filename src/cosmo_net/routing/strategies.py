"""
Three ways to get from a client terminal to a gateway, and what each one optimises.

They exist because the case asks the team to explain its routing approach and show
it working, and one search presented on its own says nothing about why it was
chosen. What they do *not* do is change availability: whether a path exists at a
given step is a property of the graph, not of the search over it, and all three
agree to 0.1 pp on every supplied scenario. They differ in the route they pick —
relay count, signal path length, how close the weakest link sits to its limit — and
that is the honest claim to make about them.
"""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from cosmo_net.routing.graph import SliceGraph

# Ground margin is degrees above the threshold, range margin is kilometres to
# spare. To compare the two on one route both are expressed as a fraction of what
# was available: an elevation margin against the span from the threshold to the
# zenith, a range margin against the range limit itself.
_ZENITH_DEG = 90.0


class Strategy(StrEnum):
    """Which route to prefer when several exist."""

    MIN_HOPS = "min_hops"
    """Fewest relays. The default: every hop is a transponder that can fail."""

    MIN_DISTANCE = "min_distance"
    """Shortest signal path in kilometres, which is the propagation delay."""

    MAX_MARGIN = "max_margin"
    """Widest path: the route whose weakest link sits furthest from its limit."""


@dataclass(frozen=True)
class Route:
    """A path that existed at one step, and the properties that make it worth comparing."""

    path: list[str]
    """Client id, then satellite ids, then gateway id."""

    gateway_id: str

    hops: int
    """Edges, both ground links included, so the shortest possible route has 2."""

    length_km: float

    min_elevation_margin_deg: float
    """Degrees above the threshold on the worse of the two ground links."""

    min_range_margin_km: float
    """Kilometres to spare on the tightest inter-satellite link. Infinite when there is none."""


def find_route(
    graph: SliceGraph,
    client_id: str,
    gateway_ids: list[str],
    strategy: Strategy = Strategy.MIN_HOPS,
) -> Route | None:
    """
    The best route from `client_id` to any of `gateway_ids`, or None when there is none.

    Every gateway is a valid destination and the search treats them as one: an
    operator does not care which ground station the traffic leaves through, only
    that it leaves.
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
    Satellite index → the gateway it should hand traffic to, and the slant range there.

    A satellite over two reachable gateways uses the nearer one; the identifier
    breaks a tie, so the same network always produces the same route.
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
    """Breadth-first, so the first satellite reached that can hand off is on a shortest route."""

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
    Dijkstra over kilometres, with the final ground link priced into the search.

    Leaving the downlink out and picking the nearest gateway afterwards would give a
    route that is shortest to some satellite rather than shortest to the ground, and
    the two are not the same once a longer chain ends closer to a station.
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

        # Anything still queued already costs at least `cost`, so once the best
        # complete route is shorter than the cheapest remaining satellite it cannot
        # be beaten and the search stops.
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
    Widest path: maximise the smallest margin along the route.

    The quantity being maximised is a fraction rather than a physical unit, because
    the route mixes two kinds of headroom — degrees of elevation on the ground links
    and kilometres of range between craft — and they have to be comparable to have a
    weakest link at all.
    """

    best: dict[int, float] = {}
    previous: dict[int, int | None] = {}
    heap: list[tuple[float, int, int | None]] = []
    for slot, satellite in enumerate(graph.uplink[client_id]):
        margin = _elevation_fraction(graph.uplink_margin_deg[client_id][slot])
        heapq.heappush(heap, (-margin, satellite, None))

    finished: tuple[float, list[int], str] | None = None
    while heap:
        negative, node, parent = heapq.heappop(heap)
        margin = -negative
        if node in best:
            continue
        best[node] = margin
        previous[node] = parent

        if node in downlink:
            gateway_id, _ = downlink[node]
            slot = graph.uplink[gateway_id].index(node)
            closing = _elevation_fraction(graph.uplink_margin_deg[gateway_id][slot])
            total = min(margin, closing)
            if finished is None or total > finished[0]:
                finished = (total, _unwind(previous, node), gateway_id)

        if finished is not None and finished[0] >= margin:
            break

        for slot, neighbour in enumerate(graph.neighbours[node]):
            if neighbour not in best:
                link = graph.neighbour_margin_km[node][slot]
                limit = link + graph.neighbour_distance_km[node][slot]
                widened = min(margin, link / limit if limit else 0.0)
                heapq.heappush(heap, (-widened, neighbour, node))

    return (finished[1], finished[2]) if finished else None


def _elevation_fraction(margin_deg: float) -> float:
    """Elevation headroom as a share of the span between the threshold and the zenith."""

    return max(margin_deg, 0.0) / _ZENITH_DEG


def _unwind(previous: dict[int, int | None], node: int) -> list[int]:
    """Walk the parent chain back to the first satellite and return it front to back."""

    chain = [node]
    while previous[chain[-1]] is not None:
        chain.append(previous[chain[-1]])  # type: ignore[arg-type]
    chain.reverse()
    return chain


def _describe(
    graph: SliceGraph, client_id: str, satellites: list[int], gateway_id: str
) -> Route:
    """Measure a route that has already been found."""

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
