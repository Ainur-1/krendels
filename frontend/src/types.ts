/**
 * The shapes the API returns. Kept in step with docs/api.md by hand.
 *
 * Every per-step array in `ClientSeries` has the same length as `times_s` and is
 * indexed by the same step, which is what lets the time slider read a whole frame
 * out of memory with one index rather than assembling it from several lookups.
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
 * Positions and links for the whole run, fetched once.
 *
 * Every outer array is indexed by step, in step with `Run.times_s`. `ecef_km` is
 * Earth-fixed and rounded to the kilometre, which is what lets the map interpolate
 * between steps without the date line and the poles needing special cases.
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
  /** Ranked on a sampled grid rather than measured. Never quote these as figures. */
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

export interface FieldError {
  field: string;
  code: string;
  message: string;
}
