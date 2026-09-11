"""
Перебор размещения второй точки приземления.

Тесты закрепляют не только числа, но и форму поверхности: она разная у целой сети и
у распавшейся, и именно это различие даёт вывод, а не сам факт прироста.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.placement import evaluate_site, placement_grid, with_gateway_at
from cosmo_net.analysis.reachability import worst_availability

NORILSK = (69.35, 88.2)


def test_the_grid_has_the_expected_shape(full_constellation):
    """Широты 50–85 через 5, долготы через 15 без повтора 180-го меридиана."""

    report = placement_grid(full_constellation)
    assert len(report.lat_deg) == 8
    assert len(report.lon_deg) == 24
    assert len(report.points) == 192
    assert report.grid.shape == (8, 24)
    assert report.lon_deg[0] == -180.0 and report.lon_deg[-1] == 165.0


def test_a_second_gateway_never_hurts(full_constellation, link_range):
    """
    Добавить точку приземления нельзя во вред: маршрут, который был, остаётся.

    Проверка на знак и на то, что кандидат действительно добавляется, а не подменяет
    выданный шлюз.
    """

    for scenario in (full_constellation, link_range):
        report = placement_grid(scenario)
        assert all(p.gain_pp >= -1e-9 for p in report.points)
        assert report.baseline_worst_availability == pytest.approx(
            worst_availability(scenario)
        )
        assert len(with_gateway_at(scenario, 70.0, 90.0).gateways) == len(scenario.gateways) + 1


def test_a_healthy_network_does_not_care_where_the_gateway_is(full_constellation):
    """
    Измерено: на целой сети второй шлюз даёт 97.8 % из любой точки сетки.

    Сеть довезёт трафик куда угодно, поэтому выбор места ничего не решает — и это
    объясняет, почему на сценарии 01 наземный рычаг слабее орбитального.
    """

    report = placement_grid(full_constellation)
    best = report.best
    assert best is not None
    assert best.worst_availability == pytest.approx(0.9778, abs=0.002)

    per_latitude = [value for _, value in report.best_per_latitude()]
    assert max(per_latitude) - min(per_latitude) < 0.005


def test_a_broken_network_makes_the_place_decisive(link_range):
    """
    При дальности 2000 км место решает всё: 94.4 % на 70° против 65.8 % на 50°.

    И выбранный соображением Норильск даёт 95.8 %, то есть лучше любого узла сетки:
    перебор рекомендацию не улучшил, а подтвердил.
    """

    report = placement_grid(link_range)
    by_latitude = dict(report.best_per_latitude())

    assert by_latitude[70.0] == pytest.approx(0.944, abs=0.005)
    assert by_latitude[50.0] == pytest.approx(0.658, abs=0.005)
    assert by_latitude[70.0] - by_latitude[50.0] > 0.25

    assert evaluate_site(link_range, NORILSK) == pytest.approx(0.958, abs=0.005)
    assert evaluate_site(link_range, NORILSK) >= report.best.worst_availability


def test_the_search_runs_on_an_unfamiliar_scenario(judge_fixture):
    """Сценарий с двумя шлюзами и другой географией перебор тоже проходит."""

    report = placement_grid(judge_fixture, lat_range=(55.0, 75.0), lat_step=10.0, lon_step=90.0)
    assert len(report.points) == 12
    assert report.best is not None
    assert report.best.worst_availability >= report.baseline_worst_availability
