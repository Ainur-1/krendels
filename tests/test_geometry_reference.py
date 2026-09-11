"""
The geometry agrees with `reference/geometry.py`, which is the organisers' own module.

This is the test that matters most in the whole suite. «Корректность расчётов» is
15 points and is judged against exactly this module, so the package is not allowed
to be *nearly* right: positions have to land in the same place and, more
importantly, the link sets have to contain the same links. A difference of a metre
in a position is invisible, but it moves a satellite across the elevation threshold
at some step of some day and silently changes an availability figure.

The two implementations share no code. Ours computes the whole horizon as arrays;
theirs computes one step at a time. They agree to 1e-13 km, which is floating-point
noise and not a model difference.
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

# Steps chosen to catch the things a single t = 0 comparison cannot: the first
# outage boundary in scenario 03 (21 600 s, where craft drop out), a time that is
# not a multiple of the step, and the last point on the grid.
SAMPLE_TIMES = [0, 120, 7_777, 21_600, 43_200, 60_000, 86_280]

SCENARIOS = bundled_scenarios()


@pytest.fixture(scope="module")
def reference():
    """The organisers' module, imported from `reference/` rather than installed."""

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
        # 1e-6 km is a millimetre. The observed difference is 1e-13 km; the margin
        # is here so a change of summation order does not fail the suite.
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
                # The reference reports elevations for in-service craft only.
                if satellite_id in theirs:
                    assert abs(contacts.elevation_deg[k, g, n] - theirs[satellite_id]) < 1e-9


def test_grid_excludes_the_right_end():
    """
    720 steps, not 721.

    The last point is 86 280 s. Counting the horizon itself as a step would put every
    share in the results out by one part in 720 and, worse, would make two runs on
    different grids look comparable when they are not.
    """

    scenario = load_scenario(SCENARIOS[0])
    assert scenario.times[0] == 0
    assert scenario.times[-1] == 86_280
    assert len(scenario.times) == 720
