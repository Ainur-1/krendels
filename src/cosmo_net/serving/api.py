"""
The HTTP service: load a scenario, change it, run it, compare runs, export the result.

    uv run uvicorn cosmo_net.serving.api:app --reload

Every endpoint that takes a design accepts it three ways — inline, by the
identifier of a saved variant, or by the name of one of the scenarios shipped with
the case — because the interface needs all three and resolving them in one place
keeps the rest of the file from caring which it got.

The compiled frontend is mounted at `/` when it is present. When it is not, the API
still works and `/` says so: a fresh clone that has not run `npm run build` should
be usable through the documented endpoints rather than answering 404 with no
explanation.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cosmo_net import __version__
from cosmo_net.analysis.compare import compare_runs
from cosmo_net.analysis.criticality import rank_satellites
from cosmo_net.analysis.optimise import refine, sweep_spacing
from cosmo_net.analysis.simulate import simulate, snapshot_at
from cosmo_net.config import STATIC_DIR
from cosmo_net.routing.strategies import Strategy
from cosmo_net.scenario.io import bundled_scenarios, dump_scenario
from cosmo_net.scenario.schema import Scenario, ScenarioInvalid, parse_scenario
from cosmo_net.serving.payloads import (
    error_payload,
    export_payload,
    run_payload,
    scenario_summary,
    snapshot_payload,
)
from cosmo_net.serving.store import RunCache, VariantStore

app = FastAPI(
    title="cosmo-net",
    version=__version__,
    summary="Проектирование устойчивой спутниковой группировки: расчёт, маршруты, сравнение.",
)

# Run payloads are a few hundred kilobytes of highly repetitive JSON — 720 causes
# drawn from five words, 2160 paths over 50 identifiers — so they compress to a
# fraction of that and the slider stays responsive over a slow link.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# The Vite dev server runs on another port during development. In the deployed
# container the frontend is served by this same app and no cross-origin request
# ever happens, so this costs nothing there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

variants = VariantStore()
runs = RunCache()


class DesignRef(BaseModel):
    """A design, however the caller has it to hand."""

    scenario: dict[str, Any] | None = None
    variant_id: str | None = None
    bundled: str | None = None


class RunRequest(DesignRef):
    strategy: Strategy = Strategy.MIN_HOPS
    save_as: str | None = Field(
        default=None, description="Save the design under this label before running it"
    )


class SaveRequest(DesignRef):
    label: str = Field(min_length=1, max_length=120)


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=6)
    labels: list[str] | None = None


class SweepRequest(DesignRef):
    mode: Literal["spacing", "refine"] = "spacing"
    workers: int | None = None


def resolve(reference: DesignRef) -> Scenario:
    """Turn any of the three ways of naming a design into a validated `Scenario`."""

    if reference.scenario is not None:
        return parse_scenario(reference.scenario)

    if reference.variant_id:
        found = variants.get(reference.variant_id)
        if found is None:
            raise HTTPException(404, f"No saved variant {reference.variant_id!r}")
        return found[1]

    if reference.bundled:
        for path in bundled_scenarios():
            if path.stem == reference.bundled:
                return parse_scenario(_read_json(path))
        raise HTTPException(404, f"No bundled scenario {reference.bundled!r}")

    raise HTTPException(422, "Provide one of: scenario, variant_id, bundled")


@app.exception_handler(ScenarioInvalid)
def invalid_scenario(request, exc: ScenarioInvalid) -> JSONResponse:
    """
    422 with every problem listed, not the first one.

    The case asks the service to say which field or object is wrong after a bad
    upload, and a user with three mistakes in a file should be told about three.
    """

    return JSONResponse(status_code=422, content=error_payload(exc.errors))


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Enough to tell a deploy whether the thing came up and whether the UI is in it."""

    return {
        "status": "ok",
        "version": __version__,
        "frontend_bundled": STATIC_DIR.is_dir(),
        "bundled_scenarios": [path.stem for path in bundled_scenarios()],
        "cached_runs": len(runs),
    }


