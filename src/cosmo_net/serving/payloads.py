"""
Shaping a run for the wire: what the interface gets, and what the case says an export must be.

One decision worth stating. A run is returned whole — every step's route, cause and
hop count in one response — rather than one step at a time. For a default run that
is about 200 kB before compression, and it means the time slider redraws from
memory instead of asking the server 720 times. The alternative was tried first and
felt exactly as slow as it sounds.
"""

from __future__ import annotations

from typing import Any

from cosmo_net.analysis.simulate import NetworkSnapshot, RunResult
from cosmo_net.config import RESULT_SCHEMA_VERSION
from cosmo_net.routing.diagnose import Outage
from cosmo_net.scenario.io import dump_scenario
from cosmo_net.scenario.schema import Scenario, ScenarioError


def run_payload(run_id: str, result: RunResult) -> dict[str, Any]:
    """Everything the interface needs to draw a run without asking again."""

    target = result.scenario.environment.target_availability
    clients: dict[str, Any] = {}

    for client_id, metrics in result.metrics.items():
        routes = result.routes[client_id]
        clients[client_id] = {
            "metrics": metrics.summary(target),
            "reachable": [route is not None for route in routes],
            "cause": [str(cause) for cause in result.causes[client_id]],
            "hops": [route.hops if route else None for route in routes],
            "path": [route.path if route else [] for route in routes],
            "gateway": [route.gateway_id if route else None for route in routes],
            "length_km": [
                round(route.length_km, 1) if route else None for route in routes
            ],
            "gaps": [
                {
                    "start_s": gap.start_s,
                    "end_s": gap.end_s,
                    "duration_s": gap.duration_s,
                    "cause": str(gap.cause),
                    "causes": gap.causes,
                    "at_horizon_edge": gap.at_horizon_edge,
                }
                for gap in metrics.gaps
            ],
        }

    return {
        "run_id": run_id,
        "summary": result.summary(),
        "times_s": result.times_s,
        "clients": clients,
        "scenario": dump_scenario(result.scenario),
        "outage_causes": [str(cause) for cause in Outage if cause is not Outage.NONE],
    }


def snapshot_payload(snapshot: NetworkSnapshot, scenario: Scenario) -> dict[str, Any]:
    """The network at one moment, with the ground sites it is drawn against."""

    return {
        "t_s": snapshot.t_s,
        "satellites": snapshot.satellites,
        "links": snapshot.links,
        "ground_links": snapshot.ground_links,
        "elevation_deg": snapshot.elevation_deg,
        "ground_sites": [site.model_dump(mode="json") for site in scenario.ground_sites],
    }


def export_payload(result: RunResult) -> dict[str, Any]:
    """
    The result in the format the case specifies: `cosmo-A-result-1.0`.

    One record per (step, client) pair — 2160 for a default run — and an empty path
    where no route existed rather than a missing record, so a reader can count
    availability straight out of the file. `effective_scenario` is the scenario as
    it was actually computed, edits included, which is what makes the export
    reloadable and the run reproducible.
    """

    routes = []
    for client_id, per_step in result.routes.items():
        for t_s, route in zip(result.times_s, per_step, strict=True):
            routes.append(
                {"t_s": t_s, "client_id": client_id, "path": route.path if route else []}
            )
    routes.sort(key=lambda record: (record["t_s"], record["client_id"]))

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "effective_scenario": dump_scenario(result.scenario),
        "routes": routes,
        # Beyond the required fields, and allowed: "К результату можно добавить
        # сводные показатели и пояснения."
        "summary": result.summary(),
        "routing_strategy": str(result.strategy),
    }


def error_payload(errors: list[ScenarioError]) -> dict[str, Any]:
    """
    Validation failures, addressed by field so the interface can point at one.

    `code` is stable and is what the Russian interface translates; `message` is the
    English fallback for anything the interface has no wording for yet.
    """

    return {
        "detail": "scenario_invalid",
        "errors": [
            {"field": error.field, "code": error.code, "message": error.message}
            for error in errors
        ],
    }


def scenario_summary(scenario: Scenario, source: str = "") -> dict[str, Any]:
    """The short description shown in a picker, without sending the whole file."""

    return {
        "source": source,
        "id": scenario.meta.id,
        "title": scenario.meta.title,
        "planes": len(scenario.design.planes),
        "satellites": len(scenario.design.satellites),
        "launch_stage": scenario.design.launch_stage,
        "clients": [{"id": s.id, "name": s.name} for s in scenario.clients],
        "gateways": [{"id": s.id, "name": s.name} for s in scenario.gateways],
        "steps": len(scenario.times),
        "environment": scenario.environment.model_dump(mode="json"),
        "failures": len(scenario.failures),
        "gateway_outages": len(scenario.gateway_outages),
    }
