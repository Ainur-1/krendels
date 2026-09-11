"""
Поиск по пространству конфигураций, которое отдаёт интерфейс: ориентация плоскостей и фазирование.

Поиска два, потому что они отвечают на разные вопросы. Перебор разноса проходит по
семейству равномерно разнесённых проектов, где каждая плоскость смещена от
предыдущей на один и тот же угол. Так группировку обычно и задают, и результат
получается таким, который инженер может прочитать как правило. Уточнение по
координатам затем позволяет плоскостям двигаться поодиночке — оно находит проекты,
которых равномерное семейство выразить не может, но обосновать их труднее.

Оба достаточно дёшевы, чтобы жить за кнопкой: одна оценка занимает 68 мс, поэтому
перебор из сотни точек — это семь секунд на одном ядре и около секунды на восьми.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import partial

import numpy as np

from cosmo_net.analysis.reachability import availability_series, longest_gap_steps
from cosmo_net.analysis.resources import usable_workers
from cosmo_net.scenario.schema import Scenario

# Первая стадия прореживает горизонт примерно до такого числа отсчётов. Чтобы
# сравнить сотню кандидатов между собой, каждый отсчёт не нужен; чтобы решить, что
# выводить на экран, — нужен, и ровно для этого есть вторая стадия.
COARSE_STEPS = 180

# Сколько отранжированных кандидатов перемеряется точно. Достаточно широко, чтобы
# настоящий победитель почти наверняка попал в список, и достаточно узко, чтобы это
# оставалось быстрым: на развёрнутом сервере точная оценка занимает 1.2 с против
# 0.3 с у грубой.
SHORTLIST = 12


@dataclass(frozen=True)
class Candidate:
    """Одна конфигурация и то, что она набрала."""

    raan_deg: list[float]
    phase_deg: list[float]
    launch_stage: int

    worst_availability: float
    worst_max_gap_s: int
    availability: dict[str, float]

    approximate: bool = False
    """Оценено на прореженной сетке для ранжирования, а не измерено. Показывать как число нельзя."""

    def to_dict(self) -> dict[str, object]:
        return {
            "raan_deg": self.raan_deg,
            "phase_deg": self.phase_deg,
            "launch_stage": self.launch_stage,
            "worst_availability": self.worst_availability,
            "worst_max_gap_s": self.worst_max_gap_s,
            "availability": self.availability,
            "approximate": self.approximate,
        }


@dataclass
class SweepReport:
    """Всё, что перебрал поиск, плюс короткий список, на который стоит смотреть."""

    baseline: Candidate
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def measured(self) -> list[Candidate]:
        """Кандидаты, числа которых получены на полной сетке, а не на выборке."""

        return [c for c in self.candidates if not c.approximate]

    @property
    def best(self) -> Candidate:
        """Наибольшая доступность худшего пункта; при равенстве решает более короткий перерыв."""

        pool = self.measured or self.candidates or [self.baseline]
        return max(pool, key=lambda c: (c.worst_availability, -c.worst_max_gap_s))

    @property
    def frontier(self) -> list[Candidate]:
        """
        Проекты, которые не проигрывают сразу по обоим показателям.

        Доступность и самый долгий непрерывный перерыв — это разные цели, и на
        сценарии 04 они расходятся: фронт перебора идёт 73.1 % при перерыве 12 720 с,
        68.2 % при 5 880 с, 67.9 % при 3 360 с, 63.9 % при 2 760 с. Пять пунктов
        доступности против четырёх часов непрерывной тишины — это выбор, чем сервис
        должен быть, и одним числом он не решается. На сценарии 01 фронт схлопывается
        в одну точку: там одна конфигурация лучше сразу по обоим показателям.
        """

        ordered = sorted(
            self.measured, key=lambda c: (-c.worst_availability, c.worst_max_gap_s)
        )
        front: list[Candidate] = []
        best_gap = float("inf")
        for candidate in ordered:
            if candidate.worst_max_gap_s < best_gap:
                front.append(candidate)
                best_gap = candidate.worst_max_gap_s
        return front

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.to_dict(),
            "best": self.best.to_dict(),
            "frontier": [c.to_dict() for c in self.frontier],
            "candidates": [c.to_dict() for c in self.candidates],
        }


def evaluate(scenario: Scenario, stride: int = 1) -> Candidate:
    """Оценить одну конфигурацию. Единица работы, из которой собран любой поиск здесь."""

    series = availability_series(scenario, stride=stride)
    step_s = scenario.environment.step_s * stride
    availability = {client: float(r.mean()) for client, r in series.items()}
    return Candidate(
        approximate=stride > 1,
        raan_deg=[p.raan_deg for p in scenario.design.planes],
        phase_deg=[p.phase_deg for p in scenario.design.planes],
        launch_stage=scenario.design.launch_stage,
        worst_availability=min(availability.values()) if availability else 0.0,
        worst_max_gap_s=max((longest_gap_steps(r) for r in series.values()), default=0) * step_s,
        availability=availability,
    )


def variant(
    scenario: Scenario,
    raan_deg: list[float] | None = None,
    phase_deg: list[float] | None = None,
    launch_stage: int | None = None,
) -> Scenario:
    """Копия `scenario` с заменёнными углами плоскостей и очередью. Углы сворачиваются
    в [0, 360)."""

    changed = scenario.model_copy(deep=True)
    for index, plane in enumerate(changed.design.planes):
        if raan_deg is not None:
            plane.raan_deg = float(raan_deg[index]) % 360.0
        if phase_deg is not None:
            plane.phase_deg = float(phase_deg[index]) % 360.0
    if launch_stage is not None:
        changed.design.launch_stage = launch_stage
    return changed


def sweep_spacing(
    scenario: Scenario,
    raan_spacings: list[float] | None = None,
    phase_spacings: list[float] | None = None,
    workers: int | None = None,
) -> SweepReport:
    """
    Пройти по равномерному семейству: плоскость k стоит на `k × raan_spacing`.

    Сетка RAAN по умолчанию останавливается на 120°, потому что дальше для трёх
    плоскостей семейство повторяется: разнос 130° ставит те же три орбиты, что и
    110°, только в другом порядке. Шаг сетки фаз — четверть расстояния между
    соседями внутри плоскости, что при 16 аппаратах на плоскость даёт 22.5°/4.
    """

    planes = len(scenario.design.planes)
    per_plane = _in_plane_spacing(scenario)

    if raan_spacings is None:
        raan_spacings = [float(x) for x in range(0, 121, 5)]
    if phase_spacings is None:
        phase_spacings = [round(per_plane * k / 4, 4) for k in range(4)]

    base_raan = scenario.design.planes[0].raan_deg
    base_phase = scenario.design.planes[0].phase_deg

    designs = [
        variant(
            scenario,
            raan_deg=[base_raan + k * raan for k in range(planes)],
            phase_deg=[base_phase + k * phase for k in range(planes)],
        )
        for raan in raan_spacings
        for phase in phase_spacings
    ]

    # Две стадии, потому что сотня точных оценок на развёрнутом сервере занимает
    # 120 с, а поиску они не нужны. Первая стадия прореживает горизонт примерно до
    # COARSE_STEPS и только ранжирует; вторая перемеряет короткий список на полной
    # сетке, и `best` с `frontier` берутся только из него. Ничто из того, что
    # интерфейс показывает как число, не приходит с прореженного прохода.
    stride = max(1, len(scenario.times) // COARSE_STEPS)
    ranked = sorted(
        _score_all(designs, workers, stride),
        key=lambda c: (-c.worst_availability, c.worst_max_gap_s),
    )

    shortlist = [
        variant(scenario, raan_deg=c.raan_deg, phase_deg=c.phase_deg)
        for c in ranked[:SHORTLIST]
    ]

    # Сценарий в его текущем виде измеряется всегда, попадает на него сетка или нет:
    # выданный проект фазирует плоскости на 7.5°, а сетка по умолчанию шагает
    # четвертями от 22.5° и мимо него проходит. Без этого «лучший» мог бы оказаться
    # хуже, чем не менять ничего.
    baseline = evaluate(scenario)
    measured = [baseline] + _score_all(shortlist, workers, 1)

    return SweepReport(baseline=baseline, candidates=measured + ranked[SHORTLIST:])


def refine(
    scenario: Scenario,
    raan_step_deg: float = 10.0,
    rounds: int = 2,
    workers: int | None = None,
) -> SweepReport:
    """
    Уточнение по координатам: двигаем плоскости по одной и оставляем движение, только
    если оно помогло.

    Начинается с того состояния, в котором сценарий уже находится, поэтому улучшает
    проект, к которому пользователь пришёл сам, а не подменяет его. Плоскости
    обходятся по порядку, первая остаётся неподвижной: повернуть все плоскости разом
    значит повернуть всю группировку, что не меняет её внутреннюю геометрию, а лишь
    сдвигает момент суток, когда под ней проходит каждый наземный пункт.
    """

    baseline = evaluate(scenario)
    current = scenario
    best = baseline
    tried: list[Candidate] = [baseline]

    offsets = [x for x in np.arange(-180, 180, raan_step_deg) if x]
    for _ in range(rounds):
        for index in range(1, len(scenario.design.planes)):
            designs = []
            for offset in offsets:
                raan = [p.raan_deg for p in current.design.planes]
                raan[index] = raan[index] + float(offset)
                designs.append(variant(current, raan_deg=raan))

            scored = _score_all(designs, workers, 1)
            tried.extend(scored)
            winner = max(scored, key=lambda c: (c.worst_availability, -c.worst_max_gap_s))
            if (winner.worst_availability, -winner.worst_max_gap_s) > (
                best.worst_availability,
                -best.worst_max_gap_s,
            ):
                best = winner
                current = variant(current, raan_deg=winner.raan_deg)

    return SweepReport(baseline=baseline, candidates=tried)


def _score_all(designs: list[Scenario], workers: int | None, stride: int) -> list[Candidate]:
    """
    Оценить пачку кандидатов, параллельно — если машина действительно вмещает такие процессы.

    Ограничение здесь не рекомендательное. Запрос `os.cpu_count()` внутри контейнера
    на 512 МБ дал восемь процессов, каждый со своим numpy и своей копией прогона, и
    ядро убило сервис прямо во время запроса: перебор утащил за собой всё, включая
    кеш посчитанных прогонов. `usable_workers` вместо этого читает cgroup.
    """

    parallel = usable_workers(workers)
    if parallel > 1 and len(designs) > 8:
        with ProcessPoolExecutor(max_workers=parallel) as pool:
            return list(pool.map(partial(evaluate, stride=stride), designs))
    return [evaluate(design, stride=stride) for design in designs]


def _in_plane_spacing(scenario: Scenario) -> float:
    """
    Угол между соседними аппаратами в самой населённой плоскости.

    Читается из проекта, а не предполагается, поэтому сценарий жюри с другим числом
    аппаратов на плоскость получит сетку фаз, осмысленную именно для него.
    """

    counts: dict[str, int] = {}
    for satellite in scenario.design.satellites:
        counts[satellite.plane_id] = counts.get(satellite.plane_id, 0) + 1
    largest = max(counts.values(), default=1)
    return 360.0 / largest if largest else 360.0
