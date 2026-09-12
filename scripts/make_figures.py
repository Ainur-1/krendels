"""
Построить графики к защите в reports/figures/.

Здесь только отрисовка: считают модули `cosmo_net.analysis`, и ни одно число не
появляется на картинке, минуя их.

Графики идут двумя группами. С первого по шестой — разведка входных данных: каждый
отвечает на один вопрос о том, что нам выдали, и ответ не зависит от того, что мы
насчитали потом (кроме шестого, где геометрия на входе нарочно сопоставлена с
измеренным результатом). С седьмого по одиннадцатый — исследования сверх постановки:
допустимая задержка, деградация, запас маршрутов, семейства разноса и место для
второй точки приземления.

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

from cosmo_net.analysis.degradation import degradation_curve  # noqa: E402
from cosmo_net.analysis.delivery import delivery_report  # noqa: E402
from cosmo_net.analysis.exploration import (  # noqa: E402
    contact_durations_s,
    explore,
    ring_geometry,
)
from cosmo_net.analysis.families import spacing_curve  # noqa: E402
from cosmo_net.analysis.placement import placement_grid  # noqa: E402
from cosmo_net.analysis.reachability import availability_series  # noqa: E402
from cosmo_net.analysis.redundancy import redundancy_report  # noqa: E402
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
    """
    Очередь запуска против плоскости, нарисованная по самой орбите.

    Широта аппарата на круговой орбите — это синус от пройденного вдоль орбиты угла,
    поджатый наклонением: arcsin(sin i · sin u). Поэтому по горизонтали здесь
    положение вдоль орбиты, а не долгота. Трасса по земле при наклонении 87° почти
    вертикальна и на карте читается как частокол — пробовали, отказались.

    Что видно с одного взгляда: в каждой плоскости ровно один цвет, то есть очередь
    запуска и плоскость — одно и то же. Заодно видно фазирование: соседи внутри
    плоскости стоят через 22.5°, а сами плоскости смещены друг относительно друга на
    7.5°, и это смещение читается как сдвиг точек от панели к панели.
    """

    found = explore(scenario)
    planes = [p.plane_id for p in found.planes]
    inclination = scenario.environment.inclination_deg
    phase = {p.id: p.phase_deg for p in scenario.design.planes}

    height = 1.6 + 1.6 * len(planes)
    fig, axes = plt.subplots(len(planes), 1, figsize=(9, height), sharex=True)
    axes = np.atleast_1d(axes)

    angle = np.linspace(0, 360, 721)
    wave = _latitude_deg(inclination, angle)

    for ax, plane_id in zip(axes, planes, strict=True):
        satellites = [s for s in scenario.design.satellites if s.plane_id == plane_id]
        ax.plot(angle, wave, color="#adb5bd", linewidth=1.2, zorder=1)

        for batch in sorted({s.launch_batch for s in satellites}):
            along = np.array(
                [(s.slot_deg + phase[plane_id]) % 360 for s in satellites
                 if s.launch_batch == batch]
            )
            ax.scatter(
                along, _latitude_deg(inclination, along),
                s=55, zorder=3, label=f"очередь {batch}",
                color=PLANE_COLOURS[(batch - 1) % len(PLANE_COLOURS)],
                edgecolor="white", linewidth=0.6,
            )

        ax.set_ylabel(f"{plane_id}\nширота, °")
        ax.set_ylim(-118, 118)
        ax.set_yticks([-90, 0, 90])
        ax.legend(frameon=False, loc="upper right", fontsize=9, ncol=2)

    # Расстояние между соседями подписывается на первой панели: это то самое число,
    # из которого потом берётся хорда 2700 км на следующем графике.
    first = [s for s in scenario.design.satellites if s.plane_id == planes[0]]
    if len(first) > 1:
        spacing = 360.0 / len(first)
        # Подпись уходит в левый нижний угол: там кривая уже ушла вверх и место пустое.
        axes[0].annotate(f"соседи через {spacing:.1f}°", xy=(8, -66), fontsize=9, color=GREY)

    axes[-1].set_xlabel("положение аппарата вдоль орбиты, градусы")
    axes[-1].set_xticks(range(0, 361, 45))
    # Заголовок читается с картинки, а не назначен заранее: на сценарии, где очереди
    # идут поперёк плоскостей, прежний текст противоречил бы собственным точкам.
    axes[0].set_title(
        "Очередь запуска совпадает с орбитальной плоскостью\n"
        "в каждой плоскости один цвет: первая очередь — это одна плоскость целиком"
        if found.batches_match_planes
        else "Очередь запуска и плоскость — разные вещи\n"
        "в каждой плоскости встречаются аппараты из разных очередей",
        loc="left", fontsize=12,
    )

    fig.savefig(path)
    plt.close(fig)
    return (
        "очередь запуска совпадает с плоскостью"
        if found.batches_match_planes
        else "не совпадает: в плоскости встречается больше одной очереди"
    )


def _latitude_deg(inclination_deg: float, along_deg: np.ndarray) -> np.ndarray:
    """Широта аппарата по пройденному вдоль орбиты углу: arcsin(sin i · sin u)."""

    return np.degrees(
        np.arcsin(np.sin(np.radians(inclination_deg)) * np.sin(np.radians(along_deg)))
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


def figure_delivery(scenarios: dict[str, object], path: Path) -> str:
    """
    Что даёт разрешение подождать, по всем четырём сценариям сразу.

    Ось задержки логарифмическая по смыслу, но подписи ставятся по посчитанным порогам,
    а не по секундам: между ними никто ничего не мерил, и рисовать там линию было бы
    обещанием, которого расчёт не давал. Поэтому точки соединены, но подписаны только
    измеренные пороги.
    """

    fig, ax = plt.subplots(figsize=(9, 4.2))
    deadlines = None
    notes = []

    for index, (label, scenario) in enumerate(scenarios.items()):
        report = delivery_report(scenario)
        deadlines = report.deadlines_s
        shares = [100 * report.worst_share_within(d) for d in deadlines]
        ax.plot(
            range(len(deadlines)),
            shares,
            marker="o",
            color=PLANE_COLOURS[index % len(PLANE_COLOURS)],
            label=label,
        )
        notes.append(f"{label}: {shares[0]:.1f} → {shares[2]:.1f} %")

    target = 100 * next(iter(scenarios.values())).environment.target_availability
    ax.axhline(target, color=BAD, linestyle="--", linewidth=1)
    ax.text(0.05, target + 1.5, f"цель {target:.0f} %", color=BAD, fontsize=9)

    ax.set_xticks(range(len(deadlines)))
    ax.set_xticklabels(["сразу" if d == 0 else _minutes(d) for d in deadlines])
    ax.set_ylim(0, 104)
    ax.set_xlabel("допустимая задержка доставки")
    ax.set_ylabel("доля отсчётов, %")
    ax.set_title("Доступность худшего пункта, если данные разрешено подождать")
    ax.legend(loc="lower right", framealpha=0.95, fontsize=9)

    fig.savefig(path)
    plt.close(fig)
    return "; ".join(notes)


def figure_degradation(scenario, path: Path) -> str:
    """
    Кривая деградации с полосой разброса наборов и отмеченной границей цели.

    Ось начинается от наименьшего измеренного значения, а не от нуля: интерес здесь в
    том, где кривая пересекает цель, и растянутый до нуля график этот момент прячет.
    Обрезка честная, потому что нижняя граница подписана.
    """

    curve = degradation_curve(scenario)
    failures = [p.failures for p in curve.points]
    mean = [100 * p.mean_worst_availability for p in curve.points]
    low = [100 * p.worst_worst_availability for p in curve.points]
    high = [100 * p.best_worst_availability for p in curve.points]
    target = 100 * curve.target_availability

    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.fill_between(failures, low, high, color=ACCENT, alpha=0.18, label="разброс наборов")
    ax.plot(failures, mean, color=ACCENT, marker="o", label="в среднем по наборам")
    ax.axhline(target, color=BAD, linestyle="--", linewidth=1)
    ax.text(failures[-1] * 0.45, target + 0.6, f"цель {target:.0f} %", color=BAD, fontsize=9)

    kept = [f for f, p in zip(failures, curve.points, strict=True) if p.meets_target_share == 1]
    if kept:
        # Подпись ставится вверху, у самого начала кривой: внизу её накрывает легенда.
        ax.axvline(max(kept), color=GOOD, linewidth=1)
        ax.text(
            max(kept) + 0.2,
            max(high) - 1.5,
            f"переносит {max(kept)} отказа",
            color=GOOD,
            fontsize=9,
        )

    ax.set_xlabel("выведено из строя аппаратов (случайный набор)")
    ax.set_ylabel("доступность худшего пункта, %")
    ax.set_title(
        f"Деградация при случайных отказах: −{curve.slope_pp_per_satellite:.1f} п.п. за аппарат"
    )
    ax.set_xticks(failures)
    ax.legend(loc="lower left", framealpha=0.95, fontsize=9)

    fig.savefig(path)
    plt.close(fig)
    return (
        f"переносит {curve.tolerated_failures}, наклон {curve.slope_pp_per_satellite:.2f} п.п., "
        f"отклонение от прямой {curve.linear_fit_error_pp:.2f} п.п."
    )


def figure_redundancy(scenarios: dict[str, object], path: Path) -> str:
    """
    Сколько независимых маршрутов есть в каждый момент, долями отсчётов.

    Столбцы сложены, потому что доли дают в сумме единицу и вопрос ровно в том, как
    сутки делятся между «пути нет», «путь один» и «есть запас».
    """

    labels: list[str] = []
    parts = {0: [], 1: [], 2: []}
    for label, scenario in scenarios.items():
        for client in redundancy_report(scenario).clients:
            labels.append(f"{label}\n{client.client_id}")
            parts[0].append(100 * client.no_path_share)
            parts[1].append(100 * client.single_path_share)
            parts[2].append(100 * client.redundant_share)

    fig, ax = plt.subplots(figsize=(9, 4.0))
    bottom = np.zeros(len(labels))
    for key, colour, name in (
        (2, GOOD, "два маршрута и больше"),
        (1, "#f0a30a", "маршрут ровно один"),
        (0, BAD, "маршрута нет"),
    ):
        values = np.array(parts[key])
        ax.bar(labels, values, bottom=bottom, color=colour, label=name)
        bottom += values

    ax.set_ylim(0, 100)
    ax.set_ylabel("доля отсчётов, %")
    ax.set_title("Запас маршрутов: на скольких аппаратах держится связь")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=3, fontsize=9)
    ax.tick_params(axis="x", labelsize=9)

    fig.savefig(path)
    plt.close(fig)
    return f"единственный маршрут занимает до {max(parts[1]):.1f} % отсчётов"


def figure_families(scenario, path: Path) -> str:
    """Кривая по разносу плоскостей: два семейства как два максимума."""

    report = spacing_curve(scenario)
    spacings = [p.spacing_deg for p in report.points]
    shares = [100 * p.worst_availability for p in report.points]

    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.plot(spacings, shares, color=ACCENT, linewidth=1.8)

    for rule, name in (
        (report.star_spacing_deg, "180°/P — звезда"),
        (report.delta_spacing_deg, "360°/P — дельта"),
    ):
        ax.axvline(rule, color=GREY, linestyle=":", linewidth=1)
        ax.text(rule + 1.5, min(shares) + 2, name, color=GREY, fontsize=9, rotation=90)

    for peak in (report.star_best, report.delta_best):
        if peak is None:
            continue
        ax.plot(peak.spacing_deg, 100 * peak.worst_availability, "o", color=GOOD, markersize=8)
        ax.annotate(
            f"{peak.spacing_deg:.0f}° → {100 * peak.worst_availability:.1f} %",
            (peak.spacing_deg, 100 * peak.worst_availability),
            textcoords="offset points",
            xytext=(0, 12),
            ha="center",
            color=GOOD,
            fontsize=9,
        )

    if report.supplied_spacing_deg is not None:
        supplied = min(
            report.points, key=lambda p: abs(p.spacing_deg - report.supplied_spacing_deg)
        )
        ax.plot(
            supplied.spacing_deg,
            100 * supplied.worst_availability,
            "o",
            markerfacecolor="none",
            markeredgecolor=BAD,
            markersize=12,
            markeredgewidth=2,
        )
        ax.annotate(
            "выданная конфигурация",
            (supplied.spacing_deg, 100 * supplied.worst_availability),
            textcoords="offset points",
            xytext=(-10, -24),
            ha="right",
            color=BAD,
            fontsize=9,
        )

    ax.set_xlabel("равномерный разнос плоскостей, градусы")
    ax.set_ylabel("доступность худшего пункта, %")
    # Запас сверху нужен подписям максимумов: без него верхняя уезжает в заголовок.
    ax.set_ylim(top=max(shares) + 9)
    ax.set_title("Два классических семейства видны как два максимума")

    fig.savefig(path)
    plt.close(fig)
    star, delta = report.star_best, report.delta_best
    return (
        f"звезда {star.spacing_deg:.0f}° → {100 * star.worst_availability:.1f} %, "
        f"дельта {delta.spacing_deg:.0f}° → {100 * delta.worst_availability:.1f} %"
    )


def figure_placement(scenario, path: Path) -> str:
    """Поверхность прироста от второй точки приземления, широта против долготы."""

    report = placement_grid(scenario)
    grid = 100 * report.grid - 100 * report.baseline_worst_availability

    fig, ax = plt.subplots(figsize=(9, 3.6))
    image = ax.imshow(
        grid,
        origin="lower",
        aspect="auto",
        cmap="YlGn",
        extent=(
            report.lon_deg[0],
            report.lon_deg[-1],
            report.lat_deg[0],
            report.lat_deg[-1],
        ),
    )
    fig.colorbar(image, ax=ax, label="прирост худшему пункту, п.п.")

    for site in scenario.ground_sites:
        ax.plot(site.lon_deg, site.lat_deg, "x" if site.role == "gateway" else "+", color="#1a1a1a")
        ax.annotate(
            site.id,
            (site.lon_deg, site.lat_deg),
            textcoords="offset points",
            xytext=(4, 4),
            fontsize=8,
            color="#1a1a1a",
        )

    best = report.best
    if best is not None:
        ax.plot(best.lon_deg, best.lat_deg, "o", markerfacecolor="none",
                markeredgecolor=BAD, markersize=12, markeredgewidth=2)

    ax.set_xlabel("долгота, градусы")
    ax.set_ylabel("широта, градусы")
    ax.set_title("Где стоило бы поставить вторую точку приземления")

    fig.savefig(path)
    plt.close(fig)
    return (
        f"лучшее {best.lat_deg:.0f}°/{best.lon_deg:.0f}° даёт "
        f"+{best.gain_pp:.1f} п.п., худшее место +{min(p.gain_pp for p in report.points):.1f}"
    )


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} мин" if seconds < 3600 else f"{seconds // 3600} ч"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR / "figures")
    arguments = parser.parse_args()
    arguments.out.mkdir(parents=True, exist_ok=True)

    full = load_scenario(SCENARIOS_DIR / "01_full_constellation.json")
    outages = load_scenario(SCENARIOS_DIR / "03_satellite_outages.json")
    link_range = load_scenario(SCENARIOS_DIR / "04_link_range.json")
    first = load_scenario(SCENARIOS_DIR / "02_first_launch.json")

    four = {
        "01 полная": full,
        "02 первая очередь": first,
        "03 отказы": outages,
        "04 дальность 2000": link_range,
    }

    work = [
        ("01_ocheredi_i_ploskosti.png", lambda p: figure_batches(full, p)),
        ("02_koltso_v_ploskosti.png", lambda p: figure_ring(full, p)),
        ("03_geografiya.png", lambda p: figure_geography(full, p)),
        ("04_vidimost.png", lambda p: figure_visibility(full, p)),
        ("05_otkazy_po_ploskostyam.png", lambda p: figure_failures(outages, p)),
        ("06_shag_setki.png", lambda p: figure_grid(full, p)),
        ("07_dopustimaya_zaderzhka.png", lambda p: figure_delivery(four, p)),
        ("08_krivaya_degradacii.png", lambda p: figure_degradation(full, p)),
        (
            "09_zapas_marshrutov.png",
            lambda p: figure_redundancy({"01": full, "04": link_range}, p),
        ),
        ("10_semeystva_raznosa.png", lambda p: figure_families(full, p)),
        ("11_mesto_dlya_shlyuza.png", lambda p: figure_placement(link_range, p)),
    ]

    for name, build in work:
        note = build(arguments.out / name)
        print(f"  {name}: {note}")

    print(f"записано в {arguments.out}")


if __name__ == "__main__":
    main()
