"""
Чтение сценариев с диска или из загрузки и запись их обратно.

Пришедший сценарий и сценарий, отредактированный пользователем в интерфейсе, уходят
отсюда через одну и ту же функцию: кейс требует, чтобы изменённый вариант можно было
выгрузить и снова загрузить. Поэтому путь туда и обратно должен быть точным, включая
поля, которых сам сервис не понимает.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cosmo_net.config import SCENARIOS_DIR
from cosmo_net.scenario.schema import Scenario, ScenarioError, ScenarioInvalid, parse_scenario


def load_scenario(path: str | Path) -> Scenario:
    """Прочитать файл сценария и проверить его."""

    return loads_scenario(Path(path).read_text(encoding="utf-8"))


def loads_scenario(text: str) -> Scenario:
    """
    Проверить сценарий, пришедший текстом.

    Испорченный JSON сообщается так же, как неверное поле, а не трассировкой стека, и
    в сообщении есть строка и столбец: именно это позволяет человеку починить файл,
    который он только что загрузил.
    """

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioInvalid(
            [
                ScenarioError(
                    "",
                    "malformed_json",
                    f"{exc.msg}: строка {exc.lineno}, столбец {exc.colno}",
                )
            ]
        ) from exc
    return parse_scenario(data)


def dump_scenario(scenario: Scenario) -> dict[str, Any]:
    """
    Сценарий как обычные данные, готовые к записи в JSON.

    Псевдонимы полей не применяются, а неизвестные ключи сохраняются, поэтому файл,
    несущий поля, которые сервис игнорирует, выходит обратно вместе с ними.
    """

    return scenario.model_dump(mode="json")


def bundled_scenarios() -> list[Path]:
    """Сценарии из комплекта кейса, в том порядке, в котором их предполагается проходить."""

    return sorted(SCENARIOS_DIR.glob("*.json"))
