"""
Path search: what a route is allowed to be, and what each strategy is for.

The first test in this file is the one that guards the rule most easily broken by
accident — that a client terminal is an endpoint and never a relay. The reference
module hands out ground edges for every site, so a graph assembled straight from
its output would quietly let one northern terminal carry another's traffic and
report a network healthier than the one being designed.
"""

from __future__ import annotations

import numpy as np
import pytest

from cosmo_net.analysis.simulate import simulate
from cosmo_net.geometry.contacts import compute_contacts
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.routing.diagnose import Outage, diagnose
from cosmo_net.routing.graph import build_slice
from cosmo_net.routing.strategies import Strategy, find_route
from cosmo_net.scenario.schema import GatewayOutage


def slice_at(scenario, t_s: float):
    trajectory = compute_trajectory(scenario, np.array([float(t_s)]))
    contacts = compute_contacts(scenario, trajectory)
    env = scenario.environment
    return build_slice(contacts, 0, env.isl_range_km, env.min_elevation_deg)


def test_clients_are_never_intermediate_nodes(full_constellation):
    """Every interior node of every route is a satellite."""

    result = simulate(full_constellation)
    ground = {site.id for site in full_constellation.ground_sites}
    clients = {site.id for site in full_constellation.clients}

    for client_id, routes in result.routes.items():
        for route in routes:
            if route is None:
                continue
            assert route.path[0] == client_id
            assert route.path[-1] not in clients
            interior = route.path[1:-1]
            assert not (set(interior) & ground), f"{route.path} relays through a ground site"


def test_hop_count_includes_both_ground_links(full_constellation):
    """A route through one satellite is two hops, not one: up and down both count."""

    result = simulate(full_constellation)
    for routes in result.routes.values():
        for route in routes:
            if route is None:
                continue
            assert route.hops == len(route.path) - 1
            assert route.hops >= 2


def test_route_endpoints_are_a_client_and_a_gateway(full_constellation):
    gateways = {site.id for site in full_constellation.gateways}
    result = simulate(full_constellation)
    for client_id, routes in result.routes.items():
        for route in routes:
            if route is not None:
                assert route.path[0] == client_id
                assert route.path[-1] in gateways
                assert route.gateway_id == route.path[-1]


@pytest.mark.parametrize("strategy", list(Strategy))
def test_strategies_agree_on_whether_a_route_exists(full_constellation, strategy):
    """
    Availability is connectivity, not search.

    Measured on the supplied scenarios: the three strategies pick different routes
    and return the same availability to within a step. If this ever fails, one of
    the searches is missing paths rather than merely preferring different ones.
    """

    reference = simulate(full_constellation, Strategy.MIN_HOPS)
    other = simulate(full_constellation, strategy)
    for client_id, metrics in reference.metrics.items():
        assert metrics.routed_steps == other.metrics[client_id].routed_steps


def test_min_distance_is_not_longer_than_min_hops(full_constellation):
    """Each strategy is at least as good as the others at the thing it optimises."""

    by_hops = simulate(full_constellation, Strategy.MIN_HOPS)
    by_distance = simulate(full_constellation, Strategy.MIN_DISTANCE)

    for client_id in by_hops.routes:
        for a, b in zip(
            by_hops.routes[client_id], by_distance.routes[client_id], strict=True
        ):
            if a is None or b is None:
                continue
            assert b.length_km <= a.length_km + 1e-6
            assert a.hops <= b.hops


def test_max_margin_does_not_weaken_the_weakest_link(full_constellation):
    by_hops = simulate(full_constellation, Strategy.MIN_HOPS)
    by_margin = simulate(full_constellation, Strategy.MAX_MARGIN)

    improved = 0
    for client_id in by_hops.routes:
        for a, b in zip(
            by_hops.routes[client_id], by_margin.routes[client_id], strict=True
        ):
            if a is None or b is None:
                continue
            if b.min_elevation_margin_deg > a.min_elevation_margin_deg + 1e-9:
                improved += 1
    assert improved > 0, "widest-path search never found a better-conditioned route"


def test_no_route_when_the_only_gateway_is_offline(full_constellation):
    """
    A gateway outage is reported as such, and never as a broken network.

    The residue is `no_client_contact`: the causes are ordered so that the first
    condition blocking the path is the one named, and at 16 of the 720 steps C65 has
    nothing overhead to begin with. Diagnosing that as a gateway problem would send
    an engineer to fix the wrong end of the link.
    """

    scenario = full_constellation.model_copy(deep=True)
    gateway_id = scenario.gateways[0].id
    scenario.gateway_outages = [
        GatewayOutage(gateway_id=gateway_id, start_s=0, end_s=scenario.environment.horizon_s)
    ]

    result = simulate(scenario)
    for client_id, metrics in result.metrics.items():
        assert metrics.routed_steps == 0, client_id
        assert set(metrics.causes) <= {
            str(Outage.GATEWAY_OFFLINE),
            str(Outage.NO_CLIENT_CONTACT),
        }
        assert metrics.causes[str(Outage.GATEWAY_OFFLINE)] > 0.9 * metrics.steps
        assert str(Outage.NETWORK_SPLIT) not in metrics.causes
        assert str(Outage.NO_GATEWAY_CONTACT) not in metrics.causes


def test_diagnose_reports_a_split_network(link_range):
    """
    Scenario 04 is the one where the network itself is the problem.

    In-plane neighbours are 2700 km apart and the range limit is 2000 km, so the
    ring is gone and only incidental crossings between planes remain. The gaps that
    follow are splits, and 33.8 % of C72's steps are diagnosed that way.
    """

    result = simulate(link_range)
    causes = result.metrics["C72"].causes
    assert causes[str(Outage.NETWORK_SPLIT)] > causes.get(str(Outage.NO_CLIENT_CONTACT), 0)


def test_diagnose_reports_missing_coverage_on_the_first_launch(first_launch):
    """Scenario 02 is the opposite case: one plane, and mostly nothing overhead at all."""

    result = simulate(first_launch)
    causes = result.metrics["C65"].causes
    assert causes[str(Outage.NO_CLIENT_CONTACT)] > causes.get(str(Outage.NETWORK_SPLIT), 0)


def test_find_route_returns_none_without_a_gateway(full_constellation):
    graph = slice_at(full_constellation, 0)
    assert find_route(graph, "C65", [], Strategy.MIN_HOPS) is None


def test_diagnose_without_client_contact(first_launch):
    """At a step where nothing is overhead, the cause is the client's own horizon."""

    result = simulate(first_launch)
    step = result.causes["C65"].index(Outage.NO_CLIENT_CONTACT)
    graph = slice_at(first_launch, result.times_s[step])
    assert diagnose(graph, "C65", [g.id for g in first_launch.gateways], set()) is (
        Outage.NO_CLIENT_CONTACT
    )
