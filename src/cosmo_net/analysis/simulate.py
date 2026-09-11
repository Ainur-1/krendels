"""
One run: a scenario over its whole horizon, step by step, with a route for every client.

This is the function everything else calls. The interface calls it when the user
presses the button, the comparison calls it twice, the criticality study calls it
49 times and the configuration sweep calls it once per candidate — so what it
returns is deliberately small. Positions and link sets are not kept: they are
reproducible from the scenario in under a tenth of a second, and holding 26 MB of
arrays per saved variant would cost more than recomputing a snapshot when one is
actually asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.analysis.metrics import ClientMetrics, collect_gaps
from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import Trajectory, compute_trajectory
from cosmo_net.routing.diagnose import Outage, diagnose
from cosmo_net.routing.graph import SliceGraph, build_slice
from cosmo_net.routing.strategies import Route, Strategy, find_route
from cosmo_net.scenario.schema import Scenario


@dataclass
class RunResult:
    """What a completed run says about a design."""

    scenario: Scenario
    strategy: Strategy
    times_s: list[int]

    metrics: dict[str, ClientMetrics]
    routes: dict[str, list[Route | None]]
    causes: dict[str, list[Outage]]

    @property
    def worst_availability(self) -> float:
        """
        The lowest availability across clients.

        The target is stated per terminal, so a design is only as good as the
        terminal it serves worst; averaging across three sites hides the one that
        fails and is the easiest way to talk yourself into a bad configuration.
        """

        return min((m.availability_share for m in self.metrics.values()), default=0.0)

    @property
    def worst_max_gap_s(self) -> int:
        return max((m.max_gap_s for m in self.metrics.values()), default=0)

    @property
    def meets_target(self) -> bool:
        return self.worst_availability >= self.scenario.environment.target_availability

    def summary(self) -> dict[str, object]:
        target = self.scenario.environment.target_availability
        return {
            "scenario_id": self.scenario.meta.id,
            "strategy": str(self.strategy),
            "steps": len(self.times_s),
            "step_s": self.scenario.environment.step_s,
            "horizon_s": self.scenario.environment.horizon_s,
            "target_availability": target,
            "worst_availability": self.worst_availability,
            "worst_max_gap_s": self.worst_max_gap_s,
            "meets_target": self.meets_target,
            "clients": [m.summary(target) for m in self.metrics.values()],
        }


@dataclass(frozen=True)
class NetworkSnapshot:
    """The state of the network at one moment, for drawing rather than for measuring."""

    t_s: float
    satellites: list[dict[str, object]]
    links: list[dict[str, object]]
    ground_links: list[dict[str, object]]
    elevation_deg: dict[str, dict[str, float]]


def simulate(scenario: Scenario, strategy: Strategy = Strategy.MIN_HOPS) -> RunResult:
    """Run `scenario` over its full grid and measure every client on it."""

    times = scenario.times
    trajectory = compute_trajectory(scenario)
    contacts = compute_contacts(scenario, trajectory)
    offline = gateway_offline_mask(scenario, np.asarray(times, dtype=float))

    env = scenario.environment
    client_ids = [site.id for site in scenario.clients]
    gateway_ids = [site.id for site in scenario.gateways]
    gateway_slots = {site.id: g for g, site in enumerate(scenario.ground_sites)}
    client_slots = {site.id: g for g, site in enumerate(scenario.ground_sites)}

    routes: dict[str, list[Route | None]] = {c: [] for c in client_ids}
    causes: dict[str, list[Outage]] = {c: [] for c in client_ids}
    visible = {c: 0 for c in client_ids}

    for step in range(len(times)):
        graph = build_slice(contacts, step, env.isl_range_km, env.min_elevation_deg)
        offline_now = {g for g in gateway_ids if offline[step, gateway_slots[g]]}

        for client_id in client_ids:
            if contacts.ground_open[step, client_slots[client_id]].any():
                visible[client_id] += 1

            route = find_route(graph, client_id, [g for g in gateway_ids if g not in offline_now],
                               strategy)
            routes[client_id].append(route)
            if route is not None:
                causes[client_id].append(Outage.NONE)
            else:
                causes[client_id].append(diagnose(graph, client_id, gateway_ids, offline_now))

    metrics: dict[str, ClientMetrics] = {}
    for client_id in client_ids:
        found = [r for r in routes[client_id] if r is not None]
        metrics[client_id] = ClientMetrics(
            client_id=client_id,
            steps=len(times),
            visible_steps=visible[client_id],
            routed_steps=len(found),
            gaps=collect_gaps(causes[client_id], times, env.step_s),
            causes=_count_causes(causes[client_id]),
            hop_counts=[r.hops for r in found],
            route_lengths_km=[r.length_km for r in found],
        )

    return RunResult(
        scenario=scenario,
        strategy=strategy,
        times_s=times,
        metrics=metrics,
        routes=routes,
        causes=causes,
    )


def snapshot_at(scenario: Scenario, t_s: float) -> NetworkSnapshot:
    """
    The network at one moment, recomputed rather than stored.

    Building the single-step trajectory costs about a millisecond, which is cheaper
    than keeping every run's arrays alive for a time slider that only ever looks at
    one step.
    """

    times = np.array([float(t_s)])
    trajectory = compute_trajectory(scenario, times)
    contacts = compute_contacts(scenario, trajectory)
    env = scenario.environment

    satellites = [
        {
            "id": satellite_id,
            "x_km": float(trajectory.ecef_km[0, k, 0]),
            "y_km": float(trajectory.ecef_km[0, k, 1]),
            "z_km": float(trajectory.ecef_km[0, k, 2]),
            "lat_deg": _latitude(trajectory.ecef_km[0, k]),
            "lon_deg": _longitude(trajectory.ecef_km[0, k]),
            "plane_id": scenario.design.satellites[k].plane_id,
            "active": bool(contacts.active[0, k]),
        }
        for k, satellite_id in enumerate(contacts.satellite_ids)
    ]

    links = [
        {
            "a": contacts.satellite_ids[i],
            "b": contacts.satellite_ids[j],
            "distance_km": float(contacts.isl_distance_km[0, p]),
        }
        for p, (i, j) in enumerate(contacts.pair_index)
        if contacts.isl_open[0, p]
    ]

    ground_links = []
    elevation: dict[str, dict[str, float]] = {}
    for g, site_id in enumerate(contacts.ground_ids):
        elevation[site_id] = {
            contacts.satellite_ids[k]: float(contacts.elevation_deg[0, g, k])
            for k in range(len(contacts.satellite_ids))
            if contacts.active[0, k]
        }
        for k in np.where(contacts.ground_open[0, g])[0]:
            ground_links.append(
                {
                    "site": site_id,
                    "satellite": contacts.satellite_ids[k],
                    "distance_km": float(contacts.ground_distance_km[0, g, k]),
                    "elevation_deg": float(contacts.elevation_deg[0, g, k]),
                }
            )

    del env
    return NetworkSnapshot(
        t_s=float(t_s),
        satellites=satellites,
        links=links,
        ground_links=ground_links,
        elevation_deg=elevation,
    )


def slice_graph_at(contacts: ContactSeries, step: int, scenario: Scenario) -> SliceGraph:
    """Convenience for callers that already hold a contact series."""

    env = scenario.environment
    return build_slice(contacts, step, env.isl_range_km, env.min_elevation_deg)


def _count_causes(causes: list[Outage]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for cause in causes:
        if cause is Outage.NONE:
            continue
        counts[str(cause)] = counts.get(str(cause), 0) + 1
    return counts


def _latitude(position: np.ndarray) -> float:
    return float(np.degrees(np.arcsin(position[2] / np.linalg.norm(position))))


def _longitude(position: np.ndarray) -> float:
    return float(np.degrees(np.arctan2(position[1], position[0])))


__all__ = [
    "NetworkSnapshot",
    "RunResult",
    "Trajectory",
    "simulate",
    "snapshot_at",
    "slice_graph_at",
]
