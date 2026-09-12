"""
HTTP-сервис: загрузить сценарий, изменить его, посчитать, сравнить прогоны, выгрузить результат.

    uv run uvicorn cosmo_net.serving.api:app --reload

Любая ручка, которой нужен проект, принимает его тремя способами: телом запроса, по
идентификатору сохранённого варианта или по имени одного из сценариев кейса.
Интерфейсу нужны все три, а разбор их в одном месте избавляет остальной файл от
необходимости знать, что именно пришло.

Собранный интерфейс монтируется на `/`, если он есть. Если его нет, API продолжает
работать, а `/` честно об этом сообщает: свежая копия репозитория, где ещё не
запускали `npm run build`, должна быть пригодна через документированные ручки, а не
отвечать 404 без объяснений.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cosmo_net import __version__
from cosmo_net.analysis.compare import compare_runs
from cosmo_net.analysis.criticality import rank_satellites
from cosmo_net.analysis.degradation import degradation_curve
from cosmo_net.analysis.delivery import delivery_report
from cosmo_net.analysis.families import spacing_curve
from cosmo_net.analysis.optimise import refine, sweep_spacing
from cosmo_net.analysis.placement import placement_grid
from cosmo_net.analysis.redundancy import redundancy_report
from cosmo_net.analysis.resources import usable_workers
from cosmo_net.analysis.simulate import simulate, snapshot_at
from cosmo_net.config import STATIC_DIR
from cosmo_net.routing.strategies import Strategy
from cosmo_net.scenario.io import bundled_scenarios, dump_scenario
from cosmo_net.scenario.schema import Scenario, ScenarioInvalid, parse_scenario
from cosmo_net.serving.payloads import (
    error_payload,
    export_payload,
    run_payload,
    scenario_summary,
    snapshot_payload,
    trajectory_payload,
)
from cosmo_net.serving.store import RunCache, VariantStore

app = FastAPI(
    title="cosmo-net",
    version=__version__,
    summary="Проектирование устойчивой спутниковой группировки: расчёт, маршруты, сравнение.",
)

# Ответ с прогоном — это несколько сотен килобайт очень однообразного JSON: 720
# причин из пяти слов, 2160 путей по 50 идентификаторам. Сжимается он в разы, и
# ползунок остаётся отзывчивым даже на медленном канале.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# При разработке сервер Vite живёт на другом порту. В развёрнутом контейнере
# интерфейс отдаёт это же приложение, кросс-доменных запросов не возникает вовсе,
# так что там эта настройка ничего не стоит.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

variants = VariantStore()
runs = RunCache()


class DesignRef(BaseModel):
    """Проект — в том виде, в каком он есть у вызывающей стороны."""

    scenario: dict[str, Any] | None = None
    variant_id: str | None = None
    bundled: str | None = None


class RunRequest(DesignRef):
    strategy: Strategy = Strategy.MIN_HOPS
    save_as: str | None = Field(
        default=None, description="Сохранить проект под этим названием перед расчётом"
    )


class SaveRequest(DesignRef):
    label: str = Field(min_length=1, max_length=120)


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=6)
    labels: list[str] | None = None


class SweepRequest(DesignRef):
    mode: Literal["spacing", "refine"] = "spacing"
    workers: int | None = None


class DegradationRequest(DesignRef):
    """
    Сколько случайных отказов разыгрывать и по сколько наборов на каждое число.

    Число отказов по умолчанию то же, что у отчёта, а наборов меньше. Разделение не
    случайное: наклон кривой считается по всему пройденному диапазону, поэтому
    обрезать диапазон значило бы показать в интерфейсе не то число, которое написано
    в отчёте. А наборов достаточно и меньше — проверено повтором с другим зерном,
    средние расходятся на десятые доли пункта, — и это экономит половину ожидания.
    """

    max_failures: int = Field(default=12, ge=1, le=48)
    trials: int = Field(default=25, ge=1, le=200)


class PlacementRequest(DesignRef):
    """
    Сетка кандидатов на вторую точку приземления.

    Шаг по долготе здесь вдвое крупнее, чем у отчёта: 96 точек вместо 192. Причина
    измеренная — на боевом ограничении в одно ядро полная сетка считается 17 секунд,
    и это уже та область, где обратный прокси может закрыть соединение раньше, чем
    придёт ответ. Найденный максимум от прореживания не страдает: 90° кратно и
    пятнадцати, и тридцати, — а картинка с двенадцатью столбцами читается так же.
    """

    lat_min_deg: float = Field(default=50.0, ge=-90, le=90)
    lat_max_deg: float = Field(default=85.0, ge=-90, le=90)
    lat_step_deg: float = Field(default=5.0, gt=0, le=45)
    lon_step_deg: float = Field(default=30.0, gt=0, le=90)
    workers: int | None = None


def resolve(reference: DesignRef) -> Scenario:
    """Превратить любой из трёх способов назвать проект в проверенный `Scenario`."""

    if reference.scenario is not None:
        return parse_scenario(reference.scenario)

    if reference.variant_id:
        found = variants.get(reference.variant_id)
        if found is None:
            raise HTTPException(404, f"Нет сохранённого варианта {reference.variant_id!r}")
        return found[1]

    if reference.bundled:
        for path in bundled_scenarios():
            if path.stem == reference.bundled:
                return parse_scenario(_read_json(path))
        raise HTTPException(404, f"Нет сценария {reference.bundled!r} в комплекте кейса")

    raise HTTPException(422, "Укажите одно из: scenario, variant_id, bundled")


@app.exception_handler(ScenarioInvalid)
def invalid_scenario(request, exc: ScenarioInvalid) -> JSONResponse:
    """
    422 со списком всех проблем, а не одной первой.

    Кейс требует, чтобы после неудачной загрузки сервис сказал, какое поле или объект
    неверен. Пользователю с тремя ошибками в файле надо сообщить про три.
    """

    return JSONResponse(status_code=422, content=error_payload(exc.errors))


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Достаточно, чтобы понять при выкладке, поднялся ли сервис и собран ли в нём интерфейс."""

    return {
        "status": "ok",
        "version": __version__,
        "frontend_bundled": STATIC_DIR.is_dir(),
        "sweep_workers": usable_workers(),
        "bundled_scenarios": [path.stem for path in bundled_scenarios()],
        "cached_runs": len(runs),
    }


