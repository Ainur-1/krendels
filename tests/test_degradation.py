"""
Кривая деградации: сколько произвольных отказов проект переносит.

Кроме закрепления измеренных чисел здесь есть проверка на предсказательную силу.
Кривая строится на отказах, которые длятся все сутки; сценарий 03 — это отказы,
начинающиеся на шестом часу, то есть случай, которого кривая не видела. Если она
верна, смесь двух её точек обязана дать измеренный результат сценария 03.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.degradation import degradation_curve
from cosmo_net.analysis.reachability import worst_availability


@pytest.fixture(scope="module")
def curve(full_constellation):
    return degradation_curve(full_constellation)


def test_the_constellation_tolerates_two_random_failures(curve):
    """Измерено: два отказа держит всегда, на трёх балансирует, на четырёх теряет цель."""

    assert curve.tolerated_failures == 2
    by_count = {p.failures: p for p in curve.points}
    assert by_count[2].meets_target_share == 1.0
    assert 0.5 < by_count[3].meets_target_share < 1.0
    assert by_count[4].meets_target_share == 0.0


def test_degradation_is_gradual_and_almost_linear(curve):
    """
    Падение — 2.3 пункта за аппарат, и оно почти прямая: отклонение меньше 0.6 пункта.

    Это третье независимое подтверждение того, что незаменимого аппарата нет. Первое
    дало выбивание по одному, второе — мгновенный запас маршрутов, третье здесь: в
    решающем диапазоне до четырёх отказов важно почти только их количество, а не то,
    какие именно аппараты потеряны.
    """

    assert curve.slope_pp_per_satellite == pytest.approx(2.30, abs=0.1)
    assert curve.linear_fit_error_pp < 0.7

    by_count = {p.failures: p for p in curve.points}
    # Один отказ стоит от 1.5 до 2.4 пункта — тот же разброс, что дало выбивание
    # аппаратов по одному, полученный совсем другим способом.
    assert by_count[1].spread_pp < 1.0
    assert max(by_count[k].spread_pp for k in (1, 2, 3, 4)) < 2.5

    # Дальше от базовой линии выбор набора начинает значить больше: на двенадцати
    # отказах разброс уже 5 пунктов. Это не противоречие, а естественный рост.
    assert by_count[12].spread_pp > 4.0


def test_the_curve_predicts_the_supplied_outage_scenario(curve, satellite_outages):
    """
    Проверка на том, чего кривая не видела: сценарий 03 предсказан с ошибкой полпункта.

    Десять отказов начинаются на 21 600 с из 86 400, значит четверть горизонта
    группировка цела. Смесь точек кривой даёт 79.8 %, измерение — 79.3 %.
    """

    intact = 21600 / 86400
    predicted = curve.predicted_availability(10, intact_share=intact)
    measured = worst_availability(satellite_outages)

    assert predicted == pytest.approx(0.798, abs=0.005)
    assert measured == pytest.approx(0.793, abs=0.005)
    assert abs(predicted - measured) < 0.01


def test_the_curve_is_reproducible(full_constellation):
    """Одно зерно — одни числа. Иначе на защите нельзя было бы назвать ни одного."""

    a = degradation_curve(full_constellation, max_failures=3, trials=5)
    b = degradation_curve(full_constellation, max_failures=3, trials=5)
    assert a.to_dict() == b.to_dict()


def test_only_satellites_in_service_can_fail(first_launch):
    """На первой очереди в строю одна плоскость, и выбивать можно только её аппараты."""

    curve = degradation_curve(first_launch, max_failures=2, trials=3)
    assert curve.satellites_in_service == 16
    # Каждый отказ обязан что-то менять: если бы в набор попадали незапущенные
    # аппараты, часть прогонов повторяла бы базовую линию.
    baseline = curve.points[0].mean_worst_availability
    assert curve.points[1].best_worst_availability < baseline


def test_the_curve_stops_at_the_satellites_that_exist(first_launch):
    """Выбить больше аппаратов, чем в строю, нельзя: кривая обрывается там, где нечего терять."""

    curve = degradation_curve(first_launch, max_failures=40, trials=2)
    assert curve.satellites_in_service == 16
    assert [p.failures for p in curve.points] == list(range(17))
    assert curve.points[-1].mean_worst_availability == 0.0
