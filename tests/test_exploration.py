"""
Находки разведочного анализа, закреплённые числами.

Каждый тест здесь соответствует одному графику в `reports/figures/` и одному
утверждению в `docs/findings.md`. Смысл в том, чтобы утверждение про входные данные
нельзя было сдвинуть незаметно: если кто-то поменяет геометрию или сценарии, упадёт
не график — упадёт тест, и станет видно, какое именно наблюдение перестало быть верным.
"""

from __future__ import annotations

import numpy as np
import pytest

from cosmo_net.analysis.exploration import (
    contact_durations_s,
    explore,
    footprint_radius_km,
    great_circle_km,
    ring_geometry,
    site_geometry,
)
from cosmo_net.analysis.reachability import availability_series


def test_launch_batches_coincide_with_planes(full_constellation):
    """
    Наблюдение, из которого следует весь вывод про первую очередь.

    В выданных файлах очередь запуска и плоскость — это одно и то же: очередь 1 это
    вся P1, очередь 2 это вся P2, очередь 3 это вся P3. Значит первая очередь — одна
    плоскость, и разносить между плоскостями на ней попросту нечего.
    """

    found = explore(full_constellation)
    assert found.batches_match_planes
    assert [p.batches for p in found.planes] == [[1], [2], [3]]
    assert all(p.satellites == 16 for p in found.planes)


def test_the_judge_fixture_does_not_have_that_property(judge_fixture):
    """Свойством формата это не является, и проверочный сценарий его нарушает."""

    assert not explore(judge_fixture).batches_match_planes


def test_the_in_plane_ring_barely_closes_at_3000_km(full_constellation):
    """
    Соседи в плоскости разнесены на 2700 км при пределе 3000 км: запас всего 11 %.

    Это объясняет, почему сценарий 04 разрушает сеть, а не просто ухудшает её.
    """

    ring = ring_geometry(full_constellation)
    assert ring.satellites_per_plane == 16
    assert ring.spacing_deg == pytest.approx(22.5)
    assert ring.chord_km == pytest.approx(2700, abs=5)
    assert ring.closes
    assert ring.margin_km == pytest.approx(300, abs=5)
    assert ring.margin_km / ring.chord_km == pytest.approx(0.11, abs=0.01)


def test_the_ring_does_not_close_at_2000_km(link_range):
    """
    При 2000 км внутриплоскостных связей не остаётся ни одной.

    Чтобы кольцо замкнулось при такой дальности, на плоскость нужно не меньше 22
    аппаратов вместо шестнадцати.
    """

    ring = ring_geometry(link_range)
    assert not ring.closes
    assert ring.margin_km == pytest.approx(-700, abs=5)
    assert ring.satellites_needed == 22


def test_the_visibility_footprint(full_constellation):
    """При 550 км и пороге 10° аппарат виден из круга радиусом около 1666 км."""

    env = full_constellation.environment
    radius = footprint_radius_km(env.altitude_km, env.min_elevation_deg)
    assert radius == pytest.approx(1666, abs=10)


def test_geography_predicts_dependence_on_the_network(full_constellation):
    """
    Расстояние до шлюза в долях зоны видимости — это и есть мера нужды в транзите.

    Измерено: C65 стоит на 0.37 предела и обслуживается одним аппаратом на 77 %
    шагов, C70 на 0.64 и 45 %, C72 на 0.97 и 2 %. То есть геометрия входных данных
    предсказывает, насколько пункт зависит от межспутниковой сети.
    """

    ratios = {s.client_id: s.bridging_ratio for s in site_geometry(full_constellation)}
    assert ratios["C65"] == pytest.approx(0.37, abs=0.02)
    assert ratios["C70"] == pytest.approx(0.64, abs=0.02)
    assert ratios["C72"] == pytest.approx(0.97, abs=0.02)

    # Порядок строгий: дальше от шлюза — сильнее зависимость.
    assert ratios["C65"] < ratios["C70"] < ratios["C72"]


def test_great_circle_matches_the_gateway_distances(full_constellation):
    gateway = full_constellation.gateways[0]
    by_id = {c.id: c for c in full_constellation.clients}
    assert great_circle_km(gateway, by_id["C65"]) == pytest.approx(1239, abs=15)
    assert great_circle_km(gateway, by_id["C72"]) == pytest.approx(3228, abs=15)


def test_coverage_is_thin(full_constellation):
    """
    Над пунктом обычно один-два аппарата, и это объясняет анализ критичности.

    Когда в среднем видно 1.3–2.0 аппарата, потеря любого стоит примерно одинаково —
    ровно то, что показало выбивание аппаратов по одному.
    """

    counts = explore(full_constellation).visible_per_site
    means = {k: float(v.mean()) for k, v in counts.items()}
    assert all(1.2 < m < 2.1 for m in means.values()), means
    assert max(int(v.max()) for v in counts.values()) <= 4


def test_failures_are_not_spread_evenly(satellite_outages):
    """Сценарий 03 — это не равномерная убыль: P1 теряет 5 из 16, P3 только 2."""

    found = explore(satellite_outages)
    assert found.failures_per_plane == {"P1": 5, "P2": 3, "P3": 2}
    assert len({(f.start_s, f.end_s) for f in satellite_outages.failures}) == 1


def test_some_contacts_are_shorter_than_the_prescribed_step(full_constellation):
    """
    Часть сеансов видимости короче 120 с, то есть предписанная сетка их не видит.

    Само по себе это не ошибка расчёта — так задан кейс, — но знать об этом нужно, и
    следующий тест показывает, что на ответ оно почти не влияет.
    """

    durations = contact_durations_s(full_constellation)
    everything = np.concatenate(list(durations.values()))
    short = float((everything < full_constellation.environment.step_s).mean())
    assert 0.03 < short < 0.12, short


def test_the_answer_does_not_depend_on_the_step(full_constellation):
    """
    Проверено измерением: от 120 с до 15 с доступность гуляет в пределах половины пункта.

    Предписанная сетка ответ не смещает, и сказать это на защите можно с числом в
    руках, а не на общих основаниях.
    """

    values: dict[str, list[float]] = {}
    for step in (120, 60, 30, 15):
        changed = full_constellation.model_copy(deep=True)
        changed.environment.step_s = step
        for client, reachable in availability_series(changed).items():
            values.setdefault(client, []).append(100 * float(reachable.mean()))

    spread = max(max(v) - min(v) for v in values.values())
    assert spread < 0.5, values
