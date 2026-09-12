/**
 * Оболочка приложения: один рабочий сценарий, один посчитанный по нему прогон, одни часы.
 *
 * Состояние намеренно маленькое. `scenario` — это проект, который правят формы,
 * `baseline` — каким он был при загрузке. Именно эта пара делает возможным «сбросить
 * изменения» и именно с ней сравнение считает дифф. `run` — результат последнего
 * расчёта, и он никогда не выводится из `scenario` сам собой: пользователь нажимает
 * «Рассчитать», а до этого панели показывают тот прогон, который действительно
 * существует, а не тот, который получился бы. Показать числа для проекта, которого
 * никто не считал, — это и есть отказ, от которого защищает такое устройство.
 */

import { useCallback, useEffect, useMemo, useReducer } from "react";

import { api, ApiError, ScenarioInvalidError, type DesignRef } from "./api";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { ComparePanel } from "./components/ComparePanel";
import { ConfigPanel } from "./components/ConfigPanel";
import { ErrorList } from "./components/ErrorList";
import { Guide } from "./components/Guide";
import { MapView } from "./components/MapView";
import { MetricsPanel } from "./components/MetricsPanel";
import { ResiliencePanel } from "./components/ResiliencePanel";
import { Timeline } from "./components/Timeline";
import { VariantsPanel } from "./components/VariantsPanel";
import type {
  CriticalityReport,
  DegradationCurve,
  DeliveryReport,
  FamilyReport,
  FieldError,
  PlacementReport,
  RedundancyReport,
  Run,
  Scenario,
  ScenarioSummary,
  Strategy,
  SweepReport,
} from "./types";

/**
 * Посчитанные исследования держит оболочка, а не сами вкладки.
 *
 * Вкладка, хранящая результат у себя, теряет его при каждом переключении — эксперту
 * приходится нажимать кнопку заново, чтобы увидеть то, что уже считали. Поэтому
 * результаты живут здесь и переживают переключение вкладок.
 */
export interface StudyResults {
  sweep: SweepReport | null;
  criticality: CriticalityReport | null;
  delivery: DeliveryReport | null;
  degradation: DegradationCurve | null;
  families: FamilyReport | null;
  placement: PlacementReport | null;
}

const NO_STUDIES: StudyResults = {
  sweep: null,
  criticality: null,
  delivery: null,
  degradation: null,
  families: null,
  placement: null,
};

export interface SavedRun {
  runId: string;
  label: string;
  worstAvailability: number;
}

interface State {
  catalogue: ScenarioSummary[];
  scenario: Scenario | null;
  baseline: Scenario | null;
  sourceLabel: string;
  strategy: Strategy;
  run: Run | null;
  /** Запас маршрутов того же прогона. Приходит отдельным ответом, потому что нужен не всем. */
  redundancy: RedundancyReport | null;
  step: number;
  client: string | null;
  tab: "metrics" | "compare" | "analysis" | "resilience";
  playing: boolean;
  stepMs: number;
  saved: SavedRun[];
  studies: StudyResults;
  /** Проект, на котором посчитаны сохранённые исследования. Нужен, чтобы отличить
      устаревшие числа от свежих, а не показывать их молча. */
  studiesFor: Scenario | null;
  guide: boolean;
  busy: string | null;
  errors: FieldError[] | null;
  message: string | null;
}

type Action =
  | { type: "catalogue"; catalogue: ScenarioSummary[] }
  | { type: "load"; scenario: Scenario; label: string }
  | { type: "edit"; scenario: Scenario }
  | { type: "revert" }
  | { type: "strategy"; strategy: Strategy }
  | { type: "ran"; run: Run }
  | { type: "redundancy"; redundancy: RedundancyReport }
  | { type: "step"; step: number }
  | { type: "client"; client: string }
  | { type: "tab"; tab: State["tab"] }
  | { type: "playing"; playing: boolean }
  | { type: "stepMs"; stepMs: number }
  | { type: "remember"; run: SavedRun }
  | { type: "forget"; runId: string }
  | { type: "guide"; guide: boolean }
  | { type: "study"; patch: Partial<StudyResults>; scenario: Scenario }
  | { type: "busy"; busy: string | null }
  | { type: "errors"; errors: FieldError[] | null }
  | { type: "message"; message: string | null };

