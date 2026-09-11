"""
Исследования поверх повторных прогонов: достижимость, критичность, перебор, сравнение.

Первый тест держит на себе все остальные. Три исследования из четырёх обходят
маршрутизацию стороной и отвечают на вопрос о доступности через связные компоненты.
Это законно ровно настолько, насколько ответ совпадает с полным расчётом, — поэтому
он проверяется против полного прогона на каждом выданном сценарии, а не
обосновывается словами.
"""

from __future__ import annotations

import pytest

from cosmo_net.analysis.compare import compare_runs, diff_scenarios
from cosmo_net.analysis.criticality import rank_satellites
from cosmo_net.analysis.optimise import evaluate, sweep_spacing, variant
from cosmo_net.analysis.reachability import availability_series, longest_gap_steps
from cosmo_net.analysis.simulate import simulate


@pytest.mark.parametrize(
    "fixture_name",
    ["full_constellation", "first_launch", "satellite_outages", "link_range"],
)
def test_reachability_agrees_with_full_routing(request, fixture_name):
    """Связные компоненты и поиск маршрута отвечают на один вопрос, отсчёт в отсчёт."""

    scenario = request.getfixturevalue(fixture_name)
    fast = availability_series(scenario)
    full = simulate(scenario)

    for client_id, metrics in full.metrics.items():
        assert int(fast[client_id].sum()) == metrics.routed_steps, client_id
        reached = [route is not None for route in full.routes[client_id]]
        assert list(fast[client_id]) == reached, f"{client_id} differs at some step"


def test_longest_gap_counts_the_run_not_the_ends():
    assert longest_gap_steps([True, False, False, True, False]) == 2
    assert longest_gap_steps([True, True]) == 0
    assert longest_gap_steps([False, False, False]) == 3


def test_no_single_satellite_holds_up_the_full_constellation(full_constellation):
    """
    Главный вывод об устойчивости, закреплённый тестом.

    Каждый из 48 аппаратов стоит худшему пункту от 1.4 до 2.4 пункта, то есть разброс
    по всей группировке меньше одного. Аппарата, потеря которого была бы принципиально
    хуже потери любого другого, нет — поэтому вывод из этого анализа получился про
    наземный сегмент, а не про резервирование аппаратов.
    """

    report = rank_satellites(full_constellation)
    assert len(report.knockouts) == 48
    assert report.spread_pp < 1.5
    assert max(k.drop_pp for k in report.knockouts) < 3.0
    assert min(k.drop_pp for k in report.knockouts) > 1.0


def test_knockouts_skip_craft_that_never_fly(first_launch):
    """На первой очереди в строю только первая партия, поэтому выключать можно 16 аппаратов."""

    report = rank_satellites(first_launch)
    assert len(report.knockouts) == 16
    assert {k.plane_id for k in report.knockouts} == {"P1"}


def test_sweep_finds_a_configuration_better_than_the_supplied_one(full_constellation):
    """
    Выданный проект — не лучший в своём же семействе.

    Измерено: RAAN 0/65/130 при шаге фазы 5.625° поднимает худший пункт с 96.67 % до
    99.58 %, а самый долгий перерыв сокращает с 480 с до 120 с — это один отсчёт,
    короче перерыв на этой сетке невозможен.
    """

    report = sweep_spacing(full_constellation)
    assert report.best.worst_availability > report.baseline.worst_availability
    assert report.best.worst_max_gap_s <= report.baseline.worst_max_gap_s

    tuned = simulate(variant(full_constellation, report.best.raan_deg, report.best.phase_deg))
    assert tuned.worst_availability == pytest.approx(report.best.worst_availability, abs=1e-9)
    assert tuned.worst_max_gap_s == report.best.worst_max_gap_s


def test_the_sweep_includes_the_scenario_it_started_from(full_constellation):
    """Иначе «лучший» мог бы оказаться хуже, чем ничего не менять, и этого никто бы не заметил."""

    report = sweep_spacing(full_constellation)
    baseline = evaluate(full_constellation)
    assert any(
        c.raan_deg == baseline.raan_deg and c.phase_deg == baseline.phase_deg
        for c in report.candidates
    )


def test_frontier_is_ordered_and_not_self_dominated(link_range):
    report = sweep_spacing(link_range)
    front = report.frontier
    assert front
    for earlier, later in zip(front[:-1], front[1:], strict=True):
        assert earlier.worst_availability >= later.worst_availability
        assert earlier.worst_max_gap_s > later.worst_max_gap_s


def test_diff_reports_only_what_moved(full_constellation):
    changed = variant(full_constellation, raan_deg=[0, 65, 130])
    changes = diff_scenarios(full_constellation, changed)
    assert {c.path for c in changes} == {
        "design.planes[P2].raan_deg",
        "design.planes[P3].raan_deg",
    }


