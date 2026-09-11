"""
Показатели, которые определяет кейс, и измеренная базовая линия, которую они обязаны воспроизводить.

Тест базовой линии внизу — это замок от регрессии, а не утверждение, что эти числа
хорошие. Получены они независимой реализацией, написанной до появления этого пакета:
чистый Python, ни строчки общего кода, с перекрёстной проверкой двумя разными
выводами угла места и перекрытия Землёй. Совпадение двух реализаций на двенадцати
числах — это и есть доказательство, что модель верна; а тест не даёт поздней правке
незаметно их сдвинуть.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.metrics import collect_gaps
from cosmo_net.analysis.simulate import simulate
from cosmo_net.routing.diagnose import Outage

GAP = Outage.NO_CLIENT_CONTACT
OK = Outage.NONE


def test_a_single_missed_step_lasts_one_step():
    """Перерыв измеряется промежутком, который он занимает, а не расстоянием между его концами."""

    gaps = collect_gaps([OK, GAP, OK], [0, 120, 240], 120)
    assert len(gaps) == 1
    assert (gaps[0].start_s, gaps[0].end_s, gaps[0].duration_s) == (120, 240, 120)
    assert gaps[0].steps == 1


def test_gaps_touching_the_horizon_are_flagged():
    """
    Настоящая длительность неизвестна: обрезали сетку, а не связь.

    Кейс просит показывать такие перерывы отдельно от остальных, поэтому помечаются
    и начальный, и конечный, а `max_interior_gap_s` их не учитывает.
    """

    gaps = collect_gaps([GAP, GAP, OK, GAP], [0, 120, 240, 360], 120)
    assert [g.at_horizon_edge for g in gaps] == [True, True]

    interior = collect_gaps([OK, GAP, OK], [0, 120, 240], 120)
    assert interior[0].at_horizon_edge is False


def test_a_gap_records_every_cause_it_passed_through():
    causes = [OK, Outage.NO_CLIENT_CONTACT, Outage.NETWORK_SPLIT, Outage.NETWORK_SPLIT, OK]
    gap = collect_gaps(causes, [0, 120, 240, 360, 480], 120)[0]
    assert gap.causes == {"no_client_contact": 1, "network_split": 2}
    assert gap.cause is Outage.NETWORK_SPLIT
    assert gap.duration_s == 360


def test_shares_are_counts_of_steps(full_constellation):
    result = simulate(full_constellation)
    for metrics in result.metrics.values():
        assert metrics.steps == 720
        assert metrics.availability_share == pytest.approx(metrics.routed_steps / 720)
        assert metrics.availability_share <= metrics.visibility_share


def test_max_gap_is_the_longest_run_times_the_step(full_constellation):
    result = simulate(full_constellation)
    for metrics in result.metrics.values():
        longest = max((g.steps for g in metrics.gaps), default=0)
        assert metrics.max_gap_s == longest * 120


# Доступность по каждому клиенту и худший максимальный перерыв, измеренные
# 2026-09-11 независимой реализацией на чистом Python и воспроизведённые этим
# пакетом с точностью до десятой доли процента. Сценарий 01 — единственный из
# четырёх, кто в своей базовой конфигурации дотягивает до цели 90 %.
BASELINE = {
    "full_constellation": ({"C65": 96.7, "C70": 98.8, "C72": 98.9}, 480),
    "first_launch": ({"C65": 27.2, "C70": 15.8, "C72": 12.6}, 47_760),
    "satellite_outages": ({"C65": 79.3, "C70": 80.8, "C72": 82.5}, 1_440),
    "link_range": ({"C65": 77.5, "C70": 62.2, "C72": 65.1}, 10_680),
}


@pytest.mark.parametrize("fixture_name", sorted(BASELINE))
def test_reproduces_the_measured_baseline(request, fixture_name):
    scenario = request.getfixturevalue(fixture_name)
    expected, expected_gap = BASELINE[fixture_name]

    result = simulate(scenario)
    for client_id, share in expected.items():
        assert result.metrics[client_id].availability_share * 100 == pytest.approx(
            share, abs=0.05
        ), client_id
    assert result.worst_max_gap_s == expected_gap


def test_only_the_full_constellation_meets_the_target(
    full_constellation, first_launch, satellite_outages, link_range
):
    """Главный вывод: три из четырёх выданных сценариев не дотягивают до 90 %."""

    assert simulate(full_constellation).meets_target
    assert not simulate(first_launch).meets_target
    assert not simulate(satellite_outages).meets_target
    assert not simulate(link_range).meets_target
