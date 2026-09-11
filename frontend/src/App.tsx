/**
 * The application shell: one working scenario, one run computed from it, one clock.
 *
 * The state is deliberately small. `scenario` is the design the forms edit,
 * `baseline` is what it was when it was loaded — that pair is what makes "сбросить
 * изменения" possible and what the comparison diffs against. `run` is the result of
 * the last calculation, and it is never derived from `scenario` implicitly: the
 * user presses Рассчитать, and until they do, the panels keep showing the run that
 * actually exists rather than one that would exist. Showing numbers for a design
 * nobody computed is the failure mode this arrangement is built to avoid.
 */

import { useCallback, useEffect, useMemo, useReducer } from "react";

import { api, ApiError, ScenarioInvalidError, type DesignRef } from "./api";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { ComparePanel } from "./components/ComparePanel";
import { ConfigPanel } from "./components/ConfigPanel";
import { ErrorList } from "./components/ErrorList";
import { MapView } from "./components/MapView";
import { MetricsPanel } from "./components/MetricsPanel";
import { Timeline } from "./components/Timeline";
import { VariantsPanel } from "./components/VariantsPanel";
import type { FieldError, Run, Scenario, ScenarioSummary, Strategy } from "./types";

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
  step: number;
  client: string | null;
  tab: "metrics" | "compare" | "analysis";
  playing: boolean;
  stepMs: number;
  saved: SavedRun[];
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
  | { type: "step"; step: number }
  | { type: "client"; client: string }
  | { type: "tab"; tab: State["tab"] }
  | { type: "playing"; playing: boolean }
  | { type: "stepMs"; stepMs: number }
  | { type: "remember"; run: SavedRun }
  | { type: "forget"; runId: string }
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
  step: 0,
  client: null,
  tab: "metrics",
  playing: false,
  stepMs: 160,
  saved: [],
  busy: null,
  errors: null,
  message: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "catalogue":
      return { ...state, catalogue: action.catalogue };

    case "load":
      // A newly loaded design has no results yet, and the previous run belonged to
      // a different scenario. Keeping it on screen would attach the old numbers to
      // the new design, which is the one mistake this interface must not make.
      return {
        ...state,
        scenario: action.scenario,
        baseline: action.scenario,
        sourceLabel: action.label,
        run: null,
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
        step: Math.min(state.step, action.run.times_s.length - 1),
        client: state.client && clients.includes(state.client) ? state.client : clients[0],
        errors: null,
      };
    }

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
    // Loading the catalogue once on mount; `openBundled` closes over dispatch only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Every call to the API goes through here, so failures are reported one way. */
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
        const parsed = JSON.parse(text) as unknown;
        await api.validate(parsed);
        const loaded = parsed as Scenario;
        dispatch({
          type: "load",
          scenario: loaded,
          label: loaded.meta?.title || filename,
        });
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

  const changed = scenario !== baseline;
  const clients = run ? Object.keys(run.clients) : [];

  return (
    <div className="app">
      <header className="topbar">
        <h1>cosmo-net</h1>
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
          disabled={!run}
          onClick={() => run && window.open(api.exportUrl(run.run_id), "_blank")}
        >
          Выгрузить результат
        </button>
      </header>

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
                onApply={(next) => dispatch({ type: "edit", scenario: next })}
                onError={(message) => dispatch({ type: "message", message })}
              />
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
