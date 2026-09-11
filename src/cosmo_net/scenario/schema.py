"""
The cosmo-A-1.0 scenario, as typed objects, and the rules a file has to satisfy to be one.

Every rule here is one the reference module `reference/geometry.py` enforces, with
one difference that matters for the interface: `validate()` there raises on the
first problem it meets, and the case asks the service to tell the user *which*
field or object is wrong. So parsing collects every problem it can find and
reports them together, addressed by path — `design.satellites[12].plane_id`
rather than "Invalid satellite".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from cosmo_net.config import SCENARIO_SCHEMA_VERSION

# Extra keys are kept rather than rejected: a scenario "может содержать другие
# названия и идентификаторы", and a judge's file carrying an extra annotation is
# still a valid scenario. Non-finite floats are refused everywhere, which is what
# the reference module's `finite()` check amounts to.
_MODEL = ConfigDict(allow_inf_nan=False, extra="allow", populate_by_name=True)


@dataclass(frozen=True)
class ScenarioError:
    """One problem with an uploaded file, addressed by the path of the field that carries it."""

    field: str
    code: str
    message: str


class ScenarioInvalid(Exception):
    """Raised when a file is not a usable scenario. Carries every problem found, not the first."""

    def __init__(self, errors: list[ScenarioError]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) in scenario: {errors[0].message}")


class Meta(BaseModel):
    model_config = _MODEL

    id: str = "scenario"
    title: str = ""


class Environment(BaseModel):
    model_config = _MODEL

    # Bounds are the reference module's, not physics': it refuses anything outside
    # them, so a file it would reject must not be accepted here either.
    altitude_km: float = Field(ge=200, le=1200)
    inclination_deg: float = Field(gt=0, le=180)
    earth_angle0_deg: float

    # Strict integers. `"horizon_s": 86400.0` is a float in JSON and the reference
    # module rejects it on `isinstance(..., int)`; accepting it here would mean a
    # file that works in the service and fails against the organisers' own tool.
    horizon_s: StrictInt = Field(gt=0, le=172_800)
    step_s: StrictInt = Field(gt=0)

    min_elevation_deg: float = Field(ge=0, lt=90)
    isl_range_km: float = Field(gt=0, le=10_000)
    target_availability: float = Field(ge=0, le=1)


class Plane(BaseModel):
    model_config = _MODEL

    id: str
    # Half-open on purpose: 360° is the same orientation as 0° and the reference
    # module rejects it, so a slider in the interface stops at 359.9.
    raan_deg: float = Field(ge=0, lt=360)
    phase_deg: float = Field(ge=0, lt=360)


class Satellite(BaseModel):
    model_config = _MODEL

    id: str
    plane_id: str
    # Unconstrained beyond being finite, matching the reference module: a slot of
    # 370° or -20° is a legal way to write a position and wraps on its own.
    slot_deg: float
    launch_batch: Literal[1, 2, 3]


class Design(BaseModel):
    model_config = _MODEL

    launch_stage: Literal[1, 2, 3]
    planes: list[Plane] = Field(min_length=1)
    satellites: list[Satellite] = Field(min_length=1)


class GroundSite(BaseModel):
    model_config = _MODEL

    id: str
    name: str = ""
    role: Literal["client", "gateway"]
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)


class Failure(BaseModel):
    """Off the air over `[start_s, end_s)`. The craft keeps its position and loses its links."""

    model_config = _MODEL

    satellite_id: str
    start_s: float
    end_s: float


class GatewayOutage(BaseModel):
    """A gateway is unreachable over `[start_s, end_s)`, so no ground link lands on it."""

    model_config = _MODEL

    gateway_id: str
    start_s: float
    end_s: float


class Scenario(BaseModel):
    model_config = _MODEL

    schema_version: str
    meta: Meta = Field(default_factory=Meta)
    environment: Environment
    design: Design
    ground_sites: list[GroundSite]
    failures: list[Failure] = Field(default_factory=list)
    gateway_outages: list[GatewayOutage] = Field(default_factory=list)

    @property
    def clients(self) -> list[GroundSite]:
        return [g for g in self.ground_sites if g.role == "client"]

    @property
    def gateways(self) -> list[GroundSite]:
        return [g for g in self.ground_sites if g.role == "gateway"]

    @property
    def times(self) -> list[int]:
        """
        The calculation grid: 0, step, …, horizon − step.

        The right end is excluded, so a 86 400 s horizon at 120 s is 720 steps and
        not 721. Every share in the results is a count of these divided by their number.
        """

        env = self.environment
        return list(range(0, env.horizon_s, env.step_s))


def parse_scenario(data: Any) -> Scenario:
    """
    Turn parsed JSON into a `Scenario`, or raise `ScenarioInvalid` listing everything wrong with it.

    Shape problems come from pydantic and arrive addressed by path already. The
    checks that follow are the ones no single field can make on its own: references
    that have to resolve, identifiers that have to be unique across two different
    lists, intervals that have to sit inside a horizon declared elsewhere in the file.
    """

    if not isinstance(data, dict):
        raise ScenarioInvalid(
            [ScenarioError("", "not_an_object", "Scenario must be a JSON object")]
        )

    # Checked before anything else: a file announcing another version may use the
    # same field names for different things, and guessing at that is worse than refusing.
    version = data.get("schema_version")
    if version != SCENARIO_SCHEMA_VERSION:
        raise ScenarioInvalid(
            [
                ScenarioError(
                    "schema_version",
                    "unsupported_schema",
                    f"Expected {SCENARIO_SCHEMA_VERSION}, got {version!r}",
                )
            ]
        )

    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        raise ScenarioInvalid([_from_pydantic(e) for e in exc.errors()]) from exc

    errors = cross_check(scenario)
    if errors:
        raise ScenarioInvalid(errors)
    return scenario


def cross_check(scenario: Scenario) -> list[ScenarioError]:
    """Every rule that needs more than one part of the file to state."""

    errors: list[ScenarioError] = []
    env, design = scenario.environment, scenario.design

    if env.step_s > env.horizon_s:
        errors.append(
            ScenarioError(
                "environment.step_s",
                "step_exceeds_horizon",
                f"Step {env.step_s} s is longer than the horizon {env.horizon_s} s",
            )
        )
    elif env.horizon_s % env.step_s:
        errors.append(
            ScenarioError(
                "environment.horizon_s",
                "horizon_not_multiple_of_step",
                f"Horizon {env.horizon_s} s is not a whole number of {env.step_s} s steps",
            )
        )

    plane_ids = _duplicates(errors, [p.id for p in design.planes], "design.planes", "plane")
    sat_ids = _duplicates(
        errors, [s.id for s in design.satellites], "design.satellites", "satellite"
    )
    _duplicates(errors, [g.id for g in scenario.ground_sites], "ground_sites", "site")

    for index, sat in enumerate(design.satellites):
        if sat.plane_id not in plane_ids:
            errors.append(
                ScenarioError(
                    f"design.satellites[{index}].plane_id",
                    "unknown_plane",
                    f"Satellite {sat.id} refers to plane {sat.plane_id!r}, which is not declared",
                )
            )

    # Satellites and ground sites share one identifier space because a route is a
    # list of ids running through both, and a collision would make a path ambiguous.
    for index, site in enumerate(scenario.ground_sites):
        if site.id in sat_ids:
            errors.append(
                ScenarioError(
                    f"ground_sites[{index}].id",
                    "id_collides_with_satellite",
                    f"{site.id!r} is used by a satellite as well as a ground site",
                )
            )

    if not scenario.clients:
        errors.append(
            ScenarioError("ground_sites", "no_client", "At least one site must have role 'client'")
        )
    if not scenario.gateways:
        errors.append(
            ScenarioError(
                "ground_sites", "no_gateway", "At least one site must have role 'gateway'"
            )
        )

    gateway_ids = {g.id for g in scenario.gateways}
    for index, failure in enumerate(scenario.failures):
        if failure.satellite_id not in sat_ids:
            errors.append(
                ScenarioError(
                    f"failures[{index}].satellite_id",
                    "unknown_satellite",
                    f"No satellite {failure.satellite_id!r} to take out of service",
                )
            )
        errors += _interval(failure.start_s, failure.end_s, env.horizon_s, f"failures[{index}]")

    for index, outage in enumerate(scenario.gateway_outages):
        if outage.gateway_id not in gateway_ids:
            errors.append(
                ScenarioError(
                    f"gateway_outages[{index}].gateway_id",
                    "unknown_gateway",
                    f"No gateway {outage.gateway_id!r} to take out of service",
                )
            )
        errors += _interval(
            outage.start_s, outage.end_s, env.horizon_s, f"gateway_outages[{index}]"
        )

    return errors


def _interval(start: float, end: float, horizon: int, where: str) -> list[ScenarioError]:
    """An outage runs over `[start, end)`, has a positive length and sits inside the horizon."""

    if not 0 <= start:
        return [ScenarioError(f"{where}.start_s", "interval_before_zero", "Start is before 0 s")]
    if not start < end:
        return [
            ScenarioError(
                f"{where}.end_s",
                "interval_not_positive",
                f"End {end} s is not after start {start} s",
            )
        ]
    if end > horizon:
        return [
            ScenarioError(
                f"{where}.end_s",
                "interval_past_horizon",
                f"End {end} s is past the horizon {horizon} s",
            )
        ]
    return []


def _duplicates(
    errors: list[ScenarioError], ids: list[str], where: str, kind: str
) -> set[str]:
    """Record every repeated identifier and return the set of distinct ones."""

    seen: set[str] = set()
    for index, value in enumerate(ids):
        if value in seen:
            errors.append(
                ScenarioError(
                    f"{where}[{index}].id",
                    "duplicate_id",
                    f"{value!r} is used by more than one {kind}",
                )
            )
        seen.add(value)
    return seen


def _from_pydantic(error: dict[str, Any]) -> ScenarioError:
    """Re-address a pydantic error as a dotted path the interface can point at."""

    path = ""
    for part in error["loc"]:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else str(part)
    return ScenarioError(path, error["type"], error["msg"])
