"""
Семейства разноса плоскостей и уход узла от сжатия Земли.

Оба вопроса про одно и то же — про то, насколько выданная конфигурация осмысленна и
насколько наша рекомендация переживёт время. Числа здесь закрепляются, чтобы на
защите их можно было называть.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.families import spacing_curve, supplied_spacing_deg
from cosmo_net.geometry.precession import (
    drift_over_horizon_deg,
    nodal_precession_deg_per_day,
)


@pytest.fixture(scope="module")
def curve(full_constellation):
    return spacing_curve(full_constellation)


def test_the_supplied_design_is_a_textbook_star(full_constellation, curve):
    """Выданный разнос 60° — это ровно 180°/3, то есть правило звезды без отклонений."""

    assert supplied_spacing_deg(full_constellation) == pytest.approx(60.0)
    assert curve.supplied_spacing_deg == pytest.approx(curve.star_spacing_deg)
    assert curve.supplied_family == "star"


def test_both_families_show_up_as_separate_peaks(curve):
    """
    Измерено: 99.3 % при разносе 66° и 92.2 % при 122°, разрыв семь пунктов.

    Два максимума стоят рядом с двумя предписанными разносами — 60° и 120°, — то есть
    классификация семейств видна прямо в данных, а не привнесена извне.
    """

    star, delta = curve.star_best, curve.delta_best
    assert star is not None and delta is not None

    assert star.spacing_deg == pytest.approx(66.0, abs=2.0)
    assert star.worst_availability == pytest.approx(0.9931, abs=0.002)
    assert delta.spacing_deg == pytest.approx(122.0, abs=2.0)
    assert delta.worst_availability == pytest.approx(0.9222, abs=0.002)

    assert (star.worst_availability - delta.worst_availability) * 100 == pytest.approx(
        7.1, abs=0.3
    )


def test_the_measured_optimum_sits_near_the_textbook_rule(curve):
    """
    Максимум смещён от правила на 6°, и эти 6° стоят 2.6 пункта.

    Смещение не опровергает правило, а уточняет его: правило выведено для равномерного
    покрытия полярной шапки, а здесь покрывать надо три конкретных пункта и один шлюз,
    да ещё при наклонении 87°, а не 90°.
    """

    star = curve.star_best
    assert abs(star.spacing_deg - curve.star_spacing_deg) == pytest.approx(6.0, abs=2.0)

    at_rule = next(p for p in curve.points if p.spacing_deg == pytest.approx(60.0))
    assert (star.worst_availability - at_rule.worst_availability) * 100 == pytest.approx(
        2.6, abs=0.3
    )


def test_an_uneven_design_belongs_to_no_family(full_constellation):
    """Плоскости можно расставить как угодно, и тогда относить проект не к чему."""

    changed = full_constellation.model_copy(deep=True)
    changed.design.planes[1].raan_deg = 17.0
    assert supplied_spacing_deg(changed) is None


def test_the_j2_drift_is_far_below_the_search_resolution():
    """
    Уход узла — 0.391° в сутки, шаг перебора разносов — 5°, ширина максимума — около 10°.

    Значит за горизонт расчёта эффект больше чем в десять раз меньше того, что наш же
    поиск способен различить, и пренебречь им законно.
    """

    per_day = nodal_precession_deg_per_day(550.0, 87.0)
    assert per_day == pytest.approx(-0.391, abs=0.002)

    over_horizon = abs(drift_over_horizon_deg(550.0, 87.0, 86400.0))
    assert over_horizon < 5.0 / 10


def test_a_polar_orbit_does_not_precess():
    """На 90° множитель cos(i) обращается в ноль, и разворачивать плоскость нечему."""

    assert nodal_precession_deg_per_day(550.0, 90.0) == pytest.approx(0.0, abs=1e-12)


def test_retrograde_orbits_drift_the_other_way():
    """Знак идёт от cos(i): прямые орбиты уходят назад, обратные — вперёд."""

    assert nodal_precession_deg_per_day(550.0, 87.0) < 0
    assert nodal_precession_deg_per_day(550.0, 93.0) > 0


def test_equal_planes_keep_their_relative_spacing(full_constellation):
    """
    Все плоскости одной высоты и наклонения уходят одинаково, поэтому разнос сохраняется.

    Это и есть причина, по которой рекомендация про разнос живёт дольше суток: за год
    группировка развернётся на 143°, а расстояния между её плоскостями не изменятся.
    """

    environment = full_constellation.environment
    drifts = [
        drift_over_horizon_deg(environment.altitude_km, environment.inclination_deg, 365 * 86400)
        for _ in full_constellation.design.planes
    ]
    assert max(drifts) - min(drifts) == pytest.approx(0.0, abs=1e-12)
    assert abs(drifts[0]) == pytest.approx(142.7, abs=0.5)
