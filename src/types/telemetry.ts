import type { DataAge } from "@/types/device";

export interface DataPoint {
  ts: number;
  value: number;
  avg?: number;
  min?: number;
  max?: number;
  count?: number;
  delta?: number;
  quality?: string;
}

export interface MpptTelemetry {
  pvVoltage:      DataPoint[];
  pvCurrent:      DataPoint[];
  outputVoltage:  DataPoint[];
  outputCurrent:  DataPoint[];
  pvInputPower?:  DataPoint[];
  outputPower?:   DataPoint[];
}

export interface PzemTelemetry {
  phaseAVoltage: DataPoint[];
  phaseBVoltage: DataPoint[];
  phaseCVoltage: DataPoint[];
  phaseACurrent: DataPoint[];
  phaseBCurrent: DataPoint[];
  phaseCCurrent: DataPoint[];
  energy:        DataPoint[];
}

// Solar irradiance sensor (Honde RD-PSR-01 pyranometer). Radiation is served
// 1:1 in W/m² (physical range 0-1800) for every resolution (raw, hourly and
// daily) with no backend scaling.
export interface SolarTelemetry {
  radiation: DataPoint[];
}

export type EnergyQuality = "VALID" | "RESET_DETECTED" | "INSUFFICIENT_DATA" | "MISSING_DATA";

export interface EnergySummary {
  accumulatedKwh: number | null;
  periodDeltaKwh: number;
  todayKwh: number;
  quality: EnergyQuality;
  coverageRatio: number;
  windowTimeCoverageRatio?: number;
  sampleCoverageRatio?: number;
}

export type TelemetryResolution = "raw" | "hourly";

// Window the backend was actually asked for and served. Requested bounds are
// echoed in ms; null means the bound was not specified. `truncated` is true
// when raw history could not fill the window.
export interface TelemetryWindow {
  fromMs:            number | null;
  toMs:              number | null;
  requestedFrom?:    number | null;
  requestedTo?:      number | null;
  requestedDuration?: number;
  availableFrom?:    number | null;
  availableTo?:      number | null;
  availableDuration?: number;
  observedDuration?:  number;
  availableSpanRatio?: number;
  resolution?:       TelemetryResolution;
  limit?:            number;
  truncated?:        boolean;
  maxPoints?:        number;
  dataPoints?:       number;
  expectedPoints?:   number;
  coverageRatio?:    number;
  windowTimeCoverageRatio?: number;
  sampleCoverageRatio?:     number;
  complete?:         boolean;
  hasGaps?:          boolean;
  gapCount?:         number;
  leadingGap?:       boolean;
  trailingGap?:      boolean;
  quality?:          EnergyQuality;
}

/**
 * why a metric has no samples. An empty array alone cannot say whether
 * the item was never wired up or merely sent nothing in the window, so the
 * backend names the case.
 */
export type MetricState =
  | "not_integrated"
  | "no_data_in_range"
  | "data"
  | "error";

/**
 *  health source integration state. `not_integrated` means no
 * ubus/telemetry source is wired up at all, which is a different fact from a source
 * that exists and currently reports nothing.
 */
export type HealthSourceState =
  | "not_integrated"
  | "available"
  | "no_data"
  | "error";

export interface DeviceTelemetry {
  deviceId: string;
  resolution?: TelemetryResolution;
  // Interval each plotted point covers, chosen server-side from the requested
  // span so the point count tracks the panel width. Absent on older or mock
  // payloads; the chart then simply omits its interval note.
  seriesIntervalMs?: number;
  mppt: MpptTelemetry;
  pzem: PzemTelemetry;
  energySummary?: EnergySummary;
  // Solar irradiance history (W/m²). Absent or null when the device has no
  // solar source; metricState["solar.radiation"] names which case applies.
  solar?: SolarTelemetry | null;
  // Absent on older or mock payloads; consumers must tolerate its absence.
  window?: TelemetryWindow;
  // Keyed by series path, e.g. "mppt.pvVoltage". Absent on older or mock
  // payloads: fall back to inferring from series length, never to a default
  // that claims integration.
  metricState?: Record<string, MetricState>;
}

// Derived electrical metrics (Watts) computed when both inputs are valid non-negative numbers
export interface DerivedTelemetry {
  pvInputPower: number | null;
  outputPower:  number | null;
}

// Latest snapshot: all fields nullable. null means NO DATA, not zero
export interface LatestMppt {
  pvVoltage:     number | null;
  pvCurrent:     number | null;
  outputVoltage: number | null;
  outputCurrent: number | null;
}

export interface LatestPzem {
  phaseAVoltage: number | null;
  phaseBVoltage: number | null;
  phaseCVoltage: number | null;
  phaseACurrent: number | null;
  phaseBCurrent: number | null;
  phaseCCurrent: number | null;
  energy:        number | null;
}

export interface DeviceLatestTelemetry {
  deviceId:  string;
  timestamp: number;    // ms epoch of the latest reading
  dataAge:   DataAge;
  mppt:      LatestMppt | null;   // null = sensor not available
  pzem:      LatestPzem | null;
  // Solar snapshot in W/m² (1:1, range 0-1800). null = no solar source or no
  // reading yet; a real 0 W/m² reading is never conflated with NO DATA.
  solar?: { radiation: number | null } | null;
  derived?: DerivedTelemetry | null;
}

// Health / system metrics
export interface HealthDataPoint { ts: number; value: number; }

export interface DeviceHealthHistory {
  deviceId:    string;
  cpu:         HealthDataPoint[];  // percent 0-100
  ram:         HealthDataPoint[];  // percent 0-100
  temperature: HealthDataPoint[];  // °C
  rssi:        HealthDataPoint[];  // dBm (negative)
  // absent on older or mock payloads. Absence must not be read as "live".
  state?: HealthSourceState;
}

export interface DeviceHealthSnapshot {
  deviceId:         string;
  cpuPercent:       number | null; // null = NO DATA
  ramPercent:       number | null;
  flashPercent:     number | null;
  temperatureC:     number | null;
  uptimeSeconds:    number | null;
  rssi:             number | null; // dBm; null = NO DATA
  networkState:     string | null;
  mqttConnected:    boolean | null;
  lastTelemetryAge: number | null; // seconds; null = never received
  // absent on older or mock payloads. Absence must not be read as "live".
  state?: HealthSourceState;
  // Named health dimensions; `routerHealth` carries the values measured on the
  // device itself (load, cores, RSRP, operator) that have no flat field above.
  dimensions?: Record<string, Record<string, unknown>>;
}