const initial: State = {
  catalogue: [],
  scenario: null,
  baseline: null,
  sourceLabel: "",
  strategy: "min_hops",
  run: null,
  redundancy: null,
  step: 0,
  client: null,
  tab: "metrics",
  playing: false,
  stepMs: 160,
  saved: [],
  studies: NO_STUDIES,
  studiesFor: null,
  guide: false,
  busy: null,
  errors: null,
  message: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "catalogue":
      return { ...state, catalogue: action.catalogue };

    case "load":
      // У только что загруженного проекта результатов ещё нет, а предыдущий прогон
      // принадлежал другому сценарию. Оставить его на экране значило бы привязать
      // старые числа к новому проекту — ошибка, которую этот интерфейс совершать не
      // имеет права.
      return {
        ...state,
        scenario: action.scenario,
        baseline: action.scenario,
        sourceLabel: action.label,
        run: null,
        redundancy: null,
        // Исследования принадлежали прежнему проекту. Оставить их на экране значило бы
        // подписать старые числа под новым сценарием — ровно то, чего делать нельзя.
        studies: NO_STUDIES,
        studiesFor: null,
        step: 0,
        client: null,
        playing: false,
        errors: null,
        message: null,
      };

    case "edit":
      return { ...state, scenario: action.scenario, errors: null };

    case "revert":
      return state.baseline
        ? { ...state, scenario: state.baseline, errors: null, message: "Изменения сброшены" }
        : state;

    case "strategy":
      return { ...state, strategy: action.strategy };

    case "ran": {
      const clients = Object.keys(action.run.clients);
      return {
        ...state,
        run: action.run,
        // Запас маршрутов принадлежит предыдущему прогону. Оставить его значило бы
        // раскрасить новую шкалу старыми числами — ровно та ошибка, от которой
        // защищает сброс прогона при загрузке сценария.
        redundancy: null,
        step: Math.min(state.step, action.run.times_s.length - 1),
        client: state.client && clients.includes(state.client) ? state.client : clients[0],
        errors: null,
      };
    }

    case "redundancy":
      return { ...state, redundancy: action.redundancy };

    case "step":
      return { ...state, step: action.step };

    case "client":
      return { ...state, client: action.client };

    case "tab":
      return { ...state, tab: action.tab };

    case "playing":
      return { ...state, playing: action.playing };

    case "stepMs":
      return { ...state, stepMs: action.stepMs };

    case "remember":
      return {
        ...state,
        saved: [...state.saved.filter((r) => r.runId !== action.run.runId), action.run],
      };

    case "forget":
      return { ...state, saved: state.saved.filter((r) => r.runId !== action.runId) };

    case "guide":
      return { ...state, guide: action.guide };

    case "study":
      // Если проект с прошлого исследования изменился, прежние результаты относятся
      // уже к другой конфигурации, и держать их рядом со свежим нельзя.
      return {
        ...state,
        studies:
          state.studiesFor === action.scenario
            ? { ...state.studies, ...action.patch }
            : { ...NO_STUDIES, ...action.patch },
        studiesFor: action.scenario,
      };

    case "busy":
      return { ...state, busy: action.busy };

    case "errors":
      return { ...state, errors: action.errors, busy: null };

    case "message":
      return { ...state, message: action.message };
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const { scenario, baseline, run, step, client, busy } = state;

  useEffect(() => {
    api
      .scenarios()
      .then((catalogue) => {
        dispatch({ type: "catalogue", catalogue });
        if (catalogue.length) void openBundled(catalogue[0].source);
      })
      .catch(() => dispatch({ type: "message", message: "Сервис недоступен" }));
    // Каталог загружается один раз при монтировании; `openBundled` замыкает только dispatch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Все обращения к API идут через эту обёртку, поэтому об отказах сообщается единообразно. */
  const guard = useCallback(async (label: string, work: () => Promise<void>) => {
    dispatch({ type: "busy", busy: label });
    try {
      await work();
      dispatch({ type: "busy", busy: null });
    } catch (error) {
      if (error instanceof ScenarioInvalidError) {
        dispatch({ type: "errors", errors: error.errors });
      } else {
        dispatch({ type: "busy", busy: null });
        dispatch({
          type: "message",
          message: error instanceof ApiError ? error.message : "Не удалось выполнить запрос",
        });
      }
    }
  }, []);

  const openBundled = useCallback(
    (name: string) =>
      guard("Загрузка сценария", async () => {
        const loaded = await api.scenario(name);
        dispatch({ type: "load", scenario: loaded, label: loaded.meta.title || name });
      }),
    [guard],
  );

  const openUploaded = useCallback(
    (text: string, filename: string) =>
      guard("Проверка файла", async () => {
        const parsed = JSON.parse(text) as Record<string, unknown>;

        // Файл результата принимается наравне со сценарием: внутри него лежит полный
        // использованный проект. Иначе выгрузить результат и попробовать загрузить
        // его обратно — естественное движение — упиралось бы в отказ по формату,
        // и это выглядело бы ограничением сервиса, хотя им не является.
        const fromResult =
          parsed?.schema_version === "cosmo-A-result-1.0" && parsed.effective_scenario;
        const candidate = fromResult ? parsed.effective_scenario : parsed;

        await api.validate(candidate);
        const loaded = candidate as Scenario;
        dispatch({
          type: "load",
          scenario: loaded,
          label: loaded.meta?.title || filename,
        });
        if (fromResult) {
          dispatch({
            type: "message",
            message: "Это файл результата — взят сценарий, по которому он посчитан",
          });
        }
      }).catch(() => undefined),
    [guard],
  );

  const design = useMemo<DesignRef | null>(
    () => (scenario ? { scenario } : null),
    [scenario],
  );

  const calculate = useCallback(
    (saveAs?: string) => {
      if (!design) return;
      return guard("Расчёт", async () => {
        const result = await api.run(design, { strategy: state.strategy, save_as: saveAs });
        dispatch({ type: "ran", run: result });
        dispatch({
          type: "remember",
          run: {
            runId: result.run_id,
            label: saveAs || state.sourceLabel || result.summary.scenario_id,
            worstAvailability: result.summary.worst_availability,
          },
        });
      });
    },
    [design, guard, state.strategy, state.sourceLabel],
  );

  // Запас маршрутов считается 0.14 с и нужен шкале, поэтому запрашивается сразу после
  // расчёта, а не по кнопке. Отказ здесь молчаливый: шкала просто останется без
  // дополнительной полосы, а это не повод показывать ошибку поверх готовых чисел.
  useEffect(() => {
    if (!run) return;
    let alive = true;
    api
      .redundancy(run.run_id)
      .then((report) => alive && dispatch({ type: "redundancy", redundancy: report }))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [run]);

  const keepStudy = useCallback(
    (patch: Partial<StudyResults>) => {
      if (scenario) dispatch({ type: "study", patch, scenario });
    },
    [scenario],
  );

  // Исследования считались для той конфигурации, что была на экране в тот момент.
  // После правки ползунков они всё ещё полезны, но это уже другие числа, и молчать
  // об этом нельзя.
  const studiesStale = state.studiesFor !== null && state.studiesFor !== scenario;

  const changed = scenario !== baseline;
  const clients = run ? Object.keys(run.clients) : [];

  return (
    <div className="app">
      <header className="topbar">
        <h1>cosmo-net</h1>
        <button
          className="help"
          onClick={() => dispatch({ type: "guide", guide: true })}
          title="Как пользоваться сервисом"
        >
          Инструкция
        </button>
        {state.sourceLabel && <span className="scenario-name">{state.sourceLabel}</span>}
        {changed && <span className="pill bad">есть изменения</span>}
        <span className="spacer" />
        {busy && <span className="busy">{busy}…</span>}
        {state.message && (
          <span className="hint" onClick={() => dispatch({ type: "message", message: null })}>
            {state.message}
          </span>
        )}
        <label className="hint" htmlFor="strategy">
          маршрутизация
        </label>
        <select
          id="strategy"
          style={{ width: "auto" }}
          value={state.strategy}
          onChange={(event) =>
            dispatch({ type: "strategy", strategy: event.target.value as Strategy })
          }
        >
          <option value="min_hops">минимум переходов</option>
          <option value="min_distance">минимум длины</option>
          <option value="max_margin">максимум запаса</option>
        </select>
        <button className="primary" disabled={!scenario || !!busy} onClick={() => calculate()}>
          Рассчитать
        </button>
        <button
          disabled={!design || !!busy}
          onClick={() =>
            design &&
            void guard("Выгрузка сценария", () =>
              api.exportScenario(design, `${scenario?.meta?.id || "scenario"}.json`),
            )
          }
          title="Файл формата cosmo-A-1.0, его можно загрузить обратно"
        >
          Выгрузить сценарий
        </button>
        <button
          disabled={!run}
          onClick={() => run && window.open(api.exportUrl(run.run_id), "_blank")}
        >
          Выгрузить результат
        </button>
      </header>

      {state.guide && <Guide onClose={() => dispatch({ type: "guide", guide: false })} />}

      <div className="body">
        <aside className="sidebar">
          <ConfigPanel
            catalogue={state.catalogue}
            scenario={scenario}
            changed={changed}
            onOpenBundled={openBundled}
            onUpload={openUploaded}
            onEdit={(next) => dispatch({ type: "edit", scenario: next })}
            onRevert={() => dispatch({ type: "revert" })}
          />
          <VariantsPanel
            design={design}
            onLoad={(loaded, label) => dispatch({ type: "load", scenario: loaded, label })}
            onError={(message) => dispatch({ type: "message", message })}
          />
        </aside>

        <main className="main">
          {state.errors && (
            <ErrorList
              errors={state.errors}
              onDismiss={() => dispatch({ type: "errors", errors: null })}
            />
          )}

          <MapView
            run={run}
            step={step}
            client={client}
            stepMs={state.stepMs}
            playing={state.playing}
          />

          <Timeline
            run={run}
            redundancy={state.redundancy}
            step={step}
            client={client}
            playing={state.playing}
            stepMs={state.stepMs}
            onStep={(next) => dispatch({ type: "step", step: next })}
            onClient={(next) => dispatch({ type: "client", client: next })}
            onPlaying={(next) => dispatch({ type: "playing", playing: next })}
            onStepMs={(next) => dispatch({ type: "stepMs", stepMs: next })}
          />

          <section className="panel">
            <div className="tabs" role="tablist">
              {(
                [
                  ["metrics", "Показатели"],
                  ["compare", "Сравнение"],
                  ["analysis", "Анализ"],
                  ["resilience", "Устойчивость"],
                ] as const
              ).map(([id, title]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={state.tab === id}
                  onClick={() => dispatch({ type: "tab", tab: id })}
                >
                  {title}
                </button>
              ))}
            </div>

            {state.tab === "metrics" && (
              <MetricsPanel
                run={run}
                client={client}
                step={step}
                onClient={(next) => dispatch({ type: "client", client: next })}
              />
            )}

            {state.tab === "compare" && (
              <ComparePanel
                saved={state.saved}
                currentRunId={run?.run_id ?? null}
                clients={clients}
                onSaveCurrent={(label) => calculate(label)}
                onError={(message) => dispatch({ type: "message", message })}
              />
            )}

            {state.tab === "analysis" && (
              <AnalysisPanel
                design={design}
                scenario={scenario}
                results={state.studies}
                stale={studiesStale}
                onResult={keepStudy}
                onApply={(next) => dispatch({ type: "edit", scenario: next })}
                onError={(message) => dispatch({ type: "message", message })}
              />
            )}

            {state.tab === "resilience" && (
              <ResiliencePanel
                design={design}
                results={state.studies}
                stale={studiesStale}
                onResult={keepStudy}
                onError={(message) => dispatch({ type: "message", message })}
              />
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
