import type { DataPoint, DeviceTelemetry, DeviceLatestTelemetry } from "@/types/telemetry";

const ANCHOR_DAY_MS = new Date("2026-09-22T00:00:00+07:00").getTime();
const INTERVAL_MS   = 5 * 60 * 1_000;
const POINTS        = 288;
const ENERGY_STEP   = 12.5 / POINTS; // ≈ 0.043403 kWh per 5-min interval

function hourOf(i: number): number {
  return (i * 5) / 60;
}

// Solar envelope: exactly 0 outside [6, 18), sine bell inside
function solarFactor(h: number): number {
  if (h < 6 || h >= 18) return 0;
  return Math.sin(((h - 6) / 12) * Math.PI);
}

// Deterministic noise via sine, no Math.random(); only called inside solar window
function solarNoise(i: number, freq: number, amp: number): number {
  return amp * Math.sin(i * freq * 0.31 + freq);
}

function buildSeries(fn: (i: number, h: number) => number): DataPoint[] {
  const out: DataPoint[] = [];
  for (let i = 0; i < POINTS; i++) {
    out.push({ ts: ANCHOR_DAY_MS + i * INTERVAL_MS, value: fn(i, hourOf(i)) });
  }
  return out;
}

function buildMpptSeries() {
  return {
    pvVoltage: buildSeries((i, h) => {
      const f = solarFactor(h);
      if (f === 0) return 0;
      return parseFloat((f * 26 + solarNoise(i, 1.7, 0.4)).toFixed(2));
    }),
    pvCurrent: buildSeries((i, h) => {
      const f = solarFactor(h);
      if (f === 0) return 0;
      return parseFloat((f * 8 + solarNoise(i, 2.1, 0.15)).toFixed(2));
    }),
    outputVoltage: buildSeries((i, h) => {
      const f = solarFactor(h);
      if (f === 0) return 0;
      return parseFloat((13.8 + solarNoise(i, 1.3, 0.1)).toFixed(2));
    }),
    outputCurrent: buildSeries((i, h) => {
      const f = solarFactor(h);
      if (f === 0) return 0;
      return parseFloat((f * 6 + solarNoise(i, 1.9, 0.12)).toFixed(2));
    }),
  };
}

function buildPzemSeries() {
  function gridNoise(i: number, freq: number, amp: number): number {
    return amp * Math.sin(i * freq * 0.31 + freq);
  }
  return {
    phaseAVoltage: buildSeries((i) => parseFloat((220 + gridNoise(i, 1.1, 3)).toFixed(1))),
    phaseBVoltage: buildSeries((i) => parseFloat((219 + gridNoise(i, 1.3, 3)).toFixed(1))),
    phaseCVoltage: buildSeries((i) => parseFloat((221 + gridNoise(i, 0.9, 3)).toFixed(1))),
    phaseACurrent: buildSeries((i) => parseFloat((2.5 + gridNoise(i, 2.3, 0.4)).toFixed(2))),
    phaseBCurrent: buildSeries((i) => parseFloat((2.3 + gridNoise(i, 2.7, 0.35)).toFixed(2))),
    phaseCCurrent: buildSeries((i) => parseFloat((2.6 + gridNoise(i, 2.1, 0.38)).toFixed(2))),
    // Monotonically increasing; final point = 12.5 kWh exactly
    energy: buildSeries((i) => parseFloat((ENERGY_STEP * (i + 1)).toFixed(4))),
  };
}

function buildSolarSeries() {
  // Daylight bell for the pyranometer: zero outside 06:00 to 18:00, peak at midday.
  const fiveMinutes = INTERVAL_MS / 60_000;
  return {
    radiation: buildSeries((i) => {
      const hour = ((i * fiveMinutes) / 60) % 24;
      if (hour < 6 || hour >= 18) return 0;
      const daylight = Math.sin(((hour - 6) / 12) * Math.PI);
      return parseFloat((daylight * 846).toFixed(1));
    }),
  };
}

export const MOCK_TELEMETRY: DeviceTelemetry = {
  resolution: "hourly",
  // The mock grid is 5 min; stating it keeps the chart's interval note and its
  // partial-bucket marker exercising the same path the real API drives.
  seriesIntervalMs: INTERVAL_MS,
  deviceId: "RUT-TEST-001",
  mppt: buildMpptSeries(),
  pzem: buildPzemSeries(),
  solar: buildSolarSeries(),
};

// Fixed midday snapshot used for dashboard cards and device overview.
// NOT derived from historical last point (which is 23:55, so solar = 0).
export const MOCK_LATEST_TELEMETRY: DeviceLatestTelemetry = {
  deviceId:  "RUT-TEST-001",
  // Fixed midday snapshot, NOT the last historical point (23:55, solar = 0)
  timestamp: new Date("2026-09-22T12:00:00+07:00").getTime(),
  dataAge:   "fresh",
  mppt: {
    pvVoltage:     24.8,
    pvCurrent:     8.4,
    outputVoltage: 24.6,
    outputCurrent: 7.9,
  },
  pzem: {
    phaseAVoltage: 220.1,
    phaseBVoltage: 220.4,
    phaseCVoltage: 219.8,
    phaseACurrent: 1.2,
    phaseBCurrent: 1.1,
    phaseCCurrent: 1.3,
    energy:        12.5,
  },
  solar: {
    radiation: 812.4,
  },
};
