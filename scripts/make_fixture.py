"""
Сгенерировать сценарий, намеренно непохожий на четыре выданных.

Кейс прямо говорит, что жюри загрузит другой сценарий того же формата и поменяет
параметры через интерфейс, и «работа с входными данными» оценивается по тому, что
тогда произойдёт. Здесь специально нарушено каждое предположение, на которое
выданные файлы могли бы навести:

- четыре плоскости, а не три, поэтому нельзя обращаться к плоскостям как к тройке;
- по десять аппаратов в плоскости, поэтому шаг 22.5° внутри плоскости не константа;
- очереди запуска идут поперёк плоскостей, поэтому «очередь = плоскость» неверно;
- два шлюза, поэтому у маршрута есть выбор, куда приземляться;
- отказ шлюза — единственная ветка, которую ни один выданный файл не задействует;
- другая высота, наклонение, предел дальности, порог угла места и целевой уровень;
- горизонт 12 часов при шаге 60 с, поэтому 720 отсчётов получаются по другой причине;
- идентификаторы другой формы, поэтому нельзя вычитывать смысл из самого имени.

    uv run python scripts/make_fixture.py --out data/scenarios/90_judge_fixture.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cosmo_net.config import PROJECT_ROOT, SCENARIO_SCHEMA_VERSION
from cosmo_net.scenario.io import loads_scenario

PLANES = 4
PER_PLANE = 10


def build() -> dict[str, object]:
    planes = [
        {"id": f"ORB-{chr(ord('A') + k)}", "raan_deg": 15.0 + k * 45.0, "phase_deg": k * 9.0}
        for k in range(PLANES)
    ]

    satellites = []
    for k, plane in enumerate(planes):
        for slot in range(PER_PLANE):
            index = k * PER_PLANE + slot
            satellites.append(
                {
                    "id": f"SAT-{index + 101}",
                    "plane_id": plane["id"],
                    "slot_deg": round(slot * 360.0 / PER_PLANE, 4),
                    # Очереди идут поперёк плоскостей, а не вдоль, поэтому смена
                    # очереди прореживает каждую плоскость, а не убирает одну целиком.
                    "launch_batch": (index % 3) + 1,
                }
            )

    return {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "meta": {"id": "90_judge_fixture", "title": "Проверочный сценарий другой формы"},
        "environment": {
            "altitude_km": 700.0,
            "inclination_deg": 82.0,
            "earth_angle0_deg": 317.5,
            "horizon_s": 43_200,
            "step_s": 60,
            "min_elevation_deg": 7.5,
            "isl_range_km": 2500.0,
            "target_availability": 0.95,
        },
        "design": {"launch_stage": 3, "planes": planes, "satellites": satellites},
        "ground_sites": [
            {
                "id": "GW-WEST",
                "name": "Западный шлюз",
                "role": "gateway",
                "lat_deg": 67.5,
                "lon_deg": 24.0,
            },
            {
                "id": "GW-EAST",
                "name": "Восточный шлюз",
                "role": "gateway",
                "lat_deg": 62.0,
                "lon_deg": 129.7,
            },
            {
                "id": "TERM-1",
                "name": "Терминал 1",
                "role": "client",
                "lat_deg": 74.5,
                "lon_deg": 42.0,
            },
            {
                "id": "TERM-2",
                "name": "Терминал 2",
                "role": "client",
                "lat_deg": 66.0,
                "lon_deg": 105.0,
            },
        ],
        "failures": [
            {"satellite_id": "SAT-104", "start_s": 3_600, "end_s": 18_000},
            {"satellite_id": "SAT-127", "start_s": 0, "end_s": 43_200},
        ],
        # Ветка, до которой не доходит ни один выданный сценарий: один из двух шлюзов
        # выключается на четыре часа, и трафику приходится уходить через второй — или
        # не уходить вовсе.
        "gateway_outages": [{"gateway_id": "GW-WEST", "start_s": 7_200, "end_s": 21_600}],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "judge_fixture.json",
    )
    arguments = parser.parse_args()

    text = json.dumps(build(), ensure_ascii=False, indent=2)

    # Записывается только после успешного разбора, чтобы на диске никогда не лежал
    # файл, который сервис забраковал бы: сломанная фикстура завалила бы все
    # использующие её тесты по неправильной причине.
    scenario = loads_scenario(text)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(text + "\n", encoding="utf-8")

    print(
        f"{arguments.out}: аппаратов {len(scenario.design.satellites)}, "
        f"плоскостей {len(scenario.design.planes)}, шлюзов {len(scenario.gateways)}, "
        f"отсчётов {len(scenario.times)}"
    )


if __name__ == "__main__":
    main()
