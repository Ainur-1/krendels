"""
Putting two saved variants side by side: what was changed, and what it bought.

The case asks for both halves and they are easy to separate in practice. The
difference in results is a table of numbers. The difference in *inputs* is harder:
a variant saved an hour earlier differs from the current one by some set of edits
nobody wrote down, and a comparison that only shows outcomes leaves the reader
guessing which edit produced them. So the diff walks the two scenarios and reports
every field that moved, matching list entries by identifier rather than by position
— a satellite that moved down the list has not changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cosmo_net.analysis.simulate import RunResult
from cosmo_net.scenario.io import dump_scenario
from cosmo_net.scenario.schema import Scenario

# Lists whose entries carry their own identity. Anything else is compared as a whole.
_KEYED = {
    "design.planes": "id",
    "design.satellites": "id",
    "ground_sites": "id",
}


@dataclass(frozen=True)
class Change:
    """One field that differs between two scenarios."""

    path: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "before": self.before, "after": self.after}


def diff_scenarios(before: Scenario, after: Scenario) -> list[Change]:
    """Every field that differs, addressed the same way validation errors are."""

    changes: list[Change] = []
    _walk(dump_scenario(before), dump_scenario(after), "", changes)
    return changes


def compare_runs(runs: list[RunResult], labels: list[str] | None = None) -> dict[str, Any]:
    """
    A comparison table over two or more completed runs.

    Every run has to be on the same grid for the numbers to mean anything together,
    so a mismatch is reported rather than quietly tabulated: comparing a 24-hour run
    against a 12-hour one would make the shorter one look better for no reason
    connected to the design.
    """

    if not runs:
        return {"runs": [], "clients": [], "comparable": True}

    names = labels or [r.scenario.meta.id for r in runs]
    grids = {(r.scenario.environment.horizon_s, r.scenario.environment.step_s) for r in runs}
    comparable = len(grids) == 1

    clients = sorted({client for run in runs for client in run.metrics})
    rows = []
    for client in clients:
        cells = []
        for run in runs:
            metrics = run.metrics.get(client)
            cells.append(
                None
                if metrics is None
                else {
                    "availability_share": metrics.availability_share,
                    "visibility_share": metrics.visibility_share,
                    "max_gap_s": metrics.max_gap_s,
                    "gap_count": metrics.gap_count,
                    "mean_hops": metrics.mean_hops,
                    "mean_route_length_km": metrics.mean_route_length_km,
                    "meets_target": metrics.meets(
                        run.scenario.environment.target_availability
                    ),
                }
            )
        rows.append({"client_id": client, "cells": cells})

    return {
        "runs": [
            {
                "label": name,
                "scenario_id": run.scenario.meta.id,
                "strategy": str(run.strategy),
                "worst_availability": run.worst_availability,
                "worst_max_gap_s": run.worst_max_gap_s,
                "meets_target": run.meets_target,
            }
            for name, run in zip(names, runs, strict=True)
        ],
        "clients": rows,
        "comparable": comparable,
        "grids": sorted(grids),
        "changes": (
            [c.to_dict() for c in diff_scenarios(runs[0].scenario, runs[-1].scenario)]
            if len(runs) >= 2
            else []
        ),
    }


def _walk(before: Any, after: Any, path: str, changes: list[Change]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            _walk(before.get(key), after.get(key), f"{path}.{key}" if path else key, changes)
        return

    if isinstance(before, list) and isinstance(after, list):
        key = _KEYED.get(path)
        if key:
            _walk_keyed(before, after, path, key, changes)
        elif before != after:
            changes.append(Change(path, before, after))
        return

    if before != after:
        changes.append(Change(path, before, after))


def _walk_keyed(
    before: list[Any], after: list[Any], path: str, key: str, changes: list[Change]
) -> None:
    """Match entries by their identifier, so reordering a list is not a change."""

    old = {entry.get(key): entry for entry in before if isinstance(entry, dict)}
    new = {entry.get(key): entry for entry in after if isinstance(entry, dict)}

    for identifier in sorted(set(old) - set(new), key=str):
        changes.append(Change(f"{path}[{identifier}]", old[identifier], None))
    for identifier in sorted(set(new) - set(old), key=str):
        changes.append(Change(f"{path}[{identifier}]", None, new[identifier]))
    for identifier in sorted(set(old) & set(new), key=str):
        _walk(old[identifier], new[identifier], f"{path}[{identifier}]", changes)
