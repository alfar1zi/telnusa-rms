import type { DeviceHealthHistory, DeviceHealthSnapshot } from "@/types/telemetry";

const ANCHOR = new Date("2026-09-22T00:00:00+07:00").getTime();
const INTERVAL = 30 * 60 * 1_000; // 30-min intervals
const POINTS = 48; // 24 h

function buildHealthSeries(
  fn: (i: number) => number,
): { ts: number; value: number }[] {
  return Array.from({ length: POINTS }, (_, i) => ({
    ts: ANCHOR + i * INTERVAL,
    value: fn(i),
  }));
}

// Deterministic wave, no Math.random()
function wave(i: number, base: number, amp: number, freq: number, phase = 0): number {
  return base + amp * Math.sin((i / POINTS) * Math.PI * freq + phase);
}

// RUT-TEST-001: healthy, stable
const health001: DeviceHealthHistory = {
  deviceId: "RUT-TEST-001",
  cpu:         buildHealthSeries((i) => parseFloat(wave(i, 32, 12, 2, 0).toFixed(1))),
  ram:         buildHealthSeries((i) => parseFloat(wave(i, 48, 8,  1, 1).toFixed(1))),
  temperature: buildHealthSeries((i) => parseFloat(wave(i, 42, 4,  1, 0.5).toFixed(1))),
  rssi:        buildHealthSeries((i) => parseFloat(wave(i, -72, 6, 1.5, 0).toFixed(1))),
};

const snapshot001: DeviceHealthSnapshot = {
  deviceId:         "RUT-TEST-001",
  cpuPercent:       34,
  ramPercent:       49,
  flashPercent:     18,
  temperatureC:     43,
  uptimeSeconds:    64_800, // 18 h
  rssi:             -71,
  networkState:     "4G LTE",
  mqttConnected:    true,
  lastTelemetryAge: 42,
};

// RUT-TEST-002: online with warning, high CPU/RAM
const health002: DeviceHealthHistory = {
  deviceId: "RUT-TEST-002",
  cpu:         buildHealthSeries((i) => {
    // Climbs from ~55% to ~85% over the second half of the day
    const base = i < 24 ? 55 : 55 + (i - 24) * 1.25;
    return parseFloat(wave(i, base, 5, 3, 0).toFixed(1));
  }),
  ram:         buildHealthSeries((i) => {
    const base = i < 24 ? 60 : 60 + (i - 24) * 0.8;
    return parseFloat(Math.min(90, wave(i, base, 3, 2, 0.3)).toFixed(1));
  }),
  temperature: buildHealthSeries((i) => parseFloat(wave(i, 51, 5, 1, 0).toFixed(1))),
  rssi:        buildHealthSeries((i) => parseFloat(wave(i, -75, 4, 2, 0.5).toFixed(1))),
};

const snapshot002: DeviceHealthSnapshot = {
  deviceId:         "RUT-TEST-002",
  cpuPercent:       83,
  ramPercent:       87,
  flashPercent:     22,
  temperatureC:     54,
  uptimeSeconds:    57_600, // 16 h
  rssi:             -76,
  networkState:     "4G LTE",
  mqttConnected:    true,
  lastTelemetryAge: 65,
};

// RUT-TEST-003: offline/critical, history only up to hour 8
// Went offline at 08:00 (i=16). Remaining points zeroed (no data).
const health003: DeviceHealthHistory = {
  deviceId: "RUT-TEST-003",
  cpu: buildHealthSeries((i) =>
    i < 16 ? parseFloat(wave(i, 40, 10, 2, 0.2).toFixed(1)) : 0,
  ),
  ram: buildHealthSeries((i) =>
    i < 16 ? parseFloat(wave(i, 52, 6, 1, 0).toFixed(1)) : 0,
  ),
  temperature: buildHealthSeries((i) =>
    i < 16 ? parseFloat(wave(i, 44, 3, 1, 0).toFixed(1)) : 0,
  ),
  rssi: buildHealthSeries((i) =>
    i < 16 ? parseFloat(wave(i, -80, 5, 2, 0).toFixed(1)) : 0,
  ),
};

const snapshot003: DeviceHealthSnapshot = {
  deviceId:         "RUT-TEST-003",
  // Device has been offline since 08:00: no fresh readings available
  cpuPercent:       null,
  ramPercent:       null,
  flashPercent:     null,
  temperatureC:     null,
  uptimeSeconds:    null,
  rssi:             null,
  networkState:     null,
  mqttConnected:    false,  // known disconnected, not unknown
  lastTelemetryAge: 57_600, // ~16 h since last successful reading
};

// RUT-TEST-004: online, degraded connectivity with weak RSSI
const health004: DeviceHealthHistory = {
  deviceId: "RUT-TEST-004",
  cpu:         buildHealthSeries((i) => parseFloat(wave(i, 38, 10, 2, 0.7).toFixed(1))),
  ram:         buildHealthSeries((i) => parseFloat(wave(i, 55, 7, 1.5, 0.2).toFixed(1))),
  temperature: buildHealthSeries((i) => parseFloat(wave(i, 46, 4, 1, 0.3).toFixed(1))),
  // Weak, erratic signal
  rssi: buildHealthSeries((i) => parseFloat(wave(i, -94, 8, 3, 0).toFixed(1))),
};

const snapshot004: DeviceHealthSnapshot = {
  deviceId:         "RUT-TEST-004",
  cpuPercent:       41,
  ramPercent:       58,
  flashPercent:     20,
  temperatureC:     47,
  uptimeSeconds:    72_000, // 20 h
  rssi:             -96,
  networkState:     "4G LTE (weak)",
  mqttConnected:    true,
  lastTelemetryAge: 310,
};

export const MOCK_HEALTH_HISTORY: DeviceHealthHistory[] = [
  health001, health002, health003, health004,
];

export const MOCK_HEALTH_SNAPSHOTS: DeviceHealthSnapshot[] = [
  snapshot001, snapshot002, snapshot003, snapshot004,
];
