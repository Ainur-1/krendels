"""
Задержка распространения: то единственное, в чём стратегии маршрутизации различаются.

На доступность выбор стратегии не влияет — это проверено отдельным тестом и сказано
в README прямым текстом. Но тогда надо показать, на что он влияет, иначе три
стратегии выглядят украшением. Влияет он на задержку, и цена у выбора немаленькая.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.simulate import simulate
from cosmo_net.config import SPEED_OF_LIGHT_KM_S
from cosmo_net.routing.strategies import Strategy


@pytest.fixture(scope="module")
def runs(full_constellation):
    return {strategy: simulate(full_constellation, strategy) for strategy in Strategy}


def test_rtt_is_the_route_length_there_and_back(runs):
    """Никакой модели задержки здесь нет: только длина трассы и скорость света."""

    for result in runs.values():
        for metrics in result.metrics.values():
            expected = 2 * metrics.mean_route_length_km / SPEED_OF_LIGHT_KM_S * 1000
            assert metrics.mean_rtt_ms == pytest.approx(expected)


def test_strategies_agree_on_availability_and_differ_on_delay(runs):
    """
    Одна и та же доступность, разная задержка — вот в чём смысл трёх стратегий.

    Измерено на полной группировке: 26.5 мс у кратчайшей трассы, 27.5 мс у минимума
    переходов и 48.6 мс у максимального запаса.
    """

    availability = {result.worst_availability for result in runs.values()}
    assert len(availability) == 1

    assert runs[Strategy.MIN_DISTANCE].mean_rtt_ms == pytest.approx(26.5, abs=0.3)
    assert runs[Strategy.MIN_HOPS].mean_rtt_ms == pytest.approx(27.5, abs=0.3)
    assert runs[Strategy.MAX_MARGIN].mean_rtt_ms == pytest.approx(48.6, abs=0.5)


def test_the_shortest_route_really_is_the_shortest(runs):
    """Стратегия кратчайшей трассы обязана давать наименьшую задержку из трёх."""

    best = min(result.mean_rtt_ms for result in runs.values())
    assert runs[Strategy.MIN_DISTANCE].mean_rtt_ms == pytest.approx(best)


def test_link_margin_is_paid_for_with_delay(runs):
    """
    Запас на линиях покупается задержкой, и в худшем случае — пятикратной.

    Максимальная задержка за сутки: 67 мс у кратчайшей трассы против 337 мс у
    максимального запаса. Это и есть содержание выбора стратегии, а не доступность.
    """

    short = runs[Strategy.MIN_DISTANCE]
    safe = runs[Strategy.MAX_MARGIN]

    assert short.max_rtt_ms == pytest.approx(66.8, abs=1.0)
    assert safe.max_rtt_ms == pytest.approx(337.1, abs=2.0)
    assert safe.max_rtt_ms / short.max_rtt_ms > 4.5
    assert safe.mean_rtt_ms / short.mean_rtt_ms > 1.7


def test_the_delay_is_comparable_to_terrestrial_fibre(runs):
    """
    Средние 27 мс — это порядок наземной линии, которой в этих местах нет.

    Расстояние Мурманск — Анадырь по поверхности около 5000 км, и даже по прямому
    волокну со скоростью света в стекле это порядка 50 мс туда-обратно. Спутниковая
    трасса даёт меньше, и это стоит сказать: ради этого межспутниковые связи и нужны.
    """

    fibre_rtt_ms = 2 * 5000 / (SPEED_OF_LIGHT_KM_S * 2 / 3) * 1000
    assert runs[Strategy.MIN_DISTANCE].mean_rtt_ms < fibre_rtt_ms
    assert fibre_rtt_ms == pytest.approx(50.0, abs=1.0)
