/**
 * The typed client, and the one place validation failures are turned into something showable.
 *
 * A 422 from this service is not an error in the usual sense — it is the answer to
 * "what is wrong with my file", and the case scores the service on giving it
 * usefully. So it arrives as a `ScenarioInvalidError` carrying the whole list of
 * field problems rather than as a string, and the upload panel renders them against
 * the fields they name.
 */

import type {
  Comparison,
  CriticalityReport,
  FieldError,
  Run,
  Scenario,
  ScenarioSummary,
  Snapshot,
  Strategy,
  SweepReport,
  VariantListing,
} from "./types";

/** Any of the three ways the API accepts a design. Exactly one must be set. */
export type DesignRef =
  | { scenario: Scenario }
  | { variant_id: string }
  | { bundled: string };

export class ScenarioInvalidError extends Error {
  constructor(readonly errors: FieldError[]) {
    super(errors[0]?.message ?? "Сценарий не прошёл проверку");
    this.name = "ScenarioInvalidError";
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });

  if (response.status === 204) return undefined as T;

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (body?.detail === "scenario_invalid" && Array.isArray(body.errors)) {
      throw new ScenarioInvalidError(body.errors);
    }
    // FastAPI's own body validation also answers 422, in its own shape. It means a
    // bug in this client rather than a bad file, so it is reported as such.
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : `Запрос не выполнен (${response.status})`;
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () =>
    request<{
      status: string;
      version: string;
      frontend_bundled: boolean;
      bundled_scenarios: string[];
    }>("/health"),

  scenarios: () => request<ScenarioSummary[]>("/scenarios"),

  scenario: (name: string) => request<Scenario>(`/scenarios/${name}`),

  validate: (scenario: unknown) =>
    post<{ valid: true; summary: ScenarioSummary }>("/scenarios/validate", scenario),

  run: (design: DesignRef, options?: { strategy?: Strategy; save_as?: string }) =>
    post<Run>("/runs", { ...design, ...options }),

  rerun: (runId: string) => request<Run>(`/runs/${runId}`),

  snapshot: (runId: string, tSeconds: number) =>
    request<Snapshot>(`/runs/${runId}/snapshot?t_s=${tSeconds}`),

  /** The export is a download, so it is fetched as a blob and handed to the browser. */
  exportUrl: (runId: string) => `/api/runs/${runId}/export`,

  variants: () => request<VariantListing[]>("/variants"),

  saveVariant: (label: string, design: DesignRef) =>
    post<{ id: string; label: string }>("/variants", { label, ...design }),

  variant: (id: string) =>
    request<{ id: string; label: string; scenario: Scenario }>(`/variants/${id}`),

  deleteVariant: (id: string) => request<void>(`/variants/${id}`, { method: "DELETE" }),

  compare: (runIds: string[], labels?: string[]) =>
    post<Comparison>("/compare", { run_ids: runIds, labels }),

  criticality: (design: DesignRef) =>
    post<CriticalityReport>("/analysis/criticality", design),

  sweep: (design: DesignRef, mode: "spacing" | "refine" = "spacing") =>
    post<SweepReport>("/analysis/sweep", { ...design, mode }),
};

/**
 * Russian wording for the validation codes the service can produce.
 *
 * Anything not listed falls back to the English message from the API, which is
 * always present — an unfamiliar code should still tell the user something.
 */
const ERROR_TEXT: Record<string, string> = {
  unsupported_schema: "Неподдерживаемая версия формата. Ожидается cosmo-A-1.0.",
  malformed_json: "Файл не является корректным JSON.",
  not_an_object: "Сценарий должен быть объектом JSON.",
  duplicate_id: "Идентификатор повторяется.",
  unknown_plane: "Ссылка на несуществующую плоскость.",
  unknown_satellite: "Ссылка на несуществующий аппарат.",
  unknown_gateway: "Ссылка на несуществующий шлюз.",
  id_collides_with_satellite: "Идентификатор уже занят спутником.",
  no_client: "Нужен хотя бы один клиентский пункт.",
  no_gateway: "Нужен хотя бы один шлюз.",
  step_exceeds_horizon: "Шаг расчёта больше горизонта.",
  horizon_not_multiple_of_step: "Горизонт не кратен шагу расчёта.",
  interval_before_zero: "Интервал начинается раньше нуля.",
  interval_not_positive: "Интервал имеет нулевую или отрицательную длительность.",
  interval_past_horizon: "Интервал выходит за горизонт расчёта.",
  int_type: "Значение должно быть целым числом секунд.",
  missing: "Обязательное поле отсутствует.",
  greater_than_equal: "Значение меньше допустимого.",
  less_than_equal: "Значение больше допустимого.",
  greater_than: "Значение меньше допустимого.",
  less_than: "Значение больше допустимого.",
};

export function describeError(error: FieldError): string {
  return ERROR_TEXT[error.code] ?? error.message;
}
