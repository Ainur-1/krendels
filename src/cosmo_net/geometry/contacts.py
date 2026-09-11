"""
Which links exist, at every step: satellite to satellite, and satellite to ground.

Two rules, both from the case description. A ground link needs the satellite at or
above the elevation threshold. An inter-satellite link needs the two craft closer
than the range limit *and* the segment between them clear of the Earth — being in
range is not enough when the planet is in the way, which is exactly what happens
to craft on opposite sides of a polar orbit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cosmo_net.config import EARTH_RADIUS_KM
from cosmo_net.geometry.orbit import Trajectory, ground_positions_km
from cosmo_net.scenario.schema import Scenario


@dataclass(frozen=True)
class ContactSeries:
    """Every link in the network, for every step of the grid."""

    times_s: np.ndarray
    satellite_ids: list[str]
    ground_ids: list[str]
    ground_roles: list[str]

    active: np.ndarray
    """(T, N) in service: launched by this stage and not inside an outage."""

    pair_index: np.ndarray
    """(P, 2) the satellite pairs, i < j, fixed for the whole run."""

    isl_geometric: np.ndarray
    """(T, P) in range and not looking through the Earth. Says nothing about service."""

    isl_open: np.ndarray
    """(T, P) usable: geometric, and both ends in service."""

    isl_distance_km: np.ndarray
    """(T, P) separation, whether or not the link is usable."""

    elevation_deg: np.ndarray
    """(T, G, N) elevation of each satellite over each ground site, in degrees."""

    elevation_ok: np.ndarray
    """(T, G, N) at or above the threshold. Again independent of who is in service."""

    ground_open: np.ndarray
    """(T, G, N) the ground link is usable: high enough, in service, site reachable."""

    ground_distance_km: np.ndarray
    """(T, G, N) slant range from the site to the satellite."""

    def with_service(self, active: np.ndarray, offline: np.ndarray) -> ContactSeries:
        """
        The same geometry with a different set of craft and stations in service.

        Positions, distances and elevations depend only on the orbits, so a study
        that changes nothing but who is switched on — the satellite knockout does
        exactly that, 48 times — can reuse them and redo the boolean masks alone.
        That is the 45 ms half of a run; what is left is a few hundred microseconds.
        """

        i, j = self.pair_index[:, 0], self.pair_index[:, 1]
        return ContactSeries(
            times_s=self.times_s,
            satellite_ids=self.satellite_ids,
            ground_ids=self.ground_ids,
            ground_roles=self.ground_roles,
            active=active,
            pair_index=self.pair_index,
            isl_geometric=self.isl_geometric,
            isl_open=self.isl_geometric & active[:, i] & active[:, j],
            isl_distance_km=self.isl_distance_km,
            elevation_deg=self.elevation_deg,
            elevation_ok=self.elevation_ok,
            ground_open=self.elevation_ok & active[:, None, :] & ~offline[:, :, None],
            ground_distance_km=self.ground_distance_km,
        )


def active_mask(scenario: Scenario, times_s: np.ndarray) -> np.ndarray:
    """
    (T, N) which satellites are in service at each step.

    Two independent reasons to be out: not launched yet at the selected stage, or
    inside a declared outage. Outage intervals are half-open — `[start_s, end_s)` —
    so a craft down from 21 600 s is already down at 21 600 and back at `end_s`.
    """

    design = scenario.design
    launched = np.array(
        [sat.launch_batch <= design.launch_stage for sat in design.satellites], dtype=bool
    )
    active = np.repeat(launched[None, :], len(times_s), axis=0)

    index = {sat.id: k for k, sat in enumerate(design.satellites)}
    for failure in scenario.failures:
        k = index.get(failure.satellite_id)
        if k is None:
            continue
        down = (times_s >= failure.start_s) & (times_s < failure.end_s)
        active[down, k] = False
    return active


def gateway_offline_mask(scenario: Scenario, times_s: np.ndarray) -> np.ndarray:
    """(T, G) which ground sites are unreachable at each step. Only gateways can be."""

    offline = np.zeros((len(times_s), len(scenario.ground_sites)), dtype=bool)
    index = {site.id: g for g, site in enumerate(scenario.ground_sites)}
    for outage in scenario.gateway_outages:
        g = index.get(outage.gateway_id)
        if g is None:
            continue
        down = (times_s >= outage.start_s) & (times_s < outage.end_s)
        offline[down, g] = True
    return offline


def compute_contacts(scenario: Scenario, trajectory: Trajectory) -> ContactSeries:
    """Build the full link set for the run from positions the trajectory already holds."""

    env = scenario.environment
    times = trajectory.times_s
    ecef = trajectory.ecef_km
    n_sat = ecef.shape[1]

    active = active_mask(scenario, times)
    offline = gateway_offline_mask(scenario, times)

    i, j = np.triu_indices(n_sat, k=1)
    a = ecef[:, i, :]
    b = ecef[:, j, :]
    delta = b - a
    distance = np.linalg.norm(delta, axis=-1)

    # Closest approach of the segment to the centre of the Earth. Clipping the
    # projection to [0, 1] keeps it on the segment: without it a pair whose infinite
    # line passes through the planet would be rejected even when both craft sit on
    # the same side of it and see each other perfectly well.
    denominator = np.maximum(np.sum(delta * delta, axis=-1), 1e-12)
    lam = np.clip(-np.sum(a * delta, axis=-1) / denominator, 0.0, 1.0)
    closest = np.linalg.norm(a + lam[..., None] * delta, axis=-1)

    isl_geometric = (distance < env.isl_range_km) & (closest > EARTH_RADIUS_KM)
    isl_open = isl_geometric & active[:, i] & active[:, j]

    ground = ground_positions_km(scenario.ground_sites)
    difference = ecef[:, None, :, :] - ground[None, :, None, :]
    slant = np.linalg.norm(difference, axis=-1)

    # Elevation above the local horizon: the angle between the line to the satellite
    # and the plane perpendicular to the site's own radius vector. On a spherical
    # Earth that radius vector is the local vertical, which is what makes the
    # one-line form below equal to the usual up/horizontal arctangent.
    up = ground / EARTH_RADIUS_KM
    sine = np.sum(difference * up[None, :, None, :], axis=-1) / np.maximum(slant, 1e-12)
    elevation = np.degrees(np.arcsin(np.clip(sine, -1.0, 1.0)))

    elevation_ok = elevation >= env.min_elevation_deg
    ground_open = elevation_ok & active[:, None, :] & ~offline[:, :, None]

    return ContactSeries(
        times_s=times,
        satellite_ids=list(trajectory.satellite_ids),
        ground_ids=[site.id for site in scenario.ground_sites],
        ground_roles=[site.role for site in scenario.ground_sites],
        active=active,
        pair_index=np.stack((i, j), axis=1),
        isl_geometric=isl_geometric,
        isl_open=isl_open,
        isl_distance_km=distance,
        elevation_deg=elevation,
        elevation_ok=elevation_ok,
        ground_open=ground_open,
        ground_distance_km=slant,
    )
