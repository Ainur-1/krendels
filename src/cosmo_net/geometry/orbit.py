"""
Where every satellite is, at every step of the grid, in one array.

The whole horizon is computed at once rather than a step at a time. A default run
is 720 steps over 48 satellites: as a loop that is 34 560 evaluations of the same
six trigonometric functions, and as an array it is six calls. The configuration
sweep runs 65 of these behind one button press, which is what makes the difference
worth having rather than merely tidy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from cosmo_net.config import (
    EARTH_ANGULAR_RATE_RAD_S,
    EARTH_RADIUS_KM,
    MU_KM3_S2,
)
from cosmo_net.scenario.schema import GroundSite, Scenario


@dataclass(frozen=True)
class Trajectory:
    """Satellite positions over the calculation grid, in both frames the model needs."""

    times_s: np.ndarray
    """(T,) the grid, in seconds from the start of the run."""

    satellite_ids: list[str]
    """(N,) satellite identifiers, in the order the scenario declares them."""

    eci_km: np.ndarray
    """(T, N, 3) inertial frame. Orbits are circles here; the Earth is what turns."""

    ecef_km: np.ndarray
    """(T, N, 3) Earth-fixed frame. Ground sites are fixed here, so links live here."""

    @property
    def orbital_period_s(self) -> float:
        """One revolution. 5730 s at 550 km, so 15.08 revolutions in a 24-hour horizon."""

        r = float(np.linalg.norm(self.eci_km[0, 0]))
        return 2 * math.pi * math.sqrt(r**3 / MU_KM3_S2)


def orbit_radius_km(altitude_km: float) -> float:
    return EARTH_RADIUS_KM + altitude_km


def mean_motion_rad_s(altitude_km: float) -> float:
    """Angular rate along a circular orbit, √(μ/r³)."""

    return math.sqrt(MU_KM3_S2 / orbit_radius_km(altitude_km) ** 3)


def compute_trajectory(scenario: Scenario, times_s: np.ndarray | None = None) -> Trajectory:
    """
    Positions of every satellite over the grid, whether or not it is in service.

    A satellite that has not launched yet, or is in an outage, still has a position:
    the case is explicit that a failed craft keeps its computed place and only drops
    out of the link set. Which of them count is decided in `contacts`, not here.
    """

    env = scenario.environment
    grid = scenario.times if times_s is None else times_s
    t = np.asarray(grid, dtype=float)

    planes = {p.id: p for p in scenario.design.planes}
    r = orbit_radius_km(env.altitude_km)
    n = mean_motion_rad_s(env.altitude_km)
    cos_i = math.cos(math.radians(env.inclination_deg))
    sin_i = math.sin(math.radians(env.inclination_deg))

    # Argument of latitude at t = 0, one per satellite: its own slot plus the shift
    # applied to its whole plane. Phasing moves satellites along the orbit; RAAN,
    # below, turns the orbit itself. They are the two levers the interface exposes.
    u0 = np.array(
        [
            math.radians(sat.slot_deg + planes[sat.plane_id].phase_deg)
            for sat in scenario.design.satellites
        ]
    )
    raan = np.array(
        [math.radians(planes[sat.plane_id].raan_deg) for sat in scenario.design.satellites]
    )

    u = u0[None, :] + n * t[:, None]
    cu, su = np.cos(u), np.sin(u)
    co, so = np.cos(raan)[None, :], np.sin(raan)[None, :]

    eci = r * np.stack(
        (co * cu - so * su * cos_i, so * cu + co * su * cos_i, su * sin_i),
        axis=-1,
    )

    # The Earth has turned by θ since the start of the run, so the same point in
    # inertial space is at a different longitude. Rotating the satellites by −θ is
    # the cheaper half of the equivalent pair, and it leaves ground sites fixed.
    theta = math.radians(env.earth_angle0_deg) + EARTH_ANGULAR_RATE_RAD_S * t
    ct, st = np.cos(theta)[:, None], np.sin(theta)[:, None]
    ecef = np.stack(
        (
            ct * eci[..., 0] + st * eci[..., 1],
            -st * eci[..., 0] + ct * eci[..., 1],
            eci[..., 2],
        ),
        axis=-1,
    )

    return Trajectory(
        times_s=t,
        satellite_ids=[sat.id for sat in scenario.design.satellites],
        eci_km=eci,
        ecef_km=ecef,
    )


def ground_position_km(site: GroundSite) -> np.ndarray:
    """A ground site in the Earth-fixed frame. Spherical Earth, so latitude is geocentric."""

    lat, lon = math.radians(site.lat_deg), math.radians(site.lon_deg)
    return EARTH_RADIUS_KM * np.array(
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)]
    )


def ground_positions_km(sites: list[GroundSite]) -> np.ndarray:
    """(G, 3) for a list of sites, in the order given."""

    if not sites:
        return np.zeros((0, 3))
    return np.stack([ground_position_km(s) for s in sites])
