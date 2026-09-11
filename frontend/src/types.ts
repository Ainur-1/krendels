/**
 * Формы данных, которые возвращает API. Сверяются с docs/api.md вручную.
 *
 * Каждый массив «по отсчётам» в `ClientSeries` имеет ту же длину, что и `times_s`, и
 * индексируется тем же номером отсчёта. Именно поэтому ползунок времени читает целый
 * кадр из памяти одним индексом, а не собирает его из нескольких обращений.
 */

export type Role = "client" | "gateway";

export type OutageCause =
  | "none"
  | "no_client_contact"
  | "gateway_offline"
  | "no_gateway_contact"
  | "network_split";

export type Strategy = "min_hops" | "min_distance" | "max_margin";

export interface Environment {
  altitude_km: number;
  inclination_deg: number;
  earth_angle0_deg: number;
  horizon_s: number;
  step_s: number;
  min_elevation_deg: number;
  isl_range_km: number;
  target_availability: number;
}

export interface Plane {
  id: string;
  raan_deg: number;
  phase_deg: number;
}

export interface Satellite {
  id: string;
  plane_id: string;
  slot_deg: number;
  launch_batch: 1 | 2 | 3;
}

export interface GroundSite {
  id: string;
  name: string;
  role: Role;
  lat_deg: number;
  lon_deg: number;
}

export interface Failure {
  satellite_id: string;
  start_s: number;
  end_s: number;
}

export interface GatewayOutage {
  gateway_id: string;
  start_s: number;
  end_s: number;
}

export interface Scenario {
  schema_version: string;
  meta: { id: string; title: string };
  environment: Environment;
  design: { launch_stage: 1 | 2 | 3; planes: Plane[]; satellites: Satellite[] };
  ground_sites: GroundSite[];
  failures: Failure[];
  gateway_outages: GatewayOutage[];
}

export interface ClientMetrics {
  client_id: string;
  steps: number;
  visibility_share: number;
  availability_share: number;
  meets_target: boolean;
  max_gap_s: number;
  max_interior_gap_s: number;
  gap_count: number;
  mean_hops: number | null;
  max_hops: number | null;
  mean_route_length_km: number | null;
  max_route_length_km: number | null;
  /** Задержка распространения туда и обратно. Только распространение: очередей в модели нет. */
  mean_rtt_ms: number | null;
  max_rtt_ms: number | null;
  causes: Record<string, number>;
}

export interface Gap {
  start_s: number;
  end_s: number;
  duration_s: number;
  cause: OutageCause;
  causes: Record<string, number>;
  at_horizon_edge: boolean;
}

export interface ClientSeries {
  metrics: ClientMetrics;
  reachable: boolean[];
  cause: OutageCause[];
  hops: (number | null)[];
  path: string[][];
  gateway: (string | null)[];
  length_km: (number | null)[];
  gaps: Gap[];
}

export interface RunSummary {
  scenario_id: string;
  strategy: Strategy;
  steps: number;
  step_s: number;
  horizon_s: number;
  target_availability: number;
  worst_availability: number;
  worst_max_gap_s: number;
  meets_target: boolean;
  mean_rtt_ms: number | null;
  max_rtt_ms: number | null;
  clients: ClientMetrics[];
}

export interface Run {
  run_id: string;
  variant_id: string | null;
  times_s: number[];
  summary: RunSummary;
  clients: Record<string, ClientSeries>;
  scenario: Scenario;
  outage_causes: OutageCause[];
}

export interface SnapshotSatellite {
  id: string;
  x_km: number;
  y_km: number;
  z_km: number;
  lat_deg: number;
  lon_deg: number;
  plane_id: string;
  active: boolean;
}

export interface Snapshot {
  t_s: number;
  satellites: SnapshotSatellite[];
  links: { a: string; b: string; distance_km: number }[];
  ground_links: {
    site: string;
    satellite: string;
    distance_km: number;
    elevation_deg: number;
  }[];
  elevation_deg: Record<string, Record<string, number>>;
  ground_sites: GroundSite[];
}

/**
 * Положения и связи на весь прогон, запрашиваемые один раз.
 *
 * Каждый внешний массив индексируется номером отсчёта, согласованно с `Run.times_s`.
 * `ecef_km` — гринвичские координаты, округлённые до километра: именно это позволяет
 * карте интерполировать между отсчётами без особых случаев на 180-м меридиане и у
 * полюсов.
 */
export interface Trajectory {
  times_s: number[];
  step_s: number;
  satellite_ids: string[];
  plane_ids: string[];
  ecef_km: [number, number, number][][];
  active: boolean[][];
  links: [number, number][][];
  ground_visible: Record<string, number[][]>;
  ground_sites: GroundSite[];
}

