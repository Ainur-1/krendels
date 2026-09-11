"""
Два классических семейства разноса плоскостей — и то, из какого выданный проект.

Группировки такого рода описывают двумя семействами. В **звезде** наклонение близко
к полярному, а плоскости разносят по 180°: плоскость с восходящим узлом θ и
плоскость с θ + 180° дают почти одну и ту же трассу по земле, поэтому весь полезный
диапазон — половина окружности, и орбиты сгущаются к полюсам. В **дельте**
наклонение меньше, а плоскости разносят по всем 360°, и покрытие выходит ровным в
средних широтах.

Модуль отвечает на два вопроса сразу. Первый: к какому семейству принадлежит
выданный проект. Второй: что даёт каждое семейство на этих конкретных наземных
пунктах — а это уже не учебниковый вопрос, потому что учебниковое правило выведено
для равномерного покрытия всей полярной шапки, а здесь покрывать надо три точки и
один шлюз.

Измерено на полной группировке: у кривой два чётких максимума. 99.3 % при разносе
66° — это семейство звезды, 92.2 % при 122° — семейство дельты, разрыв семь
пунктов. Выданный проект стоит на 60°, то есть ровно на 180°/3: это звезда,
поставленная точно по правилу. Наш максимум смещён от правила на 6°, и эти 6°
стоят 2.6 пункта — потому что задача не равномерное покрытие, а три конкретных
пункта при наклонении 87°, а не 90°.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from cosmo_net.analysis.optimise import evaluate, variant
from cosmo_net.analysis.resources import usable_workers
from cosmo_net.scenario.schema import Scenario

DEFAULT_STEP_DEG = 2.0
"""Шаг сетки. Вершины у кривой шириной около десяти градусов, так что двух хватает
с запасом, а весь прогон остаётся секундным."""


@dataclass(frozen=True)
class SpacingPoint:
    """Равномерный разнос с заданным шагом и то, что он даёт худшему пункту."""

    spacing_deg: float
    worst_availability: float

    def to_dict(self) -> dict[str, object]:
        return {
            "spacing_deg": self.spacing_deg,
            "worst_availability": self.worst_availability,
        }


@dataclass
class FamilyReport:
    """Кривая по разносу целиком и разбор её по семействам."""

    planes: int
    supplied_spacing_deg: float | None
    points: list[SpacingPoint] = field(default_factory=list)

    @property
    def star_spacing_deg(self) -> float:
        """Разнос, предписанный правилом звезды: половина окружности на все плоскости."""

        return 180.0 / self.planes

    @property
    def delta_spacing_deg(self) -> float:
        """Разнос, предписанный правилом дельты: полная окружность на все плоскости."""

        return 360.0 / self.planes

    @property
    def boundary_deg(self) -> float:
        """
        Граница между семействами — середина между их предписанными разносами.

        Делить приходится самим: непрерывная кривая семейств не знает, а сказать, какой
        из двух максимумов какому семейству принадлежит, надо. Середина — единственный
        выбор, который не отдаёт предпочтения ни одному из них.
        """

        return (self.star_spacing_deg + self.delta_spacing_deg) / 2

    @property
    def star_best(self) -> SpacingPoint | None:
        return self._best(lambda p: 0 < p.spacing_deg <= self.boundary_deg)

    @property
    def delta_best(self) -> SpacingPoint | None:
        return self._best(lambda p: p.spacing_deg > self.boundary_deg)

    @property
    def supplied_family(self) -> str | None:
        """К какому семейству отнесён выданный проект. `None`, если разнос неравномерный."""

        if self.supplied_spacing_deg is None:
            return None
        return "star" if self.supplied_spacing_deg <= self.boundary_deg else "delta"

    def _best(self, predicate) -> SpacingPoint | None:
        chosen = [p for p in self.points if predicate(p)]
        return max(chosen, key=lambda p: p.worst_availability, default=None)

    def to_dict(self) -> dict[str, object]:
        star, delta = self.star_best, self.delta_best
        return {
            "planes": self.planes,
            "supplied_spacing_deg": self.supplied_spacing_deg,
            "supplied_family": self.supplied_family,
            "star_spacing_deg": self.star_spacing_deg,
            "delta_spacing_deg": self.delta_spacing_deg,
            "star_best": star.to_dict() if star else None,
            "delta_best": delta.to_dict() if delta else None,
            "points": [p.to_dict() for p in self.points],
        }


def supplied_spacing_deg(scenario: Scenario) -> float | None:
    """
    Шаг разноса выданного проекта, если плоскости разнесены равномерно, иначе `None`.

    Равномерность проверяется, а не предполагается: сценарий жюри вправе поставить
    плоскости как угодно, и тогда относить его к семейству попросту не к чему.
    """

    raan = [plane.raan_deg for plane in scenario.design.planes]
    if len(raan) < 2:
        return None

    steps = [(raan[k + 1] - raan[k]) % 360.0 for k in range(len(raan) - 1)]
    if max(steps) - min(steps) > 1e-6:
        return None
    return steps[0]


def spacing_curve(
    scenario: Scenario,
    step_deg: float = DEFAULT_STEP_DEG,
    workers: int | None = None,
) -> FamilyReport:
    """
    Пройти равномерное семейство по всему диапазону разносов, от 0 до 180°.

    Верхняя граница выбрана так, чтобы в диапазон попали оба предписанных разноса —
    180/P и 360/P — при любом числе плоскостей от двух и больше. Фазирование берётся
    из сценария и не трогается: вопрос здесь про ориентацию плоскостей, а смешивать
    в одну кривую два разных рычага значило бы не ответить ни на один.
    """

    planes = len(scenario.design.planes)
    base_raan = scenario.design.planes[0].raan_deg

    spacings = [round(step_deg * k, 6) for k in range(int(180.0 / step_deg) + 1)]
    designs = [
        variant(scenario, raan_deg=[base_raan + k * spacing for k in range(planes)])
        for spacing in spacings
    ]

    parallel = usable_workers(workers)
    if parallel > 1 and len(designs) > 8:
        with ProcessPoolExecutor(max_workers=parallel) as pool:
            scored = list(pool.map(evaluate, designs))
    else:
        scored = [evaluate(design) for design in designs]

    return FamilyReport(
        planes=planes,
        supplied_spacing_deg=supplied_spacing_deg(scenario),
        points=[
            SpacingPoint(spacing_deg=spacing, worst_availability=candidate.worst_availability)
            for spacing, candidate in zip(spacings, scored, strict=True)
        ],
    )