@app.get("/api/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """Сценарии из комплекта кейса, кратко — для списка выбора."""

    listing = []
    for path in bundled_scenarios():
        scenario = parse_scenario(_read_json(path))
        listing.append(scenario_summary(scenario, source=path.stem))
    return listing


@app.get("/api/scenarios/{name}")
def get_scenario(name: str) -> dict[str, Any]:
    for path in bundled_scenarios():
        if path.stem == name:
            return _read_json(path)
    raise HTTPException(404, f"Нет сценария {name!r} в комплекте кейса")


@app.post("/api/scenarios/validate")
def validate_scenario(body: dict[str, Any]) -> dict[str, Any]:
    """Проверить файл, не считая его, чтобы отклонить загрузку до появления ожидания."""

    scenario = parse_scenario(body)
    return {"valid": True, "summary": scenario_summary(scenario, source="upload")}


@app.post("/api/scenarios/export")
def export_scenario(body: DesignRef) -> JSONResponse:
    """
    Текущий проект файлом формата `cosmo-A-1.0`, готовым загрузить обратно.

    Кейс требует, чтобы изменённый сценарий можно было выгрузить и загрузить снова,
    и до сих пор этого не хватало: выгружался только результат расчёта. Файл
    результата в поле загрузки сценария, разумеется, отвергался — формат другой, — и
    тупик выглядел ограничением сервиса, хотя им не был.

    Проходит через разбор и обратную сборку, а не отдаётся как есть: то, что скачано,
    гарантированно принимается обратно, потому что собрано тем же кодом, который
    сценарии и читает.
    """

    scenario = resolve(body)
    name = f"{scenario.meta.id or 'scenario'}.json"
    return JSONResponse(
        content=dump_scenario(scenario),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/variants")
def list_variants() -> list[dict[str, Any]]:
    return variants.list()


@app.post("/api/variants", status_code=201)
def save_variant(body: SaveRequest) -> dict[str, Any]:
    scenario = resolve(body)
    identifier = variants.save(body.label, scenario)
    return {"id": identifier, "label": body.label, "summary": scenario_summary(scenario)}


@app.get("/api/variants/{variant_id}")
def get_variant(variant_id: str) -> dict[str, Any]:
    found = variants.get(variant_id)
    if found is None:
        raise HTTPException(404, f"Нет сохранённого варианта {variant_id!r}")
    label, scenario = found
    return {"id": variant_id, "label": label, "scenario": dump_scenario(scenario)}


@app.delete("/api/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: str) -> Response:
    if not variants.delete(variant_id):
        raise HTTPException(404, f"Нет сохранённого варианта {variant_id!r}")
    return Response(status_code=204)


@app.post("/api/runs")
def create_run(body: RunRequest) -> dict[str, Any]:
    """
    Посчитать проект на всём горизонте и вернуть всё, что нужно для отрисовки.

    `save_as` сначала сохраняет проект, поэтому «поменять и оставить» — это одно
    обращение, а не два, и вариант не может быть сохранён в состоянии, которое
    никогда не считали.
    """

    scenario = resolve(body)
    variant_id = variants.save(body.save_as, scenario) if body.save_as else None

    result = simulate(scenario, body.strategy)
    payload = run_payload(runs.put(result), result)
    payload["variant_id"] = variant_id
    return payload


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return run_payload(run_id, _require_run(run_id))


@app.get("/api/runs/{run_id}/snapshot")
def get_snapshot(run_id: str, t_s: float = 0.0) -> dict[str, Any]:
    """
    Состояние сети в один момент: положения, связи, кто кого видит.

    Пересчитывается, а не хранится. Один отсчёт — около миллисекунды, меньше, чем
    занимает доставка самого запроса, а держать по 26 МБ массивов на прогон ради
    одной строки из них — неудачный размен.
    """

    result = _require_run(run_id)
    return snapshot_payload(snapshot_at(result.scenario, t_s), result.scenario)


@app.get("/api/runs/{run_id}/trajectory")
def get_trajectory(run_id: str) -> dict[str, Any]:
    """
    Положения и связи на всех отсчётах, запрашиваемые один раз: проигрыванию сеть не нужна.

    Подробности в `trajectory_payload`: именно запрос по одному отсчёту приводил к
    тому, что карта расходилась с нарисованным поверх неё маршрутом.
    """

    return trajectory_payload(_require_run(run_id))


@app.get("/api/runs/{run_id}/redundancy")
def get_redundancy(run_id: str) -> dict[str, Any]:
    """
    Сколько независимых маршрутов есть на каждом отсчёте: запас, а не наличие связи.

    Привязано к прогону, а не к проекту, потому что интерфейс раскрашивает этим
    временную шкалу того же прогона, который сейчас показан. Стоит 0.14 с на весь
    горизонт, поэтому считается по запросу и не хранится.
    """

    result = _require_run(run_id)
    report = redundancy_report(result.scenario)
    return {
        **report.to_dict(),
        "times_s": result.times_s,
        "series": {
            client.client_id: client.disjoint_paths.tolist() for client in report.clients
        },
    }


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: str) -> JSONResponse:
    """Прогон в формате `cosmo-A-result-1.0`, отдаётся браузеру файлом."""

    result = _require_run(run_id)
    filename = f"{result.scenario.meta.id or 'result'}-{run_id}.json"
    return JSONResponse(
        content=export_payload(result),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/compare")
def compare(body: CompareRequest) -> dict[str, Any]:
    """Два и более прогона рядом, вместе с параметрами, которыми они различаются."""

    results = [_require_run(run_id) for run_id in body.run_ids]
    if body.labels and len(body.labels) != len(results):
        raise HTTPException(422, "labels должен совпадать по длине с run_ids")
    return compare_runs(results, body.labels)


@app.post("/api/analysis/criticality")
def analyse_criticality(body: DesignRef) -> dict[str, Any]:
    """Упорядочить аппараты по тому, во что обошлась бы потеря каждого на целые сутки."""

    return rank_satellites(resolve(body)).to_dict()


@app.post("/api/analysis/sweep")
def analyse_sweep(body: SweepRequest) -> dict[str, Any]:
    """
    Обойти пространство конфигураций и вернуть всё поле, а не только победителя.

    Интерфейс рисует фронт: доступность против самого долгого перерыва, с проектами,
    которые не проигрывают сразу по обоим показателям.
    """

    scenario = resolve(body)

    # Не os.cpu_count(): внутри контейнера он показывает ядра хоста, и из-за этого на
    # инстансе с 512 МБ запустилось восемь процессов, а сервис убили прямо во время
    # запроса. usable_workers читает cgroup и ограничивает по памяти.
    workers = usable_workers(body.workers)
    report = (
        sweep_spacing(scenario, workers=workers)
        if body.mode == "spacing"
        else refine(scenario, workers=workers)
    )
    return report.to_dict()


@app.post("/api/analysis/delivery")
def analyse_delivery(body: DesignRef) -> dict[str, Any]:
    """
    Через сколько данные дойдут, если разрешить аппарату увезти их и сбросить позже.

    Мгновенная доступность — это строка с нулевой задержкой. Остальные строки
    отвечают на вопрос, который постановка не задаёт, но который решает, провалена
    ли конфигурация: для какого класса трафика она провалена.
    """

    return delivery_report(resolve(body)).to_dict()


@app.post("/api/analysis/degradation")
def analyse_degradation(body: DegradationRequest) -> dict[str, Any]:
    """Сколько произвольных отказов проект переносит, не теряя цель."""

    return degradation_curve(
        resolve(body), max_failures=body.max_failures, trials=body.trials
    ).to_dict()


@app.post("/api/analysis/placement")
def analyse_placement(body: PlacementRequest) -> dict[str, Any]:
    """
    Перебрать места для второй точки приземления и вернуть поверхность целиком.

    Выданные данные не меняются: каждая точка сетки — отдельный прогон на копии
    сценария, и ответ описывает, чего стоил бы второй шлюз, а не предлагает
    переписать вход. Формы для добавления пунктов в интерфейсе поэтому и нет.
    """

    if body.lat_max_deg < body.lat_min_deg:
        raise HTTPException(422, "lat_max_deg должна быть не меньше lat_min_deg")

    return placement_grid(
        resolve(body),
        lat_range=(body.lat_min_deg, body.lat_max_deg),
        lat_step=body.lat_step_deg,
        lon_step=body.lon_step_deg,
        workers=usable_workers(body.workers),
    ).to_dict()


@app.post("/api/analysis/families")
def analyse_families(body: SweepRequest) -> dict[str, Any]:
    """Кривая по разносу плоскостей целиком, с разбором на семейства звезды и дельты."""

    return spacing_curve(resolve(body), workers=usable_workers(body.workers)).to_dict()


def _require_run(run_id: str):
    result = runs.get(run_id)
    if result is None:
        raise HTTPException(
            404, f"Прогон {run_id!r} не найден в кеше — запустите расчёт заново"
        )
    return result


def _read_json(path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
else:

    @app.get("/", response_class=HTMLResponse)
    def no_frontend() -> str:
        """Говорим прямо, а не отвечаем 404: для свежей копии репозитория это обычное состояние."""

        return (
            "<!doctype html><meta charset='utf-8'>"
            "<title>cosmo-net</title>"
            "<body style='font:16px system-ui;margin:3rem;max-width:40rem'>"
            "<h1>cosmo-net</h1>"
            "<p>Интерфейс не собран. Соберите его командой "
            "<code>npm --prefix frontend run build</code> "
            "или откройте <a href='/docs'>/docs</a>, чтобы работать с API напрямую.</p>"
        )
