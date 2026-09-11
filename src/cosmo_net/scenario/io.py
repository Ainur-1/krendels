"""
Reading scenarios from disk or from an upload, and writing them back out.

A scenario that came in and a scenario the user edited in the interface leave here
through the same function, because the case asks for an edited variant to be
exportable and loadable again — so a round trip has to be exact, including any
field this service does not itself understand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cosmo_net.config import SCENARIOS_DIR
from cosmo_net.scenario.schema import Scenario, ScenarioError, ScenarioInvalid, parse_scenario


def load_scenario(path: str | Path) -> Scenario:
    """Read and validate a scenario file."""

    return loads_scenario(Path(path).read_text(encoding="utf-8"))


def loads_scenario(text: str) -> Scenario:
    """
    Validate a scenario that arrived as text.

    Malformed JSON is reported the same way as a bad field rather than as a stack
    trace, and the message carries the line and column, because that is what lets
    someone fix the file they just uploaded.
    """

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioInvalid(
            [
                ScenarioError(
                    "",
                    "malformed_json",
                    f"{exc.msg} at line {exc.lineno}, column {exc.colno}",
                )
            ]
        ) from exc
    return parse_scenario(data)


def dump_scenario(scenario: Scenario) -> dict[str, Any]:
    """
    The scenario as plain data, ready to be written back as JSON.

    `by_alias` is off and unknown keys are kept, so a file that carried fields this
    service ignores comes back out carrying them still.
    """

    return scenario.model_dump(mode="json")


def bundled_scenarios() -> list[Path]:
    """The scenarios shipped with the case, in the order they are meant to be worked through."""

    return sorted(SCENARIOS_DIR.glob("*.json"))
