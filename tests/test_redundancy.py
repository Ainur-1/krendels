"""
Запас маршрутов: проверка на согласованность и на измеренные числа.

Главная проверка снова перекрёстная. Поток в графе с расщеплением узлов равен нулю
ровно тогда, когда маршрута нет, — а это уже посчитано совсем другим способом, через
объединение множеств. Два независимых алгоритма обязаны сойтись на каждом отсчёте, а
не в среднем за сутки.
"""

from __future__ import annotations

import numpy as np
import pytest

from cosmo_net.analysis.reachability import availability_series
from cosmo_net.analysis.redundancy import disjoint_path_counts, redundancy_report


def test_zero_paths_means_no_route(
    full_constellation, first_launch, satellite_outages, link_range, judge_fixture
):
    """Поток равен нулю точно на тех отсчётах, где достижимость говорит «пути нет»."""

    for scenario in (
        full_constellation,
        first_launch,
        satellite_outages,
        link_range,
        judge_fixture,
    ):
        reachable = availability_series(scenario)
        for client in disjoint_path_counts(scenario):
            expected = reachable[client.client_id]
            assert ((client.disjoint_paths > 0) == expected).all(), (
                scenario.meta.id,
                client.client_id,
            )


def test_paths_never_exceed_the_visible_satellites(full_constellation):
    """Маршрутов без общих аппаратов не может быть больше, чем аппаратов над пунктом."""

    from cosmo_net.analysis.exploration import visible_counts

    visible = visible_counts(full_constellation)
    for client in disjoint_path_counts(full_constellation):
        assert (client.disjoint_paths <= visible[client.client_id]).all()


def test_there_is_almost_no_redundancy(full_constellation):
    """
    Измерено: при доступности 96.7 % у C65 ровно один маршрут на 80.1 % отсчётов.

    Это и есть честный ответ про устойчивость. Связь есть почти всегда, но запаса у
    неё почти нет: в подавляющем большинстве моментов она держится на одном аппарате,
    и его отказ именно в этот момент рвёт её немедленно.
    """

    report = redundancy_report(full_constellation)
    by_id = {c.client_id: c for c in report.clients}

    assert by_id["C65"].single_path_share == pytest.approx(0.801, abs=0.005)
    assert by_id["C65"].mean == pytest.approx(1.14, abs=0.01)
    assert by_id["C70"].single_path_share == pytest.approx(0.682, abs=0.005)
    assert by_id["C72"].single_path_share == pytest.approx(0.617, abs=0.005)

    # Дальше от шлюза — больше маршрутов: пункт обслуживается сетью, а не одним
    # аппаратом, и у сети выбор шире. Тот же порядок, что дала разведка данных.
    assert by_id["C65"].mean < by_id["C70"].mean < by_id["C72"].mean
    assert report.worst_single_path_share == pytest.approx(0.801, abs=0.005)


def test_the_broken_network_loses_redundancy_first(link_range):
    """При дальности 2000 км запас исчезает раньше, чем сама связь."""

    report = redundancy_report(link_range)
    for client in report.clients:
        assert client.redundant_share < 0.12
        assert client.mean < 0.85


def test_the_second_gateway_adds_redundancy(link_range):
    """
    Второй шлюз добавляет не только доступность, но и запас — и это разные вещи.

    Проверяется именно прирост запаса: доступность могла бы вырасти и за счёт
    единственных маршрутов, а тогда система осталась бы такой же хрупкой.
    """

    from cosmo_net.scenario.schema import GroundSite

    changed = link_range.model_copy(deep=True)
    changed.ground_sites.append(
        GroundSite(id="G2", name="Второй шлюз", role="gateway", lat_deg=69.35, lon_deg=88.2)
    )

    before = {c.client_id: c for c in disjoint_path_counts(link_range)}
    after = {c.client_id: c for c in disjoint_path_counts(changed)}
    for client_id, series in after.items():
        assert series.redundant_share > before[client_id].redundant_share
        assert series.mean > before[client_id].mean


def test_counts_are_whole_numbers(full_constellation):
    """Маршрут либо есть, либо нет: дробного потока здесь быть не может."""

    for client in disjoint_path_counts(full_constellation):
        assert client.disjoint_paths.dtype.kind == "i"
        assert (client.disjoint_paths >= 0).all()
        assert np.isfinite(client.disjoint_paths).all()


def test_an_offline_gateway_leaves_no_paths(full_constellation):
    """Выключенный шлюз обнуляет запас: приземлять маршрут некуда."""

    from cosmo_net.scenario.schema import GatewayOutage

    dead = full_constellation.model_copy(deep=True)
    dead.gateway_outages = [
        GatewayOutage(gateway_id=full_constellation.gateways[0].id, start_s=0, end_s=86400)
    ]

    for client in disjoint_path_counts(dead):
        assert int(client.disjoint_paths.max()) == 0
