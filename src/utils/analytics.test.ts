import { test } from "node:test";
import assert from "node:assert/strict";
import type { DeviceTelemetry } from "../types/telemetry.ts";

test("R3.1: multi-resolution telemetry payload preserves energySummary and derived powers", () => {
  const tel: DeviceTelemetry = {
    deviceId: "RUT-1234567890",
    resolution: "hourly",
    mppt: {
      pvVoltage: [{ ts: 1700000000000, value: 20.0, avg: 20.0, min: 19.5, max: 20.5, count: 180 }],
      pvCurrent: [{ ts: 1700000000000, value: 5.0, avg: 5.0, min: 4.8, max: 5.2, count: 180 }],
      outputVoltage: [{ ts: 1700000000000, value: 24.0, avg: 24.0, min: 23.8, max: 24.2, count: 180 }],
      outputCurrent: [{ ts: 1700000000000, value: 4.0, avg: 4.0, min: 3.8, max: 4.2, count: 180 }],
      pvInputPower: [{ ts: 1700000000000, value: 100.0, avg: 100.0, min: 95.0, max: 105.0, count: 180 }],
      outputPower: [{ ts: 1700000000000, value: 96.0, avg: 96.0, min: 91.0, max: 101.0, count: 180 }],
    },
    pzem: {
      phaseAVoltage: [],
      phaseBVoltage: [],
      phaseCVoltage: [],
      phaseACurrent: [],
      phaseBCurrent: [],
      phaseCCurrent: [],
      energy: [{ ts: 1700000000000, value: 150.0, delta: 1.5, quality: "VALID" }],
    },
    solar: {
      radiation: [
        { ts: 1700000000000, value: 0 },
        { ts: 1700000300000, value: 812 },
      ],
    },
    metricState: {
      "solar.radiation": "data",
      "mppt.pvVoltage": "data",
    },
    energySummary: {
      accumulatedKwh: 150.0,
      periodDeltaKwh: 1.5,
      todayKwh: 3.2,
      quality: "VALID",
      coverageRatio: 1.0,
    },
    window: {
      fromMs: 1700000000000,
      toMs: 1700086400000,
      range: "24h",
      dataPoints: 24,
      coverageRatio: 1.0,
      quality: "VALID",
    },
  };

  assert.equal(tel.resolution, "hourly");
  assert.equal(tel.solar?.radiation.length, 2);
  // 0 W/m² is a real night-time reading, never conflated with NO DATA.
  assert.equal(tel.solar?.radiation[0].value, 0);
  assert.equal(tel.solar?.radiation[1].value, 812);
  assert.equal(tel.metricState?.["solar.radiation"], "data");
  assert.equal(tel.energySummary?.periodDeltaKwh, 1.5);
  assert.equal(tel.energySummary?.todayKwh, 3.2);
  assert.equal(tel.energySummary?.accumulatedKwh, 150.0);
  assert.equal(tel.mppt.pvInputPower?.[0].avg, 100.0);
});

test("R3.1: meter reset detection flag is correctly structured in payload", () => {
  const tel: DeviceTelemetry = {
    deviceId: "RUT-1234567890",
    resolution: "daily",
    mppt: {
      pvVoltage: [],
      pvCurrent: [],
      outputVoltage: [],
      outputCurrent: [],
    },
    pzem: {
      phaseAVoltage: [],
      phaseBVoltage: [],
      phaseCVoltage: [],
      phaseACurrent: [],
      phaseBCurrent: [],
      phaseCCurrent: [],
      energy: [],
    },
    energySummary: {
      accumulatedKwh: 0.8,
      periodDeltaKwh: 2.3,
      todayKwh: 0.8,
      quality: "RESET_DETECTED",
      coverageRatio: 0.95,
    },
  };

  assert.equal(tel.energySummary?.quality, "RESET_DETECTED");
  assert.equal(tel.energySummary?.periodDeltaKwh, 2.3);
});

test("R3.1: solar irradiance series is served for every resolution and absence is named", () => {
  const series = [
    { ts: 1700000000000, value: 0 },
    { ts: 1700003600000, value: 812 },
  ];
  for (const resolution of ["raw", "hourly", "daily"] as const) {
    const tel: DeviceTelemetry = {
      deviceId: "RUT-1234567890",
      resolution,
      mppt: { pvVoltage: [], pvCurrent: [], outputVoltage: [], outputCurrent: [] },
      pzem: {
        phaseAVoltage: [],
        phaseBVoltage: [],
        phaseCVoltage: [],
        phaseACurrent: [],
        phaseBCurrent: [],
        phaseCCurrent: [],
        energy: [],
      },
      solar: { radiation: series },
      metricState: { "solar.radiation": "data" },
    };
    assert.equal(tel.solar?.radiation.length, 2);
    assert.equal(tel.solar?.radiation[1].value, 812);
    assert.equal(tel.metricState?.["solar.radiation"], "data");
  }

  // Every empty-solar case is named by metricState, never inferred from an
  // empty array: no data in the window is a different fact from not integrated.
  const noData: DeviceTelemetry = {
    deviceId: "RUT-1234567880",
    mppt: { pvVoltage: [], pvCurrent: [], outputVoltage: [], outputCurrent: [] },
    pzem: {
      phaseAVoltage: [],
      phaseBVoltage: [],
      phaseCVoltage: [],
      phaseACurrent: [],
      phaseBCurrent: [],
      phaseCCurrent: [],
      energy: [],
    },
    solar: { radiation: [] },
    metricState: { "solar.radiation": "no_data_in_range" },
  };
  assert.equal(noData.solar?.radiation.length, 0);
  assert.equal(noData.metricState?.["solar.radiation"], "no_data_in_range");

  // Legacy device with no solar source: series absent, state names the case.
  const notIntegrated: DeviceTelemetry = {
    deviceId: "RUT-6008586881",
    mppt: { pvVoltage: [], pvCurrent: [], outputVoltage: [], outputCurrent: [] },
    pzem: {
      phaseAVoltage: [],
      phaseBVoltage: [],
      phaseCVoltage: [],
      phaseACurrent: [],
      phaseBCurrent: [],
      phaseCCurrent: [],
      energy: [],
    },
    solar: null,
    metricState: { "solar.radiation": "not_integrated" },
  };
  assert.equal(notIntegrated.solar, null);
  assert.equal(notIntegrated.metricState?.["solar.radiation"], "not_integrated");
});
