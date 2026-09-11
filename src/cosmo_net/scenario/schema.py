"""
Сценарий cosmo-A-1.0 как типизированные объекты и правила, которым файл обязан отвечать.

Каждое правило здесь — это правило, которое проверяет эталонный модуль
`reference/geometry.py`. Отличие одно, и оно важно для интерфейса: тамошняя
`validate()` останавливается на первой же проблеме, а кейс требует сказать
пользователю, **какое именно** поле или объект неверен. Поэтому разбор собирает все
найденные проблемы и сообщает их вместе, адресуя путём —
`design.satellites[12].plane_id`, а не «неверный аппарат».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from cosmo_net.config import SCENARIO_SCHEMA_VERSION

# Лишние ключи сохраняются, а не отвергаются: сценарий «может содержать другие
# названия и идентификаторы», и файл жюри с дополнительной пометкой остаётся
# корректным сценарием. Нечисловые значения (бесконечность, NaN) отвергаются везде —
# это ровно то, что делает проверка `finite()` в эталонном модуле.
_MODEL = ConfigDict(allow_inf_nan=False, extra="allow", populate_by_name=True)


@dataclass(frozen=True)
class ScenarioError:
    """Одна проблема в загруженном файле, адресованная путём до поля, в котором она лежит."""

    field: str
    code: str
    message: str


class ScenarioInvalid(Exception):
    """Файл не является пригодным сценарием. Несёт все найденные проблемы, а не первую."""

    def __init__(self, errors: list[ScenarioError]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) in scenario: {errors[0].message}")


class Meta(BaseModel):
    model_config = _MODEL

    id: str = "scenario"
    title: str = ""


class Environment(BaseModel):
    model_config = _MODEL

    # Границы взяты у эталонного модуля, а не из физики: он отвергает всё, что вне
    # них, поэтому файл, который он забраковал бы, не должен приниматься и здесь.
    altitude_km: float = Field(ge=200, le=1200)
    inclination_deg: float = Field(gt=0, le=180)
    earth_angle0_deg: float

    # Строго целые. `"horizon_s": 86400.0` в JSON — это число с плавающей точкой, и
    # эталонный модуль отвергает его на проверке `isinstance(..., int)`. Принять его
    # здесь значило бы получить файл, который работает в сервисе и не работает в
    # инструменте организаторов.
    horizon_s: StrictInt = Field(gt=0, le=172_800)
    step_s: StrictInt = Field(gt=0)

    min_elevation_deg: float = Field(ge=0, lt=90)
    isl_range_km: float = Field(gt=0, le=10_000)
    target_availability: float = Field(ge=0, le=1)


class Plane(BaseModel):
    model_config = _MODEL

    id: str
    # Промежуток намеренно полуоткрытый: 360° — это та же ориентация, что и 0°, и
    # эталонный модуль такое значение отвергает, поэтому ползунок в интерфейсе
    # останавливается на 359.9.
    raan_deg: float = Field(ge=0, lt=360)
    phase_deg: float = Field(ge=0, lt=360)


class Satellite(BaseModel):
    model_config = _MODEL

    id: str
    plane_id: str
    # Кроме конечности, ничем не ограничено — как и в эталонном модуле: место в
    # 370° или -20° это допустимая запись положения, которая сворачивается сама.
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
    """Не в эфире на промежутке `[start_s, end_s)`. Аппарат сохраняет положение и теряет связи."""

    model_config = _MODEL

    satellite_id: str
    start_s: float
    end_s: float


class GatewayOutage(BaseModel):
    """Шлюз недоступен на `[start_s, end_s)`: на него не садится ни одна наземная линия."""

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
        Сетка расчёта: 0, шаг, …, горизонт − шаг.

        Правый конец не включается, поэтому горизонт 86 400 с при шаге 120 с — это
        720 отсчётов, а не 721. Любая доля в результатах — это их количество,
        делённое на общее число.
        """

        env = self.environment
        return list(range(0, env.horizon_s, env.step_s))


