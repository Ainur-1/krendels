"""
Построить графики разведочного анализа в reports/figures/.

Считает `cosmo_net.analysis.exploration`, здесь только отрисовка. Каждый график
отвечает на один вопрос о входных данных, и ответ на него не зависит от того, что мы
насчитали потом — кроме шестого, где нарочно сопоставлены геометрия на входе и
измеренный результат.

    uv sync --extra figures
    uv run python scripts/make_figures.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from cosmo_net.analysis.exploration import (  # noqa: E402
    contact_durations_s,
    explore,
    ring_geometry,
)
from cosmo_net.analysis.reachability import availability_series  # noqa: E402
from cosmo_net.analysis.simulate import simulate  # noqa: E402
from cosmo_net.config import REPORTS_DIR, SCENARIOS_DIR  # noqa: E402
from cosmo_net.geometry.orbit import orbit_radius_km  # noqa: E402
from cosmo_net.scenario.io import load_scenario  # noqa: E402

# Светлый фон: графики уезжают в презентацию и в README, а не в тёмный интерфейс.
# Цвета плоскостей те же, что в сервисе, чтобы читатель не переучивался.
PLANE_COLOURS = ["#1f6fd0", "#2f9e44", "#e8590c", "#7048e8"]
ACCENT = "#1f6fd0"
BAD = "#d6336c"
GOOD = "#2f9e44"
GREY = "#868e96"

plt.rcParams.update(
    {
        "figure.dpi": 140,
        "savefig.dpi": 140,
        "savefig.bbox": "tight",
        "font.size": 11,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def figure_batches(scenario, path: Path) -> str:
    """Очередь запуска против плоскости: совпадают ли они."""

    found = explore(scenario)
    planes = [p.plane_id for p in found.planes]
    fig, ax = plt.subplots(figsize=(9, 3.2))

    batches = sorted({s.launch_batch for s in scenario.design.satellites})
    for batch in batches:
        xs = [s.slot_deg for s in scenario.design.satellites if s.launch_batch == batch]
        ys = [
            planes.index(s.plane_id)
            for s in scenario.design.satellites
            if s.launch_batch == batch
        ]
        ax.scatter(
            xs, ys, s=70, label=f"очередь {batch}",
            color=PLANE_COLOURS[(batch - 1) % len(PLANE_COLOURS)], zorder=3,
        )

    ax.set_yticks(range(len(planes)), planes)
    ax.set_xlabel("положение аппарата внутри плоскости, градусы")
    ax.set_title(
        "Очередь запуска совпадает с орбитальной плоскостью\n"
        "первая очередь — это одна плоскость, а не треть группировки",
        loc="left", fontsize=12,
    )
    ax.set_ylim(-0.6, len(planes) - 0.4)
    ax.legend(frameon=False, ncol=len(batches), loc="upper center",
              bbox_to_anchor=(0.5, -0.28))
    fig.savefig(path)
    plt.close(fig)
    return (
        "очередь запуска совпадает с плоскостью"
        if found.batches_match_planes
        else "не совпадает"
    )


def figure_ring(scenario, path: Path) -> str:
    """Дотягиваются ли соседи по плоскости при заданной дальности связи."""

    ring = ring_geometry(scenario)
    r = orbit_radius_km(scenario.environment.altitude_km)
    counts = np.arange(6, 33)
    chords = 2 * r * np.sin(np.pi / counts)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(counts, chords, color=ACCENT, lw=2, label="расстояние до соседа в плоскости")

    for limit, colour, name in ((3000.0, GOOD, "предел 3000 км"), (2000.0, BAD, "предел 2000 км")):
        ax.axhline(limit, color=colour, ls="--", lw=1.5)
        ax.annotate(name, (counts[-1], limit), (-4, 6), textcoords="offset points",
                    ha="right", color=colour, fontsize=10)

    n = ring.satellites_per_plane
    ax.scatter([n], [ring.chord_km], s=110, color="#212529", zorder=4)
    ax.annotate(
        f"выдано: {n} аппаратов, {ring.chord_km:.0f} км",
        (n, ring.chord_km), (12, -26), textcoords="offset points", fontsize=10,
    )

    ax.set_xlabel("аппаратов в плоскости")
    ax.set_ylabel("расстояние между соседями, км")
    ax.set_title(
        "При 2000 км кольцо внутри плоскости не замыкается вовсе\n"
        f"чтобы замкнулось, нужно не меньше {ring_geometry_at(scenario, 2000.0)}"
        " аппаратов на плоскость",
        loc="left", fontsize=12,
    )
    ax.legend(frameon=False, loc="upper right")
    fig.savefig(path)
    plt.close(fig)
    return f"хорда {ring.chord_km:.0f} км, запас при 3000 км {ring.margin_km:+.0f} км"


def ring_geometry_at(scenario, limit_km: float) -> int:
    """Сколько аппаратов на плоскость нужно, чтобы кольцо замкнулось при другой дальности."""

    changed = scenario.model_copy(deep=True)
    changed.environment.isl_range_km = limit_km
    return ring_geometry(changed).satellites_needed


def figure_geography(scenario, path: Path) -> str:
    """Геометрия наземного сегмента предсказывает, нужен ли транзит через сеть."""

    found = explore(scenario)
    result = simulate(scenario)

    labels, ratios, single = [], [], []
    for site in found.sites:
        routes = [r for r in result.routes[site.client_id] if r is not None]
        labels.append(site.client_id)
        ratios.append(site.bridging_ratio)
        single.append(100 * sum(1 for r in routes if r.hops == 2) / len(routes))

    fig, ax = plt.subplots(figsize=(8, 4.4))
    ax.plot(ratios, single, "o-", color=ACCENT, lw=2, ms=11)
    for label, x, y in zip(labels, ratios, single, strict=True):
        ax.annotate(f"  {label}", (x, y), fontsize=11, va="center")

    ax.set_xlabel("расстояние до шлюза в долях того, что перекрывает один аппарат")
    ax.set_ylabel("доля шагов, обслуженных одним аппаратом, %")
    ax.set_xlim(0, 1.1)
    ax.set_ylim(-5, 100)
    ax.axvline(1.0, color=BAD, ls="--", lw=1.5)
    ax.annotate("предел: дальше одного аппарата\nне хватает никогда", (1.0, 55), (-10, 0),
                textcoords="offset points", ha="right", color=BAD, fontsize=10)
    ax.set_title(
        "Чем дальше пункт от шлюза, тем сильнее он зависит от межспутниковой сети\n"
        "по горизонтали — геометрия входных данных, по вертикали — измеренный результат",
        loc="left", fontsize=12,
    )
    fig.savefig(path)
    plt.close(fig)
    return ", ".join(
        f"{a}: {b:.2f} → {c:.0f} %"
        for a, b, c in zip(labels, ratios, single, strict=True)
    )


def figure_visibility(scenario, path: Path) -> str:
    """Сколько аппаратов видно над каждым пунктом в течение суток."""

    found = explore(scenario)
    hours = np.array(scenario.times) / 3600.0
    sites = list(found.visible_per_site.items())

    # Четыре отдельные полосы вместо четырёх наложенных линий: наложенные дают шум, в
    # котором не видно ни суточного ритма, ни моментов, когда над пунктом пусто, — а
    # это и есть то, ради чего график построен.
    fig, axes = plt.subplots(len(sites), 1, figsize=(11, 1.5 * len(sites) + 1.4),
                             sharex=True, sharey=True)
    top = max(int(counts.max()) for _, counts in sites)

    for ax, (site_id, counts), colour in zip(axes, sites, PLANE_COLOURS, strict=False):
        ax.fill_between(hours, counts, step="post", color=colour, alpha=0.75, lw=0)
        zeros = hours[counts == 0]
        if zeros.size:
            ax.plot(zeros, np.zeros_like(zeros), "|", color=BAD, ms=14, mew=2)
        ax.set_ylim(0, top + 0.4)
        ax.set_yticks(range(0, top + 1))
        # Среднее живёт в подписи оси, а не аннотацией внутри поля: внутри она
        # садится на данные у пунктов с плотным покрытием.
        ax.set_ylabel(f"{site_id}\nв среднем {counts.mean():.2f}", rotation=0,
                      ha="right", va="center", fontsize=11)

    axes[-1].set_xlabel("время от начала расчёта, часы")
    axes[-1].set_xlim(0, hours[-1])
    blind = sum(int((counts == 0).sum()) for _, counts in sites)
    axes[0].set_title(
        "Покрытие тонкое: над пунктом обычно один-два аппарата\n"
        f"красным отмечены отсчёты, когда не видно ни одного — всего {blind} из "
        f"{len(hours)} по четырём пунктам",
        loc="left", fontsize=12,
    )
    fig.savefig(path)
    plt.close(fig)
    return ", ".join(f"{k} {v.mean():.2f}" for k, v in sites)


def figure_failures(scenario, path: Path) -> str:
    """Как объявленные отказы распределены по плоскостям."""

    found = explore(scenario)
    planes = list(found.satellites_per_plane)
    total = [found.satellites_per_plane[p] for p in planes]
    lost = [found.failures_per_plane.get(p, 0) for p in planes]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(planes, total, color="#dee2e6", label="в плоскости")
    ax.bar(planes, lost, color=BAD, label="выведено из строя")
    for index, (t, x) in enumerate(zip(total, lost, strict=True)):
        ax.annotate(f"{x} из {t}\n{100 * x / t:.0f} %", (index, x), (0, 6),
                    textcoords="offset points", ha="center", fontsize=10)

    ax.set_ylim(0, max(total) * 1.18)
    ax.set_ylabel("аппаратов")
    ax.set_title(
        "Отказы легли на плоскости неравномерно\n"
        "все начинаются в один момент и держатся до конца расчёта",
        loc="left", fontsize=12,
    )
    ax.legend(frameon=False, loc="upper right", ncol=2)
    fig.savefig(path)
    plt.close(fig)
    return ", ".join(f"{p}: {x}/{t}" for p, x, t in zip(planes, lost, total, strict=True))


def figure_grid(scenario, path: Path) -> str:
    """Достаточно ли предписанного шага расчёта."""

    durations = contact_durations_s(scenario)
    step = scenario.environment.step_s

    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.2))

    everything = np.concatenate(list(durations.values()))
    left.hist(everything, bins=40, color=ACCENT, alpha=0.85)
    left.axvline(step, color=BAD, ls="--", lw=1.6)
    short = 100 * float((everything < step).mean())
    left.annotate(f"шаг расчёта {step} с\nкороче него {short:.0f} % сеансов",
                  (step, left.get_ylim()[1] * 0.7), (12, 0), textcoords="offset points",
                  color=BAD, fontsize=10)
    left.set_xlabel("длительность сеанса видимости, с")
    left.set_ylabel("сеансов")
    left.set_title("Часть сеансов короче шага", loc="left", fontsize=12)

    steps = [120, 60, 30, 15]
    series = {}
    for candidate in steps:
        changed = scenario.model_copy(deep=True)
        changed.environment.step_s = candidate
        for client, reachable in availability_series(changed).items():
            series.setdefault(client, []).append(100 * float(reachable.mean()))

    for index, (client, values) in enumerate(series.items()):
        right.plot(steps, values, "o-", lw=2,
                   color=PLANE_COLOURS[index % len(PLANE_COLOURS)], label=client)
    # Ось от целевого уровня до сотни. На автоматической оси разброс в 0.4 п.п.
    # растягивается во всю высоту и выглядит скачком, хотя весь смысл графика в том,
    # что ответ от шага не зависит.
    target = 100 * scenario.environment.target_availability
    right.axhline(target, color=GREY, ls=":", lw=1.4)
    right.annotate(f"цель {target:.0f} %", (steps[0], target), (6, 5),
                   textcoords="offset points", color=GREY, fontsize=10)
    right.set_ylim(target - 2, 100.5)
    right.invert_xaxis()
    right.set_xlabel("шаг расчёта, с (мельче вправо)")
    right.set_ylabel("доступность, %")
    spread = max(max(v) - min(v) for v in series.values())
    right.set_title(f"Но ответ от шага почти не зависит: разброс {spread:.2f} п.п.",
                    loc="left", fontsize=12)
    right.legend(frameon=False, ncol=len(series), loc="lower center")

    fig.savefig(path)
    plt.close(fig)
    return f"короче шага {short:.1f} % сеансов, разброс доступности {spread:.2f} п.п."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR / "figures")
    arguments = parser.parse_args()
    arguments.out.mkdir(parents=True, exist_ok=True)

    full = load_scenario(SCENARIOS_DIR / "01_full_constellation.json")
    outages = load_scenario(SCENARIOS_DIR / "03_satellite_outages.json")

    work = [
        ("01_ocheredi_i_ploskosti.png", lambda p: figure_batches(full, p)),
        ("02_koltso_v_ploskosti.png", lambda p: figure_ring(full, p)),
        ("03_geografiya.png", lambda p: figure_geography(full, p)),
        ("04_vidimost.png", lambda p: figure_visibility(full, p)),
        ("05_otkazy_po_ploskostyam.png", lambda p: figure_failures(outages, p)),
        ("06_shag_setki.png", lambda p: figure_grid(full, p)),
    ]

    for name, build in work:
        note = build(arguments.out / name)
        print(f"  {name}: {note}")

    print(f"записано в {arguments.out}")


if __name__ == "__main__":
    main()
