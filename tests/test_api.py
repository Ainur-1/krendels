"""
The HTTP surface: the three ways to name a design, the run payload, and the export format.

The export test is the one with a number in it. The case specifies one record per
(step, client) pair with an empty path where no route existed, which for a default
run is 720 × 3 = 2160 records — not 2160 minus the ones that failed. A reader has
to be able to count availability straight out of the file, and that only works if
the failures are in it.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from cosmo_net.config import RESULT_SCHEMA_VERSION, SCENARIOS_DIR, STATIC_DIR
from cosmo_net.serving import api as api_module
from cosmo_net.serving.store import RunCache, VariantStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A service with its own empty database, so tests never see each other's variants."""

    monkeypatch.setattr(api_module, "variants", VariantStore(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(api_module, "runs", RunCache())
    with TestClient(api_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads((SCENARIOS_DIR / "01_full_constellation.json").read_text(encoding="utf-8"))


def test_health_reports_what_is_available(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert len(body["bundled_scenarios"]) == 4


def test_scenarios_are_summarised_without_their_bodies(client):
    listing = client.get("/api/scenarios").json()
    assert {s["id"] for s in listing} == {
        "01_full_constellation",
        "02_first_launch",
        "03_satellite_outages",
        "04_link_range",
    }
    first = listing[0]
    assert first["satellites"] == 48
    assert first["steps"] == 720
    assert "design" not in first


def test_an_unknown_scenario_is_a_404(client):
    assert client.get("/api/scenarios/nope").status_code == 404


def test_validation_reports_the_field_at_fault(client, raw):
    broken = copy.deepcopy(raw)
    broken["design"]["satellites"][7]["plane_id"] = "P9"

    response = client.post("/api/scenarios/validate", json=broken)
    assert response.status_code == 422

    body = response.json()
    assert body["detail"] == "scenario_invalid"
    assert body["errors"][0]["field"] == "design.satellites[7].plane_id"
    assert body["errors"][0]["code"] == "unknown_plane"


def test_validation_accepts_a_good_file(client, raw):
    body = client.post("/api/scenarios/validate", json=raw).json()
    assert body["valid"] is True
    assert body["summary"]["planes"] == 3


def test_a_run_carries_a_full_series_per_client(client):
    body = client.post("/api/runs", json={"bundled": "01_full_constellation"}).json()

    assert body["summary"]["worst_availability"] == pytest.approx(0.9667, abs=5e-4)
    assert len(body["times_s"]) == 720
    for series in body["clients"].values():
        assert len(series["reachable"]) == 720
        assert len(series["cause"]) == 720
        assert len(series["path"]) == 720
        # Where there is no route the path is empty rather than absent, so the
        # frontend can index by step without checking two things.
        for reachable, path in zip(series["reachable"], series["path"], strict=True):
            assert bool(path) == reachable


def test_a_run_can_be_fetched_again_by_id(client):
    created = client.post("/api/runs", json={"bundled": "02_first_launch"}).json()
    fetched = client.get(f"/api/runs/{created['run_id']}").json()
    assert fetched["summary"] == created["summary"]


def test_an_expired_run_says_so_rather_than_crashing(client):
    response = client.get("/api/runs/deadbeef0000")
    assert response.status_code == 404
    assert "recompute" in response.json()["detail"]


def test_a_snapshot_matches_the_scenario_it_came_from(client):
    run = client.post("/api/runs", json={"bundled": "01_full_constellation"}).json()
    snapshot = client.get(
        f"/api/runs/{run['run_id']}/snapshot", params={"t_s": 3600}
    ).json()

    assert snapshot["t_s"] == 3600
    assert len(snapshot["satellites"]) == 48
    assert len(snapshot["ground_sites"]) == 4
    assert all(link["distance_km"] < 3000 for link in snapshot["links"])


def test_the_export_has_one_record_per_step_and_client(client):
    run = client.post("/api/runs", json={"bundled": "03_satellite_outages"}).json()
    export = client.get(f"/api/runs/{run['run_id']}/export").json()

    assert export["schema_version"] == RESULT_SCHEMA_VERSION
    assert len(export["routes"]) == 720 * 3

    pairs = {(record["t_s"], record["client_id"]) for record in export["routes"]}
    assert len(pairs) == 720 * 3

    missing = [r for r in export["routes"] if not r["path"]]
    assert missing, "scenario 03 has outages; some steps must have no route"
    assert all(r["path"][0] == r["client_id"] for r in export["routes"] if r["path"])


def test_the_export_carries_the_scenario_that_was_actually_run(client, raw):
    """`effective_scenario` has to include the user's edits, or the run is not reproducible."""

    edited = copy.deepcopy(raw)
    edited["design"]["planes"][1]["raan_deg"] = 65.0

    run = client.post("/api/runs", json={"scenario": edited}).json()
    export = client.get(f"/api/runs/{run['run_id']}/export").json()
    assert export["effective_scenario"]["design"]["planes"][1]["raan_deg"] == 65.0


def test_the_export_is_offered_as_a_file(client):
    run = client.post("/api/runs", json={"bundled": "01_full_constellation"}).json()
    response = client.get(f"/api/runs/{run['run_id']}/export")
    assert "attachment" in response.headers["content-disposition"]


def test_a_variant_can_be_saved_listed_fetched_and_deleted(client, raw):
    created = client.post(
        "/api/variants", json={"label": "Повёрнутые плоскости", "scenario": raw}
    )
    assert created.status_code == 201
    identifier = created.json()["id"]

    listing = client.get("/api/variants").json()
    assert [v["id"] for v in listing] == [identifier]
    assert listing[0]["label"] == "Повёрнутые плоскости"

    fetched = client.get(f"/api/variants/{identifier}").json()
    assert fetched["scenario"] == raw

    assert client.delete(f"/api/variants/{identifier}").status_code == 204
    assert client.get("/api/variants").json() == []


def test_running_with_save_as_stores_the_design_it_ran(client, raw):
    body = client.post("/api/runs", json={"scenario": raw, "save_as": "База"}).json()
    assert body["variant_id"]
    assert client.get(f"/api/variants/{body['variant_id']}").json()["scenario"] == raw


def test_a_run_can_name_a_saved_variant(client, raw):
    saved = client.post("/api/variants", json={"label": "Сохранённый", "scenario": raw}).json()
    body = client.post("/api/runs", json={"variant_id": saved["id"]}).json()
    assert body["summary"]["worst_availability"] > 0


def test_naming_nothing_is_a_422(client):
    assert client.post("/api/runs", json={}).status_code == 422


def test_comparison_reports_both_the_numbers_and_the_edits(client, raw):
    tuned = copy.deepcopy(raw)
    tuned["design"]["planes"][1]["raan_deg"] = 65.0
    tuned["design"]["planes"][2]["raan_deg"] = 130.0

    first = client.post("/api/runs", json={"scenario": raw}).json()["run_id"]
    second = client.post("/api/runs", json={"scenario": tuned}).json()["run_id"]

    table = client.post(
        "/api/compare", json={"run_ids": [first, second], "labels": ["база", "повёрнуто"]}
    ).json()

    assert table["comparable"] is True
    assert [r["label"] for r in table["runs"]] == ["база", "повёрнуто"]
    assert {c["path"] for c in table["changes"]} == {
        "design.planes[P2].raan_deg",
        "design.planes[P3].raan_deg",
    }


def test_comparison_needs_at_least_two_runs(client):
    assert client.post("/api/compare", json={"run_ids": ["x"]}).status_code == 422


def test_criticality_ranks_every_satellite(client):
    body = client.post(
        "/api/analysis/criticality", json={"bundled": "01_full_constellation"}
    ).json()
    assert len(body["knockouts"]) == 48
    drops = [k["drop_pp"] for k in body["knockouts"]]
    assert drops == sorted(drops, reverse=True)


def test_the_sweep_returns_a_frontier(client):
    body = client.post(
        "/api/analysis/sweep",
        json={"bundled": "01_full_constellation", "mode": "spacing", "workers": 1},
    ).json()

    assert body["best"]["worst_availability"] >= body["baseline"]["worst_availability"]
    assert body["frontier"]
    assert len(body["candidates"]) > 50


def test_the_root_page_works_in_both_states(client):
    """
    The bundle is gitignored, so the root route has two legitimate answers.

    On a machine where `npm run build` has run, `/` is the interface. On a fresh
    clone and in CI it is a page saying how to build it — which beats a bare 404,
    because a missing bundle is the normal state of a checkout, not a fault.
    """

    response = client.get("/")
    assert response.status_code == 200

    if STATIC_DIR.is_dir():
        assert '<div id="root">' in response.text
    else:
        assert "npm" in response.text