def parse_scenario(data: Any) -> Scenario:
    """
    Превратить JSON в `Scenario` либо бросить `ScenarioInvalid` со списком неверного.

    Проблемы формы приходят от pydantic и уже адресованы путём. Проверки ниже — это
    те, которые ни одно поле не может сделать в одиночку: ссылки, которые должны
    разрешаться, идентификаторы, которые должны быть уникальны сразу в двух списках,
    интервалы, которые должны лежать внутри горизонта, объявленного в другом месте
    файла.
    """

    if not isinstance(data, dict):
        raise ScenarioInvalid(
            [ScenarioError("", "not_an_object", "Сценарий должен быть объектом JSON")]
        )

    # Проверяется раньше всего: файл, объявляющий другую версию, может использовать
    # те же имена полей для другого смысла, а гадать об этом хуже, чем отказать.
    version = data.get("schema_version")
    if version != SCENARIO_SCHEMA_VERSION:
        raise ScenarioInvalid(
            [
                ScenarioError(
                    "schema_version",
                    "unsupported_schema",
                    f"Ожидается {SCENARIO_SCHEMA_VERSION}, получено {version!r}",
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
    """Все правила, для формулировки которых нужна больше чем одна часть файла."""

    errors: list[ScenarioError] = []
    env, design = scenario.environment, scenario.design

    if env.step_s > env.horizon_s:
        errors.append(
            ScenarioError(
                "environment.step_s",
                "step_exceeds_horizon",
                f"Шаг {env.step_s} с больше горизонта {env.horizon_s} с",
            )
        )
    elif env.horizon_s % env.step_s:
        errors.append(
            ScenarioError(
                "environment.horizon_s",
                "horizon_not_multiple_of_step",
                f"Горизонт {env.horizon_s} с не кратен шагу {env.step_s} с",
            )
        )

    plane_ids = _duplicates(errors, [p.id for p in design.planes], "design.planes", "плоскость")
    sat_ids = _duplicates(
        errors, [s.id for s in design.satellites], "design.satellites", "аппарат"
    )
    _duplicates(errors, [g.id for g in scenario.ground_sites], "ground_sites", "пункт")

    for index, sat in enumerate(design.satellites):
        if sat.plane_id not in plane_ids:
            errors.append(
                ScenarioError(
                    f"design.satellites[{index}].plane_id",
                    "unknown_plane",
                    f"Аппарат {sat.id} ссылается на плоскость {sat.plane_id!r}, которой нет",
                )
            )

    # У аппаратов и наземных пунктов общее пространство идентификаторов, потому что
    # маршрут — это список идентификаторов, проходящий через тех и других, и
    # совпадение сделало бы путь неоднозначным.
    for index, site in enumerate(scenario.ground_sites):
        if site.id in sat_ids:
            errors.append(
                ScenarioError(
                    f"ground_sites[{index}].id",
                    "id_collides_with_satellite",
                    f"Идентификатор {site.id!r} занят и аппаратом, и наземным пунктом",
                )
            )

    if not scenario.clients:
        errors.append(
            ScenarioError("ground_sites", "no_client", "Нужен хотя бы один пункт с ролью client")
        )
    if not scenario.gateways:
        errors.append(
            ScenarioError(
                "ground_sites", "no_gateway", "Нужен хотя бы один пункт с ролью gateway"
            )
        )

    gateway_ids = {g.id for g in scenario.gateways}
    for index, failure in enumerate(scenario.failures):
        if failure.satellite_id not in sat_ids:
            errors.append(
                ScenarioError(
                    f"failures[{index}].satellite_id",
                    "unknown_satellite",
                    f"Нет аппарата {failure.satellite_id!r}, который можно вывести из строя",
                )
            )
        errors += _interval(failure.start_s, failure.end_s, env.horizon_s, f"failures[{index}]")

    for index, outage in enumerate(scenario.gateway_outages):
        if outage.gateway_id not in gateway_ids:
            errors.append(
                ScenarioError(
                    f"gateway_outages[{index}].gateway_id",
                    "unknown_gateway",
                    f"Нет шлюза {outage.gateway_id!r}, который можно вывести из строя",
                )
            )
        errors += _interval(
            outage.start_s, outage.end_s, env.horizon_s, f"gateway_outages[{index}]"
        )

    return errors


def _interval(start: float, end: float, horizon: int, where: str) -> list[ScenarioError]:
    """Отказ идёт на `[start, end)`, имеет положительную длину и лежит внутри горизонта."""

    if not 0 <= start:
        return [ScenarioError(f"{where}.start_s", "interval_before_zero", "Начало раньше 0 с")]
    if not start < end:
        return [
            ScenarioError(
                f"{where}.end_s",
                "interval_not_positive",
                f"Конец {end} с не позже начала {start} с",
            )
        ]
    if end > horizon:
        return [
            ScenarioError(
                f"{where}.end_s",
                "interval_past_horizon",
                f"Конец {end} с выходит за горизонт {horizon} с",
            )
        ]
    return []


def _duplicates(
    errors: list[ScenarioError], ids: list[str], where: str, kind: str
) -> set[str]:
    """Записать каждый повторяющийся идентификатор и вернуть множество различных."""

    seen: set[str] = set()
    for index, value in enumerate(ids):
        if value in seen:
            errors.append(
                ScenarioError(
                    f"{where}[{index}].id",
                    "duplicate_id",
                    f"{value!r} встречается больше одного раза ({kind})",
                )
            )
        seen.add(value)
    return seen


def _from_pydantic(error: dict[str, Any]) -> ScenarioError:
    """Переадресовать ошибку pydantic путём, на который интерфейс может показать."""

    path = ""
    for part in error["loc"]:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else str(part)
    return ScenarioError(path, error["type"], error["msg"])
