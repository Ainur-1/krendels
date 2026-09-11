"""
Saved variants, and the runs computed from them.

The two are kept differently on purpose. A variant is a design someone decided to
keep and expects to find again, so it goes to SQLite and survives a restart — the
case asks for a variant to be saved and returned to for comparison, and losing the
morning's work because a container was redeployed would be the wrong kind of
surprise on demo day. A run is the output of 124 ms of arithmetic over a variant
that is already stored, so it lives in memory and the oldest ones are dropped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cosmo_net.analysis.simulate import RunResult
from cosmo_net.config import RUNS_DATABASE
from cosmo_net.scenario.io import dump_scenario
from cosmo_net.scenario.schema import Scenario, parse_scenario

DEFAULT_DATABASE = RUNS_DATABASE

# Enough runs for a comparison view and the last few things the user looked at.
# Each is a few hundred kilobytes of Python objects, so this is a handful of megabytes.
MAX_CACHED_RUNS = 32


class VariantStore:
    """Saved designs, by identifier. Thread-safe because uvicorn serves requests from a pool."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_DATABASE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS variants (
                id          TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                scenario    TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def save(self, label: str, scenario: Scenario, variant_id: str | None = None) -> str:
        """Store a design under a readable label and return its identifier."""

        identifier = variant_id or uuid.uuid4().hex[:12]
        payload = json.dumps(dump_scenario(scenario), ensure_ascii=False)
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO variants (id, label, created_at, scenario)"
                " VALUES (?, ?, ?, ?)",
                (identifier, label, datetime.now(UTC).isoformat(timespec="seconds"), payload),
            )
            self._connection.commit()
        return identifier

    def get(self, variant_id: str) -> tuple[str, Scenario] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT label, scenario FROM variants WHERE id = ?", (variant_id,)
            ).fetchone()
        if row is None:
            return None
        return row["label"], parse_scenario(json.loads(row["scenario"]))

    def list(self) -> list[dict[str, Any]]:
        """Newest first, without the scenario bodies — the list view does not need them."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT id, label, created_at, scenario FROM variants ORDER BY created_at DESC"
            ).fetchall()

        listing = []
        for row in rows:
            scenario = json.loads(row["scenario"])
            design = scenario["design"]
            listing.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "created_at": row["created_at"],
                    "scenario_id": scenario.get("meta", {}).get("id", ""),
                    "launch_stage": design["launch_stage"],
                    "planes": len(design["planes"]),
                    "satellites": len(design["satellites"]),
                }
            )
        return listing

    def delete(self, variant_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM variants WHERE id = ?", (variant_id,)
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class RunCache:
    """The last few computed runs, so a time slider does not recompute the horizon per frame."""

    def __init__(self, capacity: int = MAX_CACHED_RUNS) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, RunResult] = OrderedDict()

    def put(self, result: RunResult) -> str:
        identifier = uuid.uuid4().hex[:12]
        with self._lock:
            self._runs[identifier] = result
            while len(self._runs) > self._capacity:
                self._runs.popitem(last=False)
        return identifier

    def get(self, run_id: str) -> RunResult | None:
        with self._lock:
            result = self._runs.get(run_id)
            if result is not None:
                self._runs.move_to_end(run_id)
        return result

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)
