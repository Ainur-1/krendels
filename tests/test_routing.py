"""
Поиск маршрута: каким маршрут имеет право быть и зачем нужна каждая стратегия.

Первый тест в этом файле стережёт правило, которое проще всего нарушить по
недосмотру: клиентский терминал — это конец маршрута и никогда не ретранслятор.
Эталонный модуль выдаёт наземные рёбра для всех пунктов, поэтому граф, собранный
прямо из его вывода, молча позволил бы одному северному терминалу везти трафик
другого и показал бы сеть здоровее проектируемой.
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
    """Каждый промежуточный узел каждого маршрута — это аппарат."""

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
            assert not (set(interior) & ground), f"{route.path} идёт через наземный пункт"


def test_hop_count_includes_both_ground_links(full_constellation):
    """Маршрут через один аппарат — это два перехода, а не один: вверх и вниз считаются оба."""

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
    Доступность — это связность, а не поиск.

    Измерено на выданных сценариях: три стратегии выбирают разные маршруты и дают
    одну и ту же доступность с точностью до отсчёта. Если этот тест когда-нибудь
    упадёт, значит один из поисков теряет пути, а не просто предпочитает другие.
    """

    reference = simulate(full_constellation, Strategy.MIN_HOPS)
    other = simulate(full_constellation, strategy)
    for client_id, metrics in reference.metrics.items():
        assert metrics.routed_steps == other.metrics[client_id].routed_steps


def test_min_distance_is_not_longer_than_min_hops(full_constellation):
    """Каждая стратегия не хуже остальных в том, что она улучшает."""

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
    assert improved > 0, "широчайший путь ни разу не нашёл маршрут с лучшим запасом"


def test_no_route_when_the_only_gateway_is_offline(full_constellation):
    """
    Отказ шлюза сообщается именно как отказ шлюза и никогда как разрыв сети.

    Остаток приходится на `no_client_contact`: причины упорядочены так, что называется
    первое условие, блокирующее путь, а на 16 отсчётах из 720 над C65 и так никого
    нет. Назвать это проблемой шлюза значило бы отправить инженера чинить не тот
    конец линии.
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
    Сценарий 04 — тот, где проблема в самой сети.

    Соседи внутри плоскости разнесены на 2700 км, а предел дальности 2000 км, поэтому
    кольцо распадается и остаются только случайные пересечения плоскостей. Перерывы,
    которые из этого следуют, — это разрывы сети, и так диагностируются 33.8 %
    отсчётов у C72.
    """

    result = simulate(link_range)
    causes = result.metrics["C72"].causes
    assert causes[str(Outage.NETWORK_SPLIT)] > causes.get(str(Outage.NO_CLIENT_CONTACT), 0)


def test_diagnose_reports_missing_coverage_on_the_first_launch(first_launch):
    """Сценарий 02 — обратный случай: одна плоскость, и над пунктом чаще всего вообще никого."""

    result = simulate(first_launch)
    causes = result.metrics["C65"].causes
    assert causes[str(Outage.NO_CLIENT_CONTACT)] > causes.get(str(Outage.NETWORK_SPLIT), 0)


def test_find_route_returns_none_without_a_gateway(full_constellation):
    graph = slice_at(full_constellation, 0)
    assert find_route(graph, "C65", [], Strategy.MIN_HOPS) is None


def test_diagnose_without_client_contact(first_launch):
    """На отсчёте, где над пунктом никого нет, причина — собственный горизонт клиента."""

    result = simulate(first_launch)
    step = result.causes["C65"].index(Outage.NO_CLIENT_CONTACT)
    graph = slice_at(first_launch, result.times_s[step])
    assert diagnose(graph, "C65", [g.id for g in first_launch.gateways], set()) is (
        Outage.NO_CLIENT_CONTACT
    )
