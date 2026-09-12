"""
Внешняя граница сервиса: три способа назвать проект, ответ с прогоном и формат выгрузки.

Тест выгрузки — тот, в котором есть число. Кейс задаёт по одной записи на пару
«отсчёт — клиент», с пустым путём там, где маршрута не было, то есть для базового
прогона 720 × 3 = 2160 записей, а не 2160 минус неудавшиеся. Читатель должен уметь
посчитать доступность прямо из файла, а это работает, только если неудачи в нём есть.
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
    """Сервис со своей пустой базой, чтобы тесты не видели вариантов друг друга."""

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
        # Там, где маршрута нет, путь пустой, а не отсутствующий, чтобы интерфейс
        # мог обращаться по номеру отсчёта, не проверяя две вещи сразу.
        for reachable, path in zip(series["reachable"], series["path"], strict=True):
            assert bool(path) == reachable


def test_a_run_can_be_fetched_again_by_id(client):
    created = client.post("/api/runs", json={"bundled": "02_first_launch"}).json()
    fetched = client.get(f"/api/runs/{created['run_id']}").json()
    assert fetched["summary"] == created["summary"]


def test_an_expired_run_says_so_rather_than_crashing(client):
    response = client.get("/api/runs/deadbeef0000")
    assert response.status_code == 404
    assert "запустите расчёт заново" in response.json()["detail"]


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
    assert missing, "в сценарии 03 есть отказы: на части отсчётов маршрута быть не должно"
    assert all(r["path"][0] == r["client_id"] for r in export["routes"] if r["path"])


def test_the_export_carries_the_scenario_that_was_actually_run(client, raw):
    """В `effective_scenario` должны быть правки пользователя, иначе прогон невоспроизводим."""

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
    Сборка интерфейса не хранится в гите, поэтому у корня два законных ответа.

    На машине, где выполнили `npm run build`, `/` — это интерфейс. В свежей копии и в
    CI — страница с объяснением, как его собрать. Это лучше голого 404: отсутствие
    сборки — обычное состояние копии репозитория, а не неисправность.
    """

    response = client.get("/")
    assert response.status_code == 200

    if STATIC_DIR.is_dir():
        assert '<div id="root">' in response.text
    else:
        assert "npm" in response.text


def test_delivery_endpoint_returns_the_deadline_breakdown(client):
    """Разрез по допустимой задержке, с нулевой строкой, равной мгновенной доступности."""

    body = client.post("/api/analysis/delivery", json={"bundled": "04_link_range"}).json()
    shares = {row["deadline_s"]: row["share"] for row in body["worst_within"]}

    assert shares[0] == pytest.approx(0.622, abs=0.005)
    assert shares[900] == pytest.approx(1.0, abs=0.005)
    assert body["worst_max_latency_s"] == pytest.approx(840, abs=1)
    assert len(body["clients"]) == 3


def test_redundancy_endpoint_is_tied_to_a_run(client):
    """Шкалу раскрашивает тот же прогон, который показан, поэтому ручка привязана к нему."""

    run_id = client.post("/api/runs", json={"bundled": "01_full_constellation"}).json()["run_id"]
    body = client.get(f"/api/runs/{run_id}/redundancy").json()

    assert body["worst_single_path_share"] == pytest.approx(0.801, abs=0.005)
    assert len(body["times_s"]) == 720
    assert set(body["series"]) == {"C65", "C70", "C72"}
    assert all(len(series) == 720 for series in body["series"].values())

    assert client.get("/api/runs/нет-такого/redundancy").status_code == 404


def test_degradation_endpoint_answers_quickly(client):
    """У ручки своя, меньшая сетка: она живёт за кнопкой, а не в отчёте."""

    body = client.post(
        "/api/analysis/degradation",
        json={"bundled": "01_full_constellation", "max_failures": 4, "trials": 5},
    ).json()

    assert [point["failures"] for point in body["points"]] == [0, 1, 2, 3, 4]
    assert body["points"][0]["mean_worst_availability"] == pytest.approx(0.9667, abs=0.002)
    assert body["satellites_in_service"] == 48


def test_placement_endpoint_takes_its_own_grid(client):
    """Сетку задаёт запрос: полная нужна отчёту, интерфейсу хватает грубой."""

    body = client.post(
        "/api/analysis/placement",
        json={"bundled": "04_link_range", "lat_step_deg": 10, "lon_step_deg": 45},
    ).json()

    assert len(body["lat_deg"]) == 4 and len(body["lon_deg"]) == 8
    assert len(body["points"]) == 32
    assert body["best"]["worst_availability"] > body["baseline_worst_availability"]

    bad = client.post(
        "/api/analysis/placement",
        json={"bundled": "04_link_range", "lat_min_deg": 80, "lat_max_deg": 50},
    )
    assert bad.status_code == 422

    # Сетка по умолчанию прорежена вдвое против отчётной, но максимум находит тот же:
    # 90° кратно и пятнадцати градусам, и тридцати.
    default = client.post("/api/analysis/placement", json={"bundled": "04_link_range"}).json()
    assert len(default["points"]) == 96
    assert (default["best"]["lat_deg"], default["best"]["lon_deg"]) == (70.0, 90.0)


def test_families_endpoint_names_the_family_of_the_supplied_design(client):
    """Выданный проект — звезда, и ручка это говорит, а не оставляет читателю."""

    body = client.post("/api/analysis/families", json={"bundled": "01_full_constellation"}).json()

    assert body["supplied_family"] == "star"
    assert body["supplied_spacing_deg"] == pytest.approx(60.0)
    assert body["star_best"]["worst_availability"] > body["delta_best"]["worst_availability"]


def test_scenario_export_round_trips(client):
    """
    Выгруженный сценарий обязан приниматься обратно — этого требует кейс.

    Проверка не формальная: файл проходит через разбор и обратную сборку, и если бы
    сборка теряла поле или меняла его форму, обратная загрузка это поймала бы.
    """

    downloaded = client.post("/api/scenarios/export", json={"bundled": "01_full_constellation"})
    assert downloaded.status_code == 200
    assert "01_full_constellation.json" in downloaded.headers["content-disposition"]

    scenario = downloaded.json()
    assert scenario["schema_version"] == "cosmo-A-1.0"

    again = client.post("/api/scenarios/validate", json=scenario)
    assert again.status_code == 200 and again.json()["valid"]

    # И считается так же, как исходный: выгрузка не теряет ничего, что влияет на ответ.
    first = client.post("/api/runs", json={"bundled": "01_full_constellation"}).json()
    second = client.post("/api/runs", json={"scenario": scenario}).json()
    assert first["summary"]["worst_availability"] == second["summary"]["worst_availability"]


def test_an_edited_scenario_survives_the_round_trip(client, raw):
    """Правки пользователя должны доезжать до файла, иначе выгружать его незачем."""

    edited = copy.deepcopy(raw)
    edited["design"]["planes"][1]["raan_deg"] = 65.0
    edited["design"]["launch_stage"] = 2

    downloaded = client.post("/api/scenarios/export", json={"scenario": edited}).json()
    assert downloaded["design"]["planes"][1]["raan_deg"] == 65.0
    assert downloaded["design"]["launch_stage"] == 2
