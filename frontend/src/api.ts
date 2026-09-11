/**
 * Типизированный клиент и единственное место, где отказ проверки превращается в показуемое.
 *
 * Ответ 422 от этого сервиса — не ошибка в обычном смысле, а ответ на вопрос «что не
 * так с моим файлом», и кейс оценивает сервис по тому, насколько внятно он этот ответ
 * даёт. Поэтому он приходит как `ScenarioInvalidError` со всем списком проблем по
 * полям, а не строкой, и панель загрузки показывает их против названных полей.
 */

import type {
  Comparison,
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
  Snapshot,
  Strategy,
  SweepReport,
  Trajectory,
  VariantListing,
} from "./types";

/** Любой из трёх способов, которыми API принимает проект. Задан должен быть ровно один. */
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
    // Собственная проверка тела запроса в FastAPI тоже отвечает 422, но в своей форме.
    // Это означает ошибку в этом клиенте, а не плохой файл, и сообщается именно так.
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

  /** Вся геометрия прогона одним ответом — почему не по отсчётам, объяснено в MapView. */
  trajectory: (runId: string) => request<Trajectory>(`/runs/${runId}/trajectory`),

  /** Выгрузка — это скачивание, поэтому ссылка отдаётся браузеру напрямую. */
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

  delivery: (design: DesignRef) => post<DeliveryReport>("/analysis/delivery", design),

  /**
   * Запас маршрутов привязан к прогону, а не к проекту: им раскрашивается шкала
   * того самого прогона, который сейчас показан на экране.
   */
  redundancy: (runId: string) => request<RedundancyReport>(`/runs/${runId}/redundancy`),

  degradation: (design: DesignRef, options?: { max_failures?: number; trials?: number }) =>
    post<DegradationCurve>("/analysis/degradation", { ...design, ...options }),

  placement: (
    design: DesignRef,
    grid?: { lat_step_deg?: number; lon_step_deg?: number },
  ) => post<PlacementReport>("/analysis/placement", { ...design, ...grid }),

  families: (design: DesignRef) => post<FamilyReport>("/analysis/families", design),
};

/**
 * Формулировки для кодов проверки, которые может вернуть сервис.
 *
 * Для кода, которого здесь нет, берётся сообщение из ответа API — оно есть всегда.
 * Незнакомый код всё равно должен что-то сказать пользователю.
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
