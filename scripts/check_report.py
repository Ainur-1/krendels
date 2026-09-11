"""
Сверить свежий прогон отчёта с тем, что лежит в репозитории.

Смысл проверки в том, чтобы правка, молча сдвигающая опубликованное число, падала
здесь, а не обнаруживалась на защите. Поэтому сверяются именно те величины, на
которые ссылаются README и docs/findings.md, — а не всё подряд: в отчёте есть
длины трасс в километрах, и требовать от них побитового совпадения на другой
машине значило бы ловить разницу в последнем знаке вместо ошибок.

    uv run python scripts/make_report.py --out /tmp/metrics
    uv run python scripts/check_report.py /tmp/metrics
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cosmo_net.config import METRICS_DIR

# Доли отсчётов — это отношения целых, поэтому совпадать они обязаны точно. Всё
# остальное сравнивается по относительной разнице: там уже арифметика с плавающей
# точкой, и разные машины вправе расходиться в последнем знаке.
EXACT = 1e-12
RELATIVE = 1e-6


def values(report: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Опубликованные числа отчёта: путь до величины → само значение и допуск."""

    out: dict[str, tuple[float, float]] = {}

    out["baseline.worst_availability"] = (report["baseline"]["worst_availability"], EXACT)
    out["baseline.worst_max_gap_s"] = (report["baseline"]["worst_max_gap_s"], EXACT)
    out["sweep.best.worst_availability"] = (report["sweep"]["best"]["worst_availability"], EXACT)
    out["criticality.spread_pp"] = (report["criticality"]["spread_pp"], RELATIVE)

    for name, run in report["what_if"].items():
        out[f"what_if.{name}.worst_availability"] = (run["worst_availability"], EXACT)

    for name, run in report["strategies"].items():
        out[f"strategies.{name}.worst_availability"] = (run["worst_availability"], EXACT)
        out[f"strategies.{name}.mean_rtt_ms"] = (run["mean_rtt_ms"], RELATIVE)
        out[f"strategies.{name}.max_rtt_ms"] = (run["max_rtt_ms"], RELATIVE)

    for row in report["delivery"]["worst_within"]:
        out[f"delivery.within[{row['deadline_s']}]"] = (row["share"], EXACT)
    out["delivery.worst_max_latency_s"] = (report["delivery"]["worst_max_latency_s"], EXACT)

    for client in report["redundancy"]["clients"]:
        key = f"redundancy.{client['client_id']}"
        out[f"{key}.single_path_share"] = (client["single_path_share"], EXACT)
        out[f"{key}.no_path_share"] = (client["no_path_share"], EXACT)
        out[f"{key}.mean_disjoint_paths"] = (client["mean_disjoint_paths"], RELATIVE)

    degradation = report["degradation"]
    out["degradation.tolerated_failures"] = (degradation["tolerated_failures"], EXACT)
    out["degradation.slope_pp_per_satellite"] = (degradation["slope_pp_per_satellite"], RELATIVE)
    for point in degradation["points"]:
        key = f"degradation[{point['failures']}]"
        out[f"{key}.mean"] = (point["mean_worst_availability"], EXACT)
        out[f"{key}.meets_target_share"] = (point["meets_target_share"], EXACT)

    families = report["families"]
    for name in ("star_best", "delta_best"):
        peak = families.get(name)
        if peak is not None:
            out[f"families.{name}.spacing_deg"] = (peak["spacing_deg"], EXACT)
            out[f"families.{name}.worst_availability"] = (peak["worst_availability"], EXACT)

    best = report["placement"]["best"]
    if best is not None:
        out["placement.best.lat_deg"] = (best["lat_deg"], EXACT)
        out["placement.best.lon_deg"] = (best["lon_deg"], EXACT)
        out["placement.best.worst_availability"] = (best["worst_availability"], EXACT)

    return out


def compare(fresh: dict[str, Any], stored: dict[str, Any], label: str) -> list[str]:
    a, b = values(fresh), values(stored)
    problems = []

    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            problems.append(f"{label}: {key} есть только в одном из отчётов")
            continue

        got, tolerance = a[key]
        want, _ = b[key]
        if got is None or want is None:
            if got is not want:
                problems.append(f"{label}: {key}: {want} → {got}")
            continue
        allowed = tolerance if tolerance == EXACT else tolerance * max(abs(want), 1.0)
        if abs(got - want) > allowed:
            problems.append(f"{label}: {key}: {want} → {got}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fresh", type=Path, help="каталог со свежим прогоном отчёта")
    parser.add_argument("--stored", type=Path, default=METRICS_DIR)
    arguments = parser.parse_args()

    files = sorted(arguments.fresh.glob("0*.json"))
    if not files:
        print(f"в {arguments.fresh} нет отчётов — сначала выполните scripts/make_report.py")
        return 1

    problems: list[str] = []
    for path in files:
        stored_path = arguments.stored / path.name
        if not stored_path.exists():
            problems.append(f"{path.stem}: в репозитории нет такого отчёта")
            continue
        problems += compare(
            json.loads(path.read_text(encoding="utf-8")),
            json.loads(stored_path.read_text(encoding="utf-8")),
            path.stem,
        )

    if problems:
        print("опубликованные числа разошлись со свежим прогоном:")
        for line in problems:
            print(f"  {line}")
        return 1

    total = sum(len(values(json.loads(p.read_text(encoding="utf-8")))) for p in files)
    print(f"отчёты воспроизводятся: сверено {total} значений в {len(files)} файлах")
    return 0


if __name__ == "__main__":
    sys.exit(main())
