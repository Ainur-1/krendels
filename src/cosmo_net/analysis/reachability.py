"""
Whether a route exists, without finding one.

Availability is a connectivity question: a path from a client to a gateway exists
exactly when some satellite the client can see sits in the same connected component
as some satellite a gateway can see. Answering it that way costs one pass of
union-find per step instead of one search per client per step, and it gives the
same answer — which the tests check against the full run rather than assume.

It exists because two studies call it in bulk: satellite criticality runs the
horizon once per satellite, and the configuration sweep runs it once per candidate
design. Measured on scenario 01, a full run with routing is 124 ms and this is
68 ms. The remainder is `compute_contacts`, which both share — so the knockout
study also hands in a contact series built once and re-masked through
`ContactSeries.with_service`, and pays the 45 ms only on its first run.
"""

from __future__ import annotations

import numpy as np

from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario


def availability_series(
    scenario: Scenario, contacts: ContactSeries | None = None
) -> dict[str, np.ndarray]:
    """
    (T,) booleans per client: was there a path to some reachable gateway at this step.

    Passing `contacts` in lets a caller that already built them — the criticality
    study rebuilds only the service mask between runs — skip the expensive half.
    """

    if contacts is None:
        contacts = compute_contacts(scenario, compute_trajectory(scenario))

    times = np.asarray(scenario.times, dtype=float)
    offline = gateway_offline_mask(scenario, times)

    client_slots = [
        g for g, site in enumerate(scenario.ground_sites) if site.role == "client"
    ]
    gateway_slots = [
        g for g, site in enumerate(scenario.ground_sites) if site.role == "gateway"
    ]
    client_ids = [scenario.ground_sites[g].id for g in client_slots]

    steps = len(scenario.times)
    result = {client_id: np.zeros(steps, dtype=bool) for client_id in client_ids}
    pairs = contacts.pair_index

    for step in range(steps):
        # Only gateways that are up can terminate a path, so an offline station
        # contributes none of its visible craft to the landing set.
        landing: set[int] = set()
        for g in gateway_slots:
            if offline[step, g]:
                continue
            landing.update(np.flatnonzero(contacts.ground_open[step, g]).tolist())
        if not landing:
            continue

        parent = list(range(len(contacts.satellite_ids)))
        for p in np.flatnonzero(contacts.isl_open[step]):
            _union(parent, int(pairs[p, 0]), int(pairs[p, 1]))

        landing_roots = {_find(parent, n) for n in landing}
        for client_id, g in zip(client_ids, client_slots, strict=True):
            visible = np.flatnonzero(contacts.ground_open[step, g])
            if visible.size and any(_find(parent, int(n)) in landing_roots for n in visible):
                result[client_id][step] = True

    return result


def worst_availability(scenario: Scenario, contacts: ContactSeries | None = None) -> float:
    """
    The share of steps reachable for the client served worst.

    The target is stated per terminal, so this — not the mean across sites — is what
    a configuration has to be judged on.
    """

    series = availability_series(scenario, contacts)
    if not series:
        return 0.0
    return min(float(reachable.mean()) for reachable in series.values())


def longest_gap_steps(reachable: np.ndarray) -> int:
    """The longest run of consecutive unreachable steps."""

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
