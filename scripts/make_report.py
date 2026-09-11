"""
Прогнать все исследования по всем выданным сценариям и записать числа в reports/metrics/.

Отсюда берутся все цифры в README и в защите. Ни одно число в этом репозитории не
приводится, если его не пишет этот скрипт или тест, и перезапуск — это способ для
читателя проверить любое из них:

    uv run python scripts/make_report.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from cosmo_net.analysis.compare import compare_runs
from cosmo_net.analysis.criticality import rank_satellites
from cosmo_net.analysis.optimise import sweep_spacing, variant
from cosmo_net.analysis.simulate import simulate
from cosmo_net.config import METRICS_DIR
from cosmo_net.routing.strategies import Strategy
from cosmo_net.scenario.io import bundled_scenarios, load_scenario
from cosmo_net.scenario.schema import GroundSite, Scenario

# Вторая точка приземления трафика, примерно под средним клиентом. Частью кейса она
# не является, и менять выданные данные нельзя: это самое дешёвое вмешательство,
# которое нашёл анализ, и исследование существует, чтобы поставить ему число, а не
# чтобы предложить правку сценария.
SECOND_GATEWAY = GroundSite(
    id="G_NOR",
    name="Второй шлюз (Норильск)",
    role="gateway",
    lat_deg=69.35,
    lon_deg=88.2,
)


def with_second_gateway(scenario: Scenario) -> Scenario:
    changed = scenario.model_copy(deep=True)
    changed.ground_sites.append(SECOND_GATEWAY.model_copy())
    return changed


def with_elevation(scenario: Scenario, min_elevation_deg: float) -> Scenario:
    changed = scenario.model_copy(deep=True)
    changed.environment.min_elevation_deg = min_elevation_deg
    return changed


def study_scenario(path: Path, workers: int) -> dict[str, object]:
    """Базовая линия, стратегии, критичность, перебор и проверки по наземному сегменту."""

    scenario = load_scenario(path)
    started = time.perf_counter()

    baseline = simulate(scenario)
    strategies = {
        str(strategy): simulate(scenario, strategy).summary() for strategy in Strategy
    }

    sweep = sweep_spacing(scenario, workers=workers)
    tuned = variant(scenario, sweep.best.raan_deg, sweep.best.phase_deg)

    # Каждая проверка меняет ровно одну вещь на той же сетке времени, поэтому строки
    # можно читать друг против друга. Орбитальная строка и наземные — это два
    # семейства вмешательств, которые анализ и сравнивает.
    what_if = {
        "baseline": baseline.summary(),
        "tuned_planes": simulate(tuned).summary(),
        "second_gateway": simulate(with_second_gateway(scenario)).summary(),
        "elevation_5deg": simulate(with_elevation(scenario, 5.0)).summary(),
        "second_gateway_and_elevation_5deg": simulate(
            with_elevation(with_second_gateway(scenario), 5.0)
        ).summary(),
    }

    return {
        "scenario": {
            "id": scenario.meta.id,
            "title": scenario.meta.title,
            "file": path.name,
            "satellites": len(scenario.design.satellites),
            "planes": len(scenario.design.planes),
            "launch_stage": scenario.design.launch_stage,
            "clients": [site.id for site in scenario.clients],
            "gateways": [site.id for site in scenario.gateways],
            "isl_range_km": scenario.environment.isl_range_km,
            "min_elevation_deg": scenario.environment.min_elevation_deg,
            "steps": len(scenario.times),
        },
        "baseline": baseline.summary(),
        "strategies": strategies,
        "criticality": rank_satellites(scenario).to_dict(),
        "sweep": sweep.to_dict(),
        "what_if": what_if,
        "tuned_vs_baseline": compare_runs(
            [baseline, simulate(tuned)], ["baseline", "tuned_planes"]
        ),
        "elapsed_s": round(time.perf_counter() - started, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="число процессов для перебора конфигураций (1 отключает параллельность)",
    )
    parser.add_argument("--out", type=Path, default=METRICS_DIR)
    arguments = parser.parse_args()

    arguments.out.mkdir(parents=True, exist_ok=True)
    index = []

    for path in bundled_scenarios():
        print(f"{path.stem} …", end="", flush=True)
        report = study_scenario(path, arguments.workers)
        destination = arguments.out / f"{path.stem}.json"
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

        baseline = report["baseline"]
        print(
            f" worst {baseline['worst_availability'] * 100:.2f}%"
            f" → tuned {report['what_if']['tuned_planes']['worst_availability'] * 100:.2f}%"
            f"  ({report['elapsed_s']} s)"
        )
        index.append(
            {
                "scenario_id": report["scenario"]["id"],
                "file": destination.name,
                "worst_availability": baseline["worst_availability"],
                "meets_target": baseline["meets_target"],
                "best_tuned_availability": report["sweep"]["best"]["worst_availability"],
            }
        )

    (arguments.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"записано в {arguments.out}")


if __name__ == "__main__":
    main()
