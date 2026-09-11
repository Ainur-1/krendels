"""
Generate a scenario deliberately unlike the four supplied ones.

The case says the judges will upload another scenario in the same format and change
parameters through the interface, and «работа с входными данными» is scored on what
happens then. Every assumption the supplied files would let you get away with is
broken here on purpose:

- four planes, not three, so nothing may index planes as a fixed triple;
- ten satellites per plane, so the 22.5° in-plane spacing is not a constant;
- launch batches that cut across planes, so `launch_batch == plane` is false;
- two gateways, so a route has a choice of destination;
- a gateway outage, the one branch no supplied file exercises at all;
- a different altitude, inclination, range limit, elevation threshold and target;
- a 12-hour horizon on a 60 s step, so the grid is 720 steps for different reasons;
- identifiers in a different shape, so nothing may parse meaning out of an id.

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
                    # Batches run across the planes rather than along them, so a
                    # stage change thins every plane instead of removing one.
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
        # The branch no supplied scenario reaches: one of the two gateways goes down
        # for four hours, and traffic has to leave through the other one or not at all.
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

    # Written only after it parses, so the file on disk is never one the service
    # would reject: a broken fixture would make every test using it fail for the
    # wrong reason.
    scenario = loads_scenario(text)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(text + "\n", encoding="utf-8")

    print(
        f"{arguments.out}: {len(scenario.design.satellites)} satellites, "
        f"{len(scenario.design.planes)} planes, {len(scenario.gateways)} gateways, "
        f"{len(scenario.times)} steps"
    )


if __name__ == "__main__":
    main()
