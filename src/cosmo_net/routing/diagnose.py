"""
Why a client has no route at this step.

The case names four reasons and asks the service to show which one applies: no
satellite visible, the inter-satellite network split, no contact with a gateway, or
the gateway itself unavailable. Separating them is what turns a flat availability
figure into something an engineer can act on — the same 80 % means "add satellites"
in one scenario and "add a ground station" in another, and on the supplied data it
means both, in different scenarios.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum

from cosmo_net.routing.graph import SliceGraph


class Outage(StrEnum):
    """What stops traffic at one step. `NONE` means a route exists."""

    NONE = "none"

    NO_CLIENT_CONTACT = "no_client_contact"
    """Nothing in service is above the terminal's horizon."""

    GATEWAY_OFFLINE = "gateway_offline"
    """Every gateway is inside a declared outage, so there is nowhere to deliver to."""

    NO_GATEWAY_CONTACT = "no_gateway_contact"
    """The gateways are up, but nothing in service is above any of them."""

    NETWORK_SPLIT = "network_split"
    """Both ends have a satellite overhead and no chain of links joins them."""


def diagnose(
    graph: SliceGraph,
    client_id: str,
    gateway_ids: list[str],
    offline_gateway_ids: set[str],
) -> Outage:
    """
    Classify one step for one client.

    The order is the order in which the problems have to be ruled out: a split
    network cannot be observed until both ends are known to have a satellite, so
    reporting the split first would blame the constellation for a ground problem.
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
