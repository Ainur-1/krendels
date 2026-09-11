"""
Physical constants, schema identifiers and the paths every other module resolves against.

The constants are fixed by the case description rather than chosen here, so they
carry the value the scenarios were generated with and nothing may override them:
a run computed against a different Earth radius is not comparable with the
reference module's output.
"""

from __future__ import annotations

import math
from pathlib import Path

# Earth radius, km. Spherical Earth throughout - the model has no oblateness and
# therefore no J2, so orbital planes do not precess over the 24-hour horizon.
EARTH_RADIUS_KM = 6371.0

# Standard gravitational parameter, km^3/s^2.
MU_KM3_S2 = 398600.435507

# Sidereal rotation period, s. The Earth turns by this much in one revolution, and
# it is what makes a ground site sweep under a fixed orbital plane.
EARTH_ROTATION_PERIOD_S = 86164.09054

EARTH_ANGULAR_RATE_RAD_S = 2 * math.pi / EARTH_ROTATION_PERIOD_S

# The scenario format this service reads, and the result format it writes. Both are
# fixed by the case: a file announcing anything else is rejected rather than guessed at.
SCENARIO_SCHEMA_VERSION = "cosmo-A-1.0"
RESULT_SCHEMA_VERSION = "cosmo-A-result-1.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = PROJECT_ROOT / "data" / "scenarios"
REPORTS_DIR = PROJECT_ROOT / "reports"
METRICS_DIR = REPORTS_DIR / "metrics"

# The compiled frontend. Written by `npm run build`, absent in a fresh clone, and the
# service reports that plainly instead of failing to start - the API is useful on its own.
STATIC_DIR = Path(__file__).resolve().parent / "serving" / "static"