@app.get("/api/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """The scenarios shipped with the case, summarised for a picker."""

    listing = []
    for path in bundled_scenarios():
        scenario = parse_scenario(_read_json(path))
        listing.append(scenario_summary(scenario, source=path.stem))
    return listing


@app.get("/api/scenarios/{name}")
def get_scenario(name: str) -> dict[str, Any]:
    for path in bundled_scenarios():
        if path.stem == name:
            return _read_json(path)
    raise HTTPException(404, f"No bundled scenario {name!r}")


@app.post("/api/scenarios/validate")
def validate_scenario(body: dict[str, Any]) -> dict[str, Any]:
    """Check a file without running it, so an upload can be rejected before the spinner."""

    scenario = parse_scenario(body)
    return {"valid": True, "summary": scenario_summary(scenario, source="upload")}


@app.get("/api/variants")
def list_variants() -> list[dict[str, Any]]:
    return variants.list()


@app.post("/api/variants", status_code=201)
def save_variant(body: SaveRequest) -> dict[str, Any]:
    scenario = resolve(body)
    identifier = variants.save(body.label, scenario)
    return {"id": identifier, "label": body.label, "summary": scenario_summary(scenario)}


@app.get("/api/variants/{variant_id}")
def get_variant(variant_id: str) -> dict[str, Any]:
    found = variants.get(variant_id)
    if found is None:
        raise HTTPException(404, f"No saved variant {variant_id!r}")
    label, scenario = found
    return {"id": variant_id, "label": label, "scenario": dump_scenario(scenario)}


@app.delete("/api/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: str) -> Response:
    if not variants.delete(variant_id):
        raise HTTPException(404, f"No saved variant {variant_id!r}")
    return Response(status_code=204)


@app.post("/api/runs")
def create_run(body: RunRequest) -> dict[str, Any]:
    """
    Run a design over its whole horizon and return everything needed to draw it.

    `save_as` stores the design first, so "change something and keep it" is one
    round trip rather than two and a variant can never be saved in a state that was
    never actually computed.
    """

    scenario = resolve(body)
    variant_id = variants.save(body.save_as, scenario) if body.save_as else None

    result = simulate(scenario, body.strategy)
    payload = run_payload(runs.put(result), result)
    payload["variant_id"] = variant_id
    return payload


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return run_payload(run_id, _require_run(run_id))


@app.get("/api/runs/{run_id}/snapshot")
def get_snapshot(run_id: str, t_s: float = 0.0) -> dict[str, Any]:
    """
    The network at one moment: positions, links, who sees whom.

    Recomputed rather than stored. A single step is about a millisecond, which is
    less than the request takes to arrive, and storing 26 MB of arrays per run so
    that a slider can read one row of them would be the wrong trade.
    """

    result = _require_run(run_id)
    return snapshot_payload(snapshot_at(result.scenario, t_s), result.scenario)


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: str) -> JSONResponse:
    """The run as `cosmo-A-result-1.0`, offered to the browser as a file."""

    result = _require_run(run_id)
    filename = f"{result.scenario.meta.id or 'result'}-{run_id}.json"
    return JSONResponse(
        content=export_payload(result),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/compare")
def compare(body: CompareRequest) -> dict[str, Any]:
    """Two or more runs side by side, with the parameters that differ between them."""

    results = [_require_run(run_id) for run_id in body.run_ids]
    if body.labels and len(body.labels) != len(results):
        raise HTTPException(422, "labels must match run_ids")
    return compare_runs(results, body.labels)


@app.post("/api/analysis/criticality")
def analyse_criticality(body: DesignRef) -> dict[str, Any]:
    """Rank every satellite by what its loss for a full day would cost."""

    return rank_satellites(resolve(body)).to_dict()


@app.post("/api/analysis/sweep")
def analyse_sweep(body: SweepRequest) -> dict[str, Any]:
    """
    Search the configuration space and return the whole field, not only the winner.

    The frontier is what the interface plots: availability against the longest
    interruption, with the designs that are not beaten on both at once.
    """

    scenario = resolve(body)
    workers = body.workers if body.workers is not None else min(8, os.cpu_count() or 1)
    report = (
        sweep_spacing(scenario, workers=workers)
        if body.mode == "spacing"
        else refine(scenario, workers=workers)
    )
    return report.to_dict()


def _require_run(run_id: str):
    result = runs.get(run_id)
    if result is None:
        raise HTTPException(
            404, f"Run {run_id!r} is not in the cache; run it again to recompute it"
        )
    return result


def _read_json(path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
else:

    @app.get("/", response_class=HTMLResponse)
    def no_frontend() -> str:
        """Said plainly rather than as a 404, because this is the normal state of a fresh clone."""

        return (
            "<!doctype html><meta charset='utf-8'>"
            "<title>cosmo-net</title>"
            "<body style='font:16px system-ui;margin:3rem;max-width:40rem'>"
            "<h1>cosmo-net</h1>"
            "<p>Интерфейс не собран. Соберите его командой "
            "<code>npm --prefix frontend run build</code> "
            "или откройте <a href='/docs'>/docs</a>, чтобы работать с API напрямую.</p>"
        )