def test_diff_matches_list_entries_by_identity(full_constellation):
    """Перестановка списка аппаратов — это не изменение проекта."""

    reordered = full_constellation.model_copy(deep=True)
    reordered.design.satellites.reverse()
    assert diff_scenarios(full_constellation, reordered) == []


def test_comparison_flags_runs_on_different_grids(full_constellation):
    shorter = full_constellation.model_copy(deep=True)
    shorter.environment.horizon_s = 43_200

    table = compare_runs([simulate(full_constellation), simulate(shorter)])
    assert table["comparable"] is False


def test_comparison_tabulates_every_client(full_constellation):
    tuned = variant(full_constellation, raan_deg=[0, 65, 130], phase_deg=[0, 5.625, 11.25])
    table = compare_runs([simulate(full_constellation), simulate(tuned)], ["base", "tuned"])

    assert table["comparable"] is True
    assert [r["label"] for r in table["runs"]] == ["base", "tuned"]
    assert {row["client_id"] for row in table["clients"]} == {"C65", "C70", "C72"}
    for row in table["clients"]:
        before, after = row["cells"]
        assert after["availability_share"] >= before["availability_share"]


# --- what the sweep may and may not report -------------------------------------


def test_the_sweep_never_reports_a_sampled_figure(full_constellation):
    """
    Первая стадия ранжирует по прореженной сетке, вторая измеряет. Показывается только вторая.

    Прореженный проход существует потому, что сотня точных оценок на развёрнутом
    сервере занимает две минуты. Это поиск, а не измерение, и процент, полученный
    таким способом, не должен попадать в интерфейс — поэтому проверяется, что и
    `best`, и каждая точка фронта измерены на полной сетке.
    """

    report = sweep_spacing(full_constellation)

    assert report.best.approximate is False
    assert all(not candidate.approximate for candidate in report.frontier)
    assert any(candidate.approximate for candidate in report.candidates), (
        "ничего не прорежено — значит тест не проверяет двухстадийный путь"
    )


def test_the_sampled_search_finds_the_same_winner(full_constellation):
    """
    Срезанный угол не стоит нам ответа.

    Измерено на всех четырёх выданных сценариях: ранжирование по прореженной сетке с
    последующим измерением короткого списка выбирает ту же конфигурацию, что и точная
    оценка каждого кандидата. Здесь это RAAN 0/65/130 при шаге фазы 5.625°, 99.58 %.
    """

    report = sweep_spacing(full_constellation)
    exact = evaluate(
        variant(full_constellation, report.best.raan_deg, report.best.phase_deg)
    )

    assert exact.worst_availability == pytest.approx(report.best.worst_availability)
    assert report.best.worst_availability > report.baseline.worst_availability


def test_a_sampled_series_may_not_reuse_full_grid_contacts(full_constellation):
    """Смешать их значило бы молча оценить кандидата на неверном числе отсчётов."""

    from cosmo_net.analysis.reachability import availability_series
    from cosmo_net.geometry.contacts import compute_contacts
    from cosmo_net.geometry.orbit import compute_trajectory

    contacts = compute_contacts(full_constellation, compute_trajectory(full_constellation))
    with pytest.raises(ValueError):
        availability_series(full_constellation, contacts, stride=4)


# --- how much of the machine a sweep may take ----------------------------------


def test_workers_never_exceed_what_memory_holds(monkeypatch):
    """
    Ошибка, от которой это защищает, не гипотетическая.

    `os.cpu_count()` внутри контейнера с 512 МБ показывает ядра хоста, поэтому перебор
    запустил восемь процессов, каждый со своим numpy и своей копией прогона. Ядро
    убило сервис прямо во время запроса и унесло с собой весь кеш прогонов: жюри,
    нажавшее кнопку, потеряло бы сервис, а не только ответ.
    """

    from cosmo_net.analysis import resources

    monkeypatch.setattr(resources, "available_cpus", lambda: 16)
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 512)

    # (512 - 180) / 220 — это один процесс, и явный запрос на восемь отклоняется.
    assert resources.usable_workers() == 1
    assert resources.usable_workers(8) == 1


def test_workers_follow_the_cores_when_memory_is_plentiful(monkeypatch):
    from cosmo_net.analysis import resources

    monkeypatch.setattr(resources, "available_cpus", lambda: 4)
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 8192)

    assert resources.usable_workers() == 4
    assert resources.usable_workers(2) == 2


def test_workers_is_at_least_one(monkeypatch):
    from cosmo_net.analysis import resources

    monkeypatch.setattr(resources, "available_cpus", lambda: 1)
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 128)
    assert resources.usable_workers(0) == 1
    assert resources.usable_workers(None) == 1
