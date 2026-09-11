"""
Геометрия совпадает с `reference/geometry.py` — собственным модулем организаторов.

Это самый важный тест во всём наборе. «Корректность расчётов» — это 15 баллов, и
проверяется она именно против этого модуля, поэтому пакету нельзя быть **почти**
правым: положения должны попадать в ту же точку и, что важнее, составы связей должны
содержать те же связи. Разница в метр в положении незаметна, но на каком-то отсчёте
какого-то дня она переводит аппарат через порог угла места и молча меняет цифру
доступности.

Две реализации не делят ни строчки кода. Наша считает весь горизонт массивами, их —
по одному отсчёту. Совпадают они до 1e-13 км, то есть до шума чисел с плавающей
точкой, а не до разницы в модели.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from cosmo_net.config import PROJECT_ROOT
from cosmo_net.geometry.contacts import compute_contacts
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.io import bundled_scenarios, load_scenario

sys.path.insert(0, str(PROJECT_ROOT / "reference"))

# Отсчёты выбраны так, чтобы поймать то, что сравнение в одной точке t = 0 поймать
# не может: границу первого отказа в сценарии 03 (21 600 с, где выбывают аппараты),
# момент, не кратный шагу, и последнюю точку сетки.
SAMPLE_TIMES = [0, 120, 7_777, 21_600, 43_200, 60_000, 86_280]

SCENARIOS = bundled_scenarios()


@pytest.fixture(scope="module")
def reference():
    """Модуль организаторов, подключаемый из `reference/`, а не установленный пакетом."""

    import geometry

    return geometry


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_positions_match_reference(reference, path):
    scenario = load_scenario(path)
    raw = reference.load(str(path))
    trajectory = compute_trajectory(scenario, np.array(SAMPLE_TIMES, dtype=float))

    for k, t in enumerate(SAMPLE_TIMES):
        snapshot = reference.snapshot(raw, float(t))
        theirs = np.array([[s["x_km"], s["y_km"], s["z_km"]] for s in snapshot["satellites"]])
        # 1e-6 км — это миллиметр. Наблюдаемая разница 1e-13 км; запас оставлен,
        # чтобы смена порядка суммирования не роняла набор тестов.
        assert np.abs(trajectory.ecef_km[k] - theirs).max() < 1e-6


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_link_sets_match_reference(reference, path):
    scenario = load_scenario(path)
    raw = reference.load(str(path))
    times = np.array(SAMPLE_TIMES, dtype=float)
    contacts = compute_contacts(scenario, compute_trajectory(scenario, times))
    satellites = set(contacts.satellite_ids)

    for k, t in enumerate(SAMPLE_TIMES):
        snapshot = reference.snapshot(raw, float(t))

        theirs_active = np.array([s["active"] for s in snapshot["satellites"]])
        assert (contacts.active[k] == theirs_active).all(), f"active set differs at {t} s"

        ours = {
            frozenset((contacts.satellite_ids[i], contacts.satellite_ids[j]))
            for (i, j), is_open in zip(contacts.pair_index, contacts.isl_open[k], strict=True)
            if is_open
        }
        theirs = {
            frozenset((edge[0], edge[1]))
            for edge in snapshot["edges"]
            if edge[0] in satellites and edge[1] in satellites
        }
        assert ours == theirs, f"inter-satellite links differ at {t} s"

        for g, site_id in enumerate(contacts.ground_ids):
            ours_ground = {
                contacts.satellite_ids[n] for n in np.where(contacts.ground_open[k, g])[0]
            }
            theirs_ground = {edge[1] for edge in snapshot["edges"] if edge[0] == site_id}
            assert ours_ground == theirs_ground, f"{site_id} sees different craft at {t} s"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_elevations_match_reference(reference, path):
    scenario = load_scenario(path)
    raw = reference.load(str(path))
    times = np.array(SAMPLE_TIMES, dtype=float)
    contacts = compute_contacts(scenario, compute_trajectory(scenario, times))

    for k, t in enumerate(SAMPLE_TIMES):
        snapshot = reference.snapshot(raw, float(t))
        for g, site_id in enumerate(contacts.ground_ids):
            theirs = snapshot["elevation_deg"][site_id]
            for n, satellite_id in enumerate(contacts.satellite_ids):
                # Эталон сообщает углы места только для аппаратов в строю.
                if satellite_id in theirs:
                    assert abs(contacts.elevation_deg[k, g, n] - theirs[satellite_id]) < 1e-9


def test_grid_excludes_the_right_end():
    """
    720 отсчётов, а не 721.

    Последняя точка — 86 280 с. Если считать отсчётом сам горизонт, каждая доля в
    результатах сместится на одну семьсот двадцатую, и, что хуже, два прогона на
    разных сетках будут выглядеть сопоставимыми, не будучи таковыми.
    """

    scenario = load_scenario(SCENARIOS[0])
    assert scenario.times[0] == 0
    assert scenario.times[-1] == 86_280
    assert len(scenario.times) == 720
