/**
 * Single authority for device state semantics.
 *
 * The backend may legitimately send any of the five states in each model; an
 * unrecognised value must never fall through to a failure/offline meaning,
 * because "we could not tell" and "it is broken" are different facts.
 */
import type { ConnectivityState, DataAge, HealthState } from "@/types/device";
import { translate } from "../i18n/translate.ts";

/** name, then id, then an explicit absent marker. Never a blank cell. */
export function siteLabel(siteName: string | null, siteId: string | null): string {
  // `||` not `??`: a blank name is not an identity, and an empty cell would
  // misreport the device as having no site when its id is known.
  return siteName || siteId || translate("state.unassigned");
}

/** Dictionary keys, not copy: one label set shared by every surface. */
export const CONNECTIVITY_LABEL: Record<ConnectivityState, string> = {
  connected: "state.connected",
  degraded: "state.degraded",
  disconnected: "state.disconnected",
  unknown: "state.unknown",
  no_data: "state.noDataLabel",
};

export function connectivityLabel(state: ConnectivityState): string {
  return translate(CONNECTIVITY_LABEL[state] ?? "state.unknown");
}

export interface HealthSemantic {
  label: string;
  color: string;
}

/**
 * unknown and no_data deliberately use neutral tones. A missing reading must not
 * borrow the red that means confirmed failure.
 */
const HEALTH_LABEL_KEY: Record<HealthState, string> = {
  healthy: "state.healthy",
  warning: "state.warning",
  critical: "state.critical",
  unknown: "state.unknownUpper",
  no_data: "state.noData",
  not_integrated: "state.notIntegrated",
};

const HEALTH_TONE: Record<HealthState, string> = {
  healthy: "var(--status-online)",
  warning: "var(--severity-warning)",
  critical: "var(--status-critical)",
  unknown: "var(--status-unknown)",
  no_data: "var(--muted-foreground)",
  not_integrated: "var(--muted-foreground)",
};

/** Tone plus the translated label for one health state. */
export function healthSemantic(state: HealthState): HealthSemantic {
  const key = HEALTH_LABEL_KEY[state] ?? HEALTH_LABEL_KEY.unknown;
  return { label: translate(key), color: HEALTH_TONE[state] ?? HEALTH_TONE.unknown };
}

/**
 * the MQTT row reflects the collector's real runtime state.
 *
 * Only "SUBSCRIBED"/"CONNECTED" mean the broker session is live; every other
 * collector state (and an absent one) stays Unknown, because inventing
 * "Connected" here would be fabricating telemetry transport health.
 */
const MQTT_LIVE_STATES: Record<string, true> = {
  SUBSCRIBED: true,
  CONNECTED: true,
};

export function mqttStateLabel(runtimeState: string | null | undefined): string {
  if (runtimeState == null) return translate("state.unknownUpper");
  return translate(MQTT_LIVE_STATES[runtimeState] ? "state.connected" : "state.unknown");
}

/** Device status to display, preserving the no_data case from freshness. */
export function deviceStatusLabel(status: string | null | undefined, dataAge: DataAge | null | undefined): string {
  // A device-supplied status is data, not copy: it is never translated.
  if (status) return status.toUpperCase();
  return translate(dataAge === "no_data" ? "state.noData" : "state.unknownUpper");
}