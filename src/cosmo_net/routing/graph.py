"""
One step of the run, as a graph a path search can walk.

The shape of this graph is the one place where the case's rules about who may
relay have to be enforced, and it is easy to get wrong: `snapshot()` in the
reference module emits a ground edge for every ground site, client sites included,
so a graph built by dropping its `edges` list into an adjacency map lets traffic
hop from one northern terminal to another and come out looking far healthier than
the network is. Here ground sites are only ever an endpoint: the client is where a
path starts, a gateway is where it ends, and everything in between is a satellite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.geometry.contacts import ContactSeries


@dataclass(frozen=True)
class SliceGraph:
    """The usable links at one step, indexed for search."""

    step: int
    """Index into the calculation grid, not a time in seconds."""

    satellite_ids: list[str]

    neighbours: list[list[int]]
    """Satellite index → the satellites it can reach directly."""

    neighbour_distance_km: list[list[float]]
    """Parallel to `neighbours`."""

    neighbour_margin_km: list[list[float]]
    """How much range is left over on each link. Zero means it is about to break."""

    uplink: dict[str, list[int]]
    """Ground site id → the satellites it can exchange traffic with."""

    uplink_distance_km: dict[str, list[float]]

    uplink_margin_deg: dict[str, list[float]]
    """Elevation above the threshold, in degrees. The ground-link counterpart of range margin."""


def build_slice(contacts: ContactSeries, step: int, isl_range_km: float,
                min_elevation_deg: float) -> SliceGraph:
    """Assemble the graph for one step of an already-computed contact series."""

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
