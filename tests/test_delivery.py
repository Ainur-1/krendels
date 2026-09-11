"""
Доставка с допустимой задержкой: проверка на согласованность и на измеренные числа.

Главный тест здесь — первый. Доля отсчётов с нулевой задержкой это ровно мгновенная
доступность, посчитанная совсем другим способом: один обратный проход по сетке против
прямой проверки связности на каждом отсчёте. Если бы в переносе была ошибка, она
почти наверняка сдвинула бы и это число.
"""

from __future__ import annotations

import numpy as np
import pytest

from cosmo_net.analysis.delivery import delivery_latency, delivery_report
from cosmo_net.analysis.reachability import availability_series


def test_zero_delay_equals_instant_availability(
    full_constellation, first_launch, satellite_outages, link_range, judge_fixture
):
    """Нулевая задержка — это и есть мгновенная доступность, посчитанная иначе."""

    for scenario in (
        full_constellation,
        first_launch,
        satellite_outages,
        link_range,
        judge_fixture,
    ):
        instant = availability_series(scenario)
        for client in delivery_latency(scenario):
            expected = float(instant[client.client_id].mean())
            assert client.share_within(0) == pytest.approx(expected, abs=1e-12), (
                scenario.meta.id,
                client.client_id,
            )


def test_latency_never_decreases_the_share(full_constellation, link_range):
    """Разрешить подождать дольше не может доставить меньше — иначе где-то ошибка знака."""

    for scenario in (full_constellation, link_range):
        for client in delivery_latency(scenario):
            shares = [client.share_within(d) for d in (0, 300, 900, 1800, 3600)]
            assert shares == sorted(shares), (scenario.meta.id, client.client_id, shares)


def test_latency_is_never_negative(full_constellation):
    """Данные нельзя доставить раньше, чем они появились."""

    for client in delivery_latency(full_constellation):
        finite = client.latency_s[np.isfinite(client.latency_s)]
        assert (finite >= 0).all()


def test_fifteen_minutes_rescue_two_scenarios(satellite_outages, link_range):
    """
    Измерено: сценарии 03 и 04 провалены только для трафика реального времени.

    При нулевой задержке худший пункт даёт 79.3 % и 62.2 %, то есть цель 90 % не
    достигнута ни там, ни там. Стоит разрешить пятнадцать минут — и оба дают почти
    сто процентов, а самая долгая доставка за сутки занимает 20 и 14 минут.
    """

    outages = delivery_report(satellite_outages)
    assert outages.worst_share_within(0) == pytest.approx(0.793, abs=0.005)
    assert outages.worst_share_within(300) == pytest.approx(0.904, abs=0.005)
    assert outages.worst_share_within(900) == pytest.approx(0.986, abs=0.005)
    assert outages.worst_share_within(1800) == pytest.approx(1.0, abs=0.005)
    assert outages.worst_max_latency_s == pytest.approx(1200, abs=1)

    ranges = delivery_report(link_range)
    assert ranges.worst_share_within(0) == pytest.approx(0.622, abs=0.005)
    assert ranges.worst_share_within(300) == pytest.approx(0.943, abs=0.005)
    assert ranges.worst_share_within(900) == pytest.approx(1.0, abs=0.005)
    assert ranges.worst_max_latency_s == pytest.approx(840, abs=1)


def test_waiting_does_not_rescue_the_first_launch(first_launch):
    """
    Одна плоскость не работает и с ожиданием: за два часа худший пункт добирается до 38 %.

    Это важно сказать отдельно, иначе допустимая задержка выглядит средством от всего.
    """

    report = delivery_report(first_launch)
    assert report.worst_share_within(7200) < 0.40
    assert max(c.undelivered_share for c in report.clients) > 0.10


def test_the_full_constellation_delivers_everything_within_ten_minutes(full_constellation):
    """На полной группировке максимальная доставка за сутки — восемь минут."""

    report = delivery_report(full_constellation)
    assert report.worst_max_latency_s == pytest.approx(480, abs=1)
    assert report.worst_share_within(900) == pytest.approx(1.0, abs=1e-12)
    assert all(c.undelivered_share == 0.0 for c in report.clients)
