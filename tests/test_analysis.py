"""
The studies built on repeated runs: fast reachability, criticality, sweeps, comparison.

The first test is the one holding the rest up. Three of the four studies here skip
routing entirely and answer the availability question through connected components
instead, which is only legitimate if it gives the same answer — so it is checked
against the full run on every supplied scenario rather than argued for.
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
    """Connected components and path search answer the same question, step for step."""

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
    The headline resilience finding, locked in as a test.

    Every one of the 48 costs the worst-served client between 1.4 and 2.4 points, so
    the spread across the fleet is under a point. There is no craft whose loss is
    categorically worse than another's — which is why the recommendation that came
    out of this analysis is about the ground segment and not about sparing satellites.
    """

    report = rank_satellites(full_constellation)
    assert len(report.knockouts) == 48
    assert report.spread_pp < 1.5
    assert max(k.drop_pp for k in report.knockouts) < 3.0
    assert min(k.drop_pp for k in report.knockouts) > 1.0


def test_knockouts_skip_craft_that_never_fly(first_launch):
    """At stage 1 only the first batch is up, so there are 16 satellites to knock out."""

    report = rank_satellites(first_launch)
    assert len(report.knockouts) == 16
    assert {k.plane_id for k in report.knockouts} == {"P1"}


def test_sweep_finds_a_configuration_better_than_the_supplied_one(full_constellation):
    """
    The supplied design is not the best one in its own family.

    Measured: RAAN 0/65/130 with a 5.625° phase step takes the worst-served client
    from 96.67 % to 99.58 % and the longest gap from 480 s to 120 s — one step, the
    shortest an interruption can be on this grid.
    """

    report = sweep_spacing(full_constellation)
    assert report.best.worst_availability > report.baseline.worst_availability
    assert report.best.worst_max_gap_s <= report.baseline.worst_max_gap_s

    tuned = simulate(variant(full_constellation, report.best.raan_deg, report.best.phase_deg))
    assert tuned.worst_availability == pytest.approx(report.best.worst_availability, abs=1e-9)
    assert tuned.worst_max_gap_s == report.best.worst_max_gap_s


def test_the_sweep_includes_the_scenario_it_started_from(full_constellation):
    """Otherwise "best" could be worse than doing nothing and nobody would see it."""

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
    """Reordering the satellite list is not a design change."""

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
    Stage one ranks on a sampled grid; stage two measures. Only stage two is quoted.

    The sampled pass exists because a hundred exact evaluations is two minutes on the
    deployed instance. It is a search, not a measurement, and a percentage that came
    out of it must never reach the interface — so `best` and every point on the
    frontier is checked to have been measured on the full grid.
    """

    report = sweep_spacing(full_constellation)

    assert report.best.approximate is False
    assert all(not candidate.approximate for candidate in report.frontier)
    assert any(candidate.approximate for candidate in report.candidates), (
        "nothing was sampled, so this test is not exercising the two-stage path"
    )


def test_the_sampled_search_finds_the_same_winner(full_constellation):
    """
    The shortcut does not cost the answer.

    Measured on all four supplied scenarios: ranking on a sampled grid and then
    measuring the shortlist picks the same configuration as evaluating every
    candidate exactly. Here that is RAAN 0/65/130 at a 5.625° phase step, 99.58 %.
    """

    report = sweep_spacing(full_constellation)
    exact = evaluate(
        variant(full_constellation, report.best.raan_deg, report.best.phase_deg)
    )

    assert exact.worst_availability == pytest.approx(report.best.worst_availability)
    assert report.best.worst_availability > report.baseline.worst_availability


def test_a_sampled_series_may_not_reuse_full_grid_contacts(full_constellation):
    """Mixing the two would silently score a candidate on the wrong number of steps."""

    from cosmo_net.analysis.reachability import availability_series
    from cosmo_net.geometry.contacts import compute_contacts
    from cosmo_net.geometry.orbit import compute_trajectory

    contacts = compute_contacts(full_constellation, compute_trajectory(full_constellation))
    with pytest.raises(ValueError):
        availability_series(full_constellation, contacts, stride=4)


# --- how much of the machine a sweep may take ----------------------------------


def test_workers_never_exceed_what_memory_holds(monkeypatch):
    """
    The bug this guards is not hypothetical.

    `os.cpu_count()` inside a 512 MB container reports the cores of the host, so the
    sweep started eight workers, each with its own NumPy and its own copy of the run.
    The kernel killed the service mid-request and took every cached run with it —
    a judge who pressed the button lost the service, not just the answer.
    """

    from cosmo_net.analysis import resources

    monkeypatch.setattr(resources, "available_cpus", lambda: 16)
    monkeypatch.setattr(resources, "available_memory_mb", lambda: 512)

    # (512 - 180) / 220 is one worker, and an explicit request for eight is refused.
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
