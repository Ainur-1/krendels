/** Formatting shared by every panel, so the same quantity never appears two ways. */

import type { OutageCause } from "../types";

/** Shares arrive from the API in fractions of one; percent is a presentation choice. */
export function percent(share: number, digits = 1): string {
  return `${(share * 100).toFixed(digits)} %`;
}

/** A point on the calculation grid, as time of day from the start of the run. */
export function clock(seconds: number): string {
  const whole = Math.round(seconds);
  const hh = Math.floor(whole / 3600);
  const mm = Math.floor((whole % 3600) / 60);
  const ss = whole % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
}

/** A duration, in the largest unit that keeps it readable. */
export function duration(seconds: number): string {
  if (seconds === 0) return "нет";
  if (seconds < 60) return `${seconds} с`;
  if (seconds < 3600) {
    const minutes = seconds / 60;
    return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} мин`;
  }
  return `${(seconds / 3600).toFixed(1)} ч`;
}

export function km(value: number | null): string {
  return value === null ? "—" : `${Math.round(value).toLocaleString("ru-RU")} км`;
}

export function decimal(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

export const CAUSE_LABEL: Record<OutageCause, string> = {
  none: "связь есть",
  no_client_contact: "нет спутника над пунктом",
  gateway_offline: "шлюз недоступен",
  no_gateway_contact: "нет спутника над шлюзом",
  network_split: "разрыв межспутниковой сети",
};

/** Short enough for a chart axis or a dense table cell. */
export const CAUSE_SHORT: Record<OutageCause, string> = {
  none: "связь",
  no_client_contact: "нет над пунктом",
  gateway_offline: "шлюз выключен",
  no_gateway_contact: "нет над шлюзом",
  network_split: "разрыв сети",
};

export const CAUSE_COLOUR: Record<OutageCause, string> = {
  none: "#2d7d3f",
  no_client_contact: "var(--cause-client)",
  gateway_offline: "var(--cause-gateway-offline)",
  no_gateway_contact: "var(--cause-gateway)",
  network_split: "var(--cause-split)",
};

/**
 * Resolved colours for canvas, which cannot read CSS variables.
 *
 * Duplicated from styles.css on purpose and deliberately kept in the same order:
 * the map and the timeline have to agree about what orange means.
 */
export const CAUSE_RGB: Record<OutageCause, string> = {
  none: "#2d7d3f",
  no_client_contact: "#f0883e",
  gateway_offline: "#a371f7",
  no_gateway_contact: "#d29922",
  network_split: "#f85149",
};

/** Distinct colours for orbital planes, extended by rotation for larger designs. */
const PLANE_COLOURS = [
  "#4c9aff",
  "#3fb950",
  "#f0883e",
  "#a371f7",
  "#e05c8a",
  "#2bb6b0",
  "#d29922",
  "#8b949e",
];

export function planeColour(planeIds: string[], planeId: string): string {
  const index = planeIds.indexOf(planeId);
  return PLANE_COLOURS[(index < 0 ? 0 : index) % PLANE_COLOURS.length];
}
