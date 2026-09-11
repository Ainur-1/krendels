"""
Что уходит в сеть: как выглядит прогон для интерфейса и каким кейс требует видеть экспорт.

Одно решение стоит проговорить. Прогон возвращается целиком — маршрут, причина и
число переходов на каждом отсчёте в одном ответе, — а не по отсчёту за раз. Для
базового прогона это около 200 КБ до сжатия, и благодаря этому ползунок времени
перерисовывается из памяти, а не спрашивает сервер 720 раз. Обратный вариант
пробовали первым, и ощущался он ровно так медленно, как звучит.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cosmo_net.analysis.simulate import NetworkSnapshot, RunResult
from cosmo_net.config import RESULT_SCHEMA_VERSION
from cosmo_net.geometry.contacts import compute_contacts
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.routing.diagnose import Outage
from cosmo_net.scenario.io import dump_scenario
from cosmo_net.scenario.schema import Scenario, ScenarioError


def run_payload(run_id: str, result: RunResult) -> dict[str, Any]:
    """Всё, что нужно интерфейсу, чтобы нарисовать прогон, ни о чём не переспрашивая."""

    target = result.scenario.environment.target_availability
    clients: dict[str, Any] = {}

    for client_id, metrics in result.metrics.items():
        routes = result.routes[client_id]
        clients[client_id] = {
            "metrics": metrics.summary(target),
            "reachable": [route is not None for route in routes],
            "cause": [str(cause) for cause in result.causes[client_id]],
            "hops": [route.hops if route else None for route in routes],
            "path": [route.path if route else [] for route in routes],
            "gateway": [route.gateway_id if route else None for route in routes],
            "length_km": [
                round(route.length_km, 1) if route else None for route in routes
            ],
            "gaps": [
                {
                    "start_s": gap.start_s,
                    "end_s": gap.end_s,
                    "duration_s": gap.duration_s,
                    "cause": str(gap.cause),
                    "causes": gap.causes,
                    "at_horizon_edge": gap.at_horizon_edge,
                }
                for gap in metrics.gaps
            ],
        }

    return {
        "run_id": run_id,
        "summary": result.summary(),
        "times_s": result.times_s,
        "clients": clients,
        "scenario": dump_scenario(result.scenario),
        "outage_causes": [str(cause) for cause in Outage if cause is not Outage.NONE],
    }


def trajectory_payload(result: RunResult) -> dict[str, Any]:
    """
    Все положения и все связи на всех отсчётах — одним ответом.

    Появилось потому, что карта получала по одному отсчёту за раз по сети, тогда как
    маршрут на тот же отсчёт брался из памяти. При проигрывании запросы не успевали и
    отменяли друг друга: аппараты стояли на устаревшем кадре, а линия маршрута уже
    уехала вперёд — картинка противоречила сама себе.

    Измерено на базовом сценарии: 240 КБ в gzip на весь прогон против примерно 20 КБ
    на отсчёт и 720 обращений, чтобы проиграть те же сутки. Разовый ответ и меньше по
    сумме, и единственный, который можно рисовать, ничего не дожидаясь.

    Координаты гринвичские и округлены до километра: на карте мира это заметно меньше
    пикселя. А то, что они декартовы, позволяет клиенту интерполировать между
    отсчётами без особых случаев на 180-м меридиане и у полюсов.
    """

    scenario = result.scenario
    trajectory = compute_trajectory(scenario)
    contacts = compute_contacts(scenario, trajectory)

    links: list[list[list[int]]] = []
    for step in range(len(result.times_s)):
        open_pairs = np.flatnonzero(contacts.isl_open[step])
        links.append([[int(contacts.pair_index[p, 0]), int(contacts.pair_index[p, 1])]
                      for p in open_pairs])

    # Какие аппараты доступны каждому наземному пункту, индексами в `satellite_ids`.
    # Карта рисует их только для выбранного терминала, но выбор меняется без
    # пересчёта прогона, поэтому отправляются все.
    ground_visible: dict[str, list[list[int]]] = {}
    for g, site_id in enumerate(contacts.ground_ids):
        ground_visible[site_id] = [
            [int(n) for n in np.flatnonzero(contacts.ground_open[step, g])]
            for step in range(len(result.times_s))
        ]

    return {
        "times_s": result.times_s,
        "step_s": scenario.environment.step_s,
        "satellite_ids": contacts.satellite_ids,
        "plane_ids": [sat.plane_id for sat in scenario.design.satellites],
        "ecef_km": np.round(trajectory.ecef_km).astype(int).tolist(),
        "active": contacts.active.tolist(),
        "links": links,
        "ground_visible": ground_visible,
        "ground_sites": [site.model_dump(mode="json") for site in scenario.ground_sites],
    }


def snapshot_payload(snapshot: NetworkSnapshot, scenario: Scenario) -> dict[str, Any]:
    """Состояние сети в один момент вместе с наземными пунктами, на фоне которых оно рисуется."""

    return {
        "t_s": snapshot.t_s,
        "satellites": snapshot.satellites,
        "links": snapshot.links,
        "ground_links": snapshot.ground_links,
        "elevation_deg": snapshot.elevation_deg,
        "ground_sites": [site.model_dump(mode="json") for site in scenario.ground_sites],
    }


def export_payload(result: RunResult) -> dict[str, Any]:
    """
    Результат в формате, который задаёт кейс: `cosmo-A-result-1.0`.

    По одной записи на пару «отсчёт — клиент», для базового прогона это 2160 записей,
    и там, где маршрута не было, лежит пустой путь, а не отсутствующая запись: так
    доступность можно посчитать прямо из файла. В `effective_scenario` лежит сценарий
    в том виде, в котором его действительно считали, вместе с правками, — благодаря
    этому выгрузка загружается обратно, а прогон воспроизводится.
    """

    routes = []
    for client_id, per_step in result.routes.items():
        for t_s, route in zip(result.times_s, per_step, strict=True):
            routes.append(
                {"t_s": t_s, "client_id": client_id, "path": route.path if route else []}
            )
    routes.sort(key=lambda record: (record["t_s"], record["client_id"]))

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "effective_scenario": dump_scenario(result.scenario),
        "routes": routes,
        # Сверх обязательных полей, и это разрешено: «К результату можно добавить
        # сводные показатели и пояснения».
        "summary": result.summary(),
        "routing_strategy": str(result.strategy),
    }


def error_payload(errors: list[ScenarioError]) -> dict[str, Any]:
    """
    Ошибки проверки, адресованные полем, чтобы интерфейс мог на него показать.

    `code` стабилен, и именно его интерфейс превращает в текст. `message` — запасной
    вариант на случай кода, для которого формулировки ещё нет; собственные сообщения
    сервиса русские, сообщения самого pydantic остаются английскими.
    """

    return {
        "detail": "scenario_invalid",
        "errors": [
            {"field": error.field, "code": error.code, "message": error.message}
            for error in errors
        ],
    }


def scenario_summary(scenario: Scenario, source: str = "") -> dict[str, Any]:
    """Короткое описание для списка выбора, без отправки всего файла."""

    return {
        "source": source,
        "id": scenario.meta.id,
        "title": scenario.meta.title,
        "planes": len(scenario.design.planes),
        "satellites": len(scenario.design.satellites),
        "launch_stage": scenario.design.launch_stage,
        "clients": [{"id": s.id, "name": s.name} for s in scenario.clients],
        "gateways": [{"id": s.id, "name": s.name} for s in scenario.gateways],
        "steps": len(scenario.times),
        "environment": scenario.environment.model_dump(mode="json"),
        "failures": len(scenario.failures),
        "gateway_outages": len(scenario.gateway_outages),
    }
