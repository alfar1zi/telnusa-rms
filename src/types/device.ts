import type { DeviceLatestTelemetry } from "./telemetry";

export type DeviceStatus       = "online" | "offline" | "stale" | "clock_skew" | "sensor_fault" | "partial" | "degraded" | "no_data" | "unknown";
export type HealthState        = "healthy" | "warning" | "critical" | "unknown" | "no_data" | "not_integrated";
export type ConnectivityState  = "connected" | "degraded" | "disconnected" | "unknown" | "no_data";
export type SensorState        = "all_ok" | "ok" | "partial" | "no_data" | "sensor_fault" | "not_installed" | "not_integrated" | "warning";
export type AcquisitionState   = "all_ok" | "partial" | "no_data" | "sensor_fault" | "not_integrated";
export type DataAge            = "fresh" | "stale" | "offline" | "no_data";
export type UserRole           = "admin" | "operator" | "viewer";

export interface Device {
  id:           string;
  name:         string;
  model:        string;
  serial:       string;
  siteId:       string;        // raw site identifier, never fabricated
  siteName:     string | null; // null = UNASSIGNED (site name not yet mapped)
  status:       DeviceStatus;
  lastSeen:     number | null; // ms epoch; null = never seen
  network:      string | null; // null = UNKNOWN
  health:       HealthState;
  connectivity: ConnectivityState;
  dataAge:      DataAge;
  mpptState:    SensorState;
  pzemState:    SensorState;
  sensor3State: SensorState;
  clockSkew?:        boolean;      // true = device clock far behind server receipt
  clockSkewSeconds?: number | null; // received - source, seconds
  acquisitionState?: AcquisitionState;
  lastPayload?:      DeviceLatestTelemetry | null;
}