export interface ScenarioSummary {
  source: string;
  id: string;
  title: string;
  planes: number;
  satellites: number;
  launch_stage: number;
  clients: { id: string; name: string }[];
  gateways: { id: string; name: string }[];
  steps: number;
  environment: Environment;
  failures: number;
  gateway_outages: number;
}

export interface VariantListing {
  id: string;
  label: string;
  created_at: string;
  scenario_id: string;
  launch_stage: number;
  planes: number;
  satellites: number;
}

export interface Candidate {
  raan_deg: number[];
  phase_deg: number[];
  launch_stage: number;
  worst_availability: number;
  worst_max_gap_s: number;
  availability: Record<string, number>;
  /** Отранжировано по прореженной сетке, а не измерено. Показывать как число нельзя. */
  approximate: boolean;
}

export interface SweepReport {
  baseline: Candidate;
  best: Candidate;
  frontier: Candidate[];
  candidates: Candidate[];
}

export interface Knockout {
  satellite_id: string;
  plane_id: string;
  worst_availability: number;
  drop_pp: number;
  worst_max_gap_s: number;
}

export interface CriticalityReport {
  baseline_worst_availability: number;
  baseline_worst_max_gap_s: number;
  spread_pp: number;
  knockouts: Knockout[];
}

export interface ComparisonCell {
  availability_share: number;
  visibility_share: number;
  max_gap_s: number;
  gap_count: number;
  mean_hops: number | null;
  mean_route_length_km: number | null;
  meets_target: boolean;
}

export interface Comparison {
  comparable: boolean;
  grids: [number, number][];
  runs: {
    label: string;
    scenario_id: string;
    strategy: Strategy;
    worst_availability: number;
    worst_max_gap_s: number;
    meets_target: boolean;
  }[];
  clients: { client_id: string; cells: (ComparisonCell | null)[] }[];
  changes: { path: string; before: unknown; after: unknown }[];
}

/**
 * Доставка с допустимой задержкой.
 *
 * Строка с `deadline_s: 0` — это мгновенная доступность, та же, что в ответе расчёта.
 * Остальные отвечают на вопрос, которого постановка не задаёт: для какого класса
 * трафика конфигурация провалена.
 */
export interface DeliveryShare {
  deadline_s: number;
  share: number;
}

export interface ClientDelivery {
  client_id: string;
  undelivered_share: number;
  max_latency_s: number | null;
  within: DeliveryShare[];
}

export interface DeliveryReport {
  deadlines_s: number[];
  worst_within: DeliveryShare[];
  worst_max_latency_s: number | null;
  clients: ClientDelivery[];
}

/** Сколько маршрутов без общих аппаратов есть на каждом отсчёте. Ноль — маршрута нет. */
export interface ClientRedundancy {
  client_id: string;
  mean_disjoint_paths: number;
  no_path_share: number;
  single_path_share: number;
  redundant_share: number;
  max_disjoint_paths: number;
}

export interface RedundancyReport {
  worst_single_path_share: number;
  clients: ClientRedundancy[];
  times_s: number[];
  series: Record<string, number[]>;
}

export interface DegradationPoint {
  failures: number;
  trials: number;
  mean_worst_availability: number;
  best_worst_availability: number;
  worst_worst_availability: number;
  meets_target_share: number;
}

export interface DegradationCurve {
  target_availability: number;
  satellites_in_service: number;
  seed: number;
  /** Наибольшее число отказов, при котором цель удержана в **каждом** наборе. */
  tolerated_failures: number;
  slope_pp_per_satellite: number;
  linear_fit_error_pp: number;
  points: DegradationPoint[];
}

export interface PlacementPoint {
  lat_deg: number;
  lon_deg: number;
  worst_availability: number;
  gain_pp: number;
}

export interface PlacementReport {
  baseline_worst_availability: number;
  lat_deg: number[];
  lon_deg: number[];
  best: PlacementPoint | null;
  /** По строкам: для каждой широты все долготы. */
  points: PlacementPoint[];
}

export interface SpacingPoint {
  spacing_deg: number;
  worst_availability: number;
}

export interface FamilyReport {
  planes: number;
  supplied_spacing_deg: number | null;
  /** `null`, если плоскости разнесены неравномерно: тогда относить проект не к чему. */
  supplied_family: "star" | "delta" | null;
  star_spacing_deg: number;
  delta_spacing_deg: number;
  star_best: SpacingPoint | null;
  delta_best: SpacingPoint | null;
  points: SpacingPoint[];
}

export interface FieldError {
  field: string;
  code: string;
  message: string;
}
