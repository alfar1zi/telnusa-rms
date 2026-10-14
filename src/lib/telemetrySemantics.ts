/**
 * single authority for telemetry metric state and health source state.
 *
 * The rule these encode: "this metric was never wired up" and "this metric
 * exists but sent nothing in the window you asked for" are different facts,
 * and collapsing them into one "no data" string misreports the system. The UI
 * must be able to name each case.
 */
import type { HealthSourceState, MetricState } from "@/types/telemetry";
import { translate } from "../i18n/translate.ts";

/** Dictionary keys, not copy. */
export const METRIC_STATE_LABEL: Record<MetricState, string> = {
  not_integrated:     "metricState.notIntegrated",
  no_data_in_range:    "metricState.noDataInRange",
  data:                "metricState.data",
  error:               "metricState.error",
};

/**
 * Resolve one metric's state.
 *
 * `metricState` is the authority when present. When it is absent (older or mock
 * payload) the series length is all we have, and that can only distinguish
 * "has data" from "no data", never integration. Falling back to
 * `no_data_in_range` is deliberate: it is the conservative claim, because
 * asserting `not_integrated` from an empty array would fabricate a wiring fact.
 */
export function metricState(
  states: Record<string, MetricState> | undefined,
  path: string,
  pointCount: number,
): MetricState {
  const declared = states?.[path];
  if (declared) return declared;
  return pointCount > 0 ? "data" : "no_data_in_range";
}

export function metricStateLabel(state: MetricState): string {
  return translate(METRIC_STATE_LABEL[state] ?? METRIC_STATE_LABEL.error);
}

/**
 * Human message for an empty chart. The generic "No data for selected time
 * range" is only correct when the metric is actually integrated; it is never
 * the right message for an unintegrated metric, because no window would ever
 * fill it.
 */
export function metricEmptyMessage(state: MetricState): string {
  switch (state) {
    // The chip above already renders the state name, so the message carries
    // only the meaning; repeating "NOT INTEGRATED" twice reads as a defect.
    case "not_integrated":  return translate("metricState.empty.notIntegrated");
    case "error":           return translate("metricState.empty.error");
    case "no_data_in_range":return translate("metricState.empty.noDataInRange");
    case "data":            return translate("metricState.empty.data");
  }
}

export const HEALTH_STATE_LABEL: Record<HealthSourceState, string> = {
  not_integrated: "healthState.notIntegrated",
  available:      "healthState.available",
  no_data:        "healthState.noData",
  error:          "healthState.error",
};

/**
 * Resolve the health source state.
 *
 * An absent `state` (older or mock payload) is resolved from the readings
 * themselves: any non-null metric means a source is live. When nothing is
 * present we still cannot claim the source exists, so it degrades to
 * `not_integrated`, the honest reading, and the one that stops the UI
 * implying CPU/RAM/temperature are merely temporarily blank.
 */
export function healthSourceState(
  declared: HealthSourceState | undefined,
  readings: ReadonlyArray<number | boolean | string | null | undefined>,
): HealthSourceState {
  if (declared) return declared;
  return readings.some((v) => v !== null && v !== undefined)
    ? "available"
    : "not_integrated";
}

export function healthStateLabel(state: HealthSourceState): string {
  return translate(HEALTH_STATE_LABEL[state] ?? HEALTH_STATE_LABEL.error);
}