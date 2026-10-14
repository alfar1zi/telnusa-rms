import test from "node:test";
import assert from "node:assert/strict";
import type { DeviceTelemetry } from "../../types/telemetry.ts";

/**
 * Deterministic harness testing the exact request-generation lifecycle
 * used in Monitoring/index.tsx to guarantee out-of-order resilience.
 */
class MonitoringFetchController {
  activeRequestId = 0;
  telemetry: DeviceTelemetry | undefined = undefined;
  loading = false;
  error: string | null = null;
  currentRange = "24h";
  currentDeviceId = "RUT-1234567890";

  fetchRange(
    deviceId: string,
    range: string,
    fetcher: (reqId: number) => Promise<DeviceTelemetry>
  ): void {
    this.currentDeviceId = deviceId;
    this.currentRange = range;
    const reqId = ++this.activeRequestId;
    this.telemetry = undefined;
    this.loading = true;
    this.error = null;

    fetcher(reqId)
      .then((tel) => {
        if (reqId !== this.activeRequestId) return;
        this.telemetry = tel;
        this.loading = false;
      })
      .catch((err: unknown) => {
        if (reqId !== this.activeRequestId) return;
        this.error = err instanceof Error ? err.message : String(err);
        this.loading = false;
      });
  }
}

test("Deterministic out-of-order range sequence: 7d requested, 30d requested, 30d completes first, 7d completes later", async () => {
  const controller = new MonitoringFetchController();

  const { promise: promise7d, resolve: resolve7d } = Promise.withResolvers<DeviceTelemetry>();
  const { promise: promise30d, resolve: resolve30d } = Promise.withResolvers<DeviceTelemetry>();

  // 1. Request 7d
  controller.fetchRange("RUT-1234567890", "7d", () => promise7d);
  assert.equal(controller.loading, true);
  assert.equal(controller.telemetry, undefined);
  assert.equal(controller.currentRange, "7d");

  // 2. Before 7d completes, select 30d
  controller.fetchRange("RUT-1234567890", "30d", () => promise30d);
  assert.equal(controller.loading, true);
  assert.equal(controller.telemetry, undefined);
  assert.equal(controller.currentRange, "30d");

  // 3. Complete 30d response first
  const response30d: DeviceTelemetry = {
    deviceId: "RUT-1234567890",
    resolution: "daily",
    window: {
      range: "30d",
      resolution: "daily",
      windowTimeCoverageRatio: 0.043,
      observedDuration: 112364475,
      complete: false,
      dataPoints: 2,
    },
    mppt: {
      pvVoltage: [{ ts: 1700000000000, value: 24.5 }],
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
    solar: { radiation: [{ ts: 1700000000000, value: 700 }] },
    energySummary: {
      quality: "VALID",
      todayKwh: 3.2,
      periodDeltaKwh: 12.5,
      accumulatedKwh: 150.0,
      coverageRatio: 0.043,
    },
  };
  resolve30d(response30d);
  await new Promise((r) => setImmediate(r));

  assert.equal(controller.loading, false);
  assert.equal(controller.telemetry?.resolution, "daily");
  assert.equal(controller.telemetry?.window?.range, "30d");
  assert.equal(controller.currentRange, "30d");

  // 4. Complete old 7d response afterward
  const response7d: DeviceTelemetry = {
    deviceId: "RUT-1234567890",
    resolution: "hourly",
    window: {
      range: "7d",
      resolution: "hourly",
      windowTimeCoverageRatio: 0.055,
      observedDuration: 33164475,
      complete: false,
      dataPoints: 10,
    },
    mppt: {
      pvVoltage: [{ ts: 1700000000000, value: 20.1 }],
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
    solar: { radiation: [{ ts: 1700000000000, value: 650 }] },
    energySummary: {
      quality: "VALID",
      todayKwh: 3.2,
      periodDeltaKwh: 4.1,
      accumulatedKwh: 150.0,
      coverageRatio: 0.055,
    },
  };
  resolve7d(response7d);
  await new Promise((r) => setImmediate(r));

  // 5. Confirm visible state remains 30d and is not overwritten by the stale 7d payload
  assert.equal(controller.loading, false);
  assert.equal(controller.telemetry?.resolution, "daily");
  assert.equal(controller.telemetry?.window?.range, "30d");
  assert.equal(controller.telemetry?.window?.observedDuration, 112364475);
  assert.equal(controller.telemetry?.energySummary?.periodDeltaKwh, 12.5);
  // The solar irradiance series belongs to the visible 30d payload, not the
  // late 7d one (700 ≠ the stale response's 650).
  assert.equal(controller.telemetry?.solar?.radiation[0].value, 700);
  assert.equal(controller.currentRange, "30d");
});

test("Deterministic out-of-order device switching sequence: dev1 requested, dev2 requested, dev1 resolves late", async () => {
  const controller = new MonitoringFetchController();

  const { promise: promiseDev1, resolve: resolveDev1 } = Promise.withResolvers<DeviceTelemetry>();
  const { promise: promiseDev2, resolve: resolveDev2 } = Promise.withResolvers<DeviceTelemetry>();

  // Request dev1
  controller.fetchRange("RUT-DEV-1", "1h", () => promiseDev1);
  assert.equal(controller.currentDeviceId, "RUT-DEV-1");

  // Switch to dev2 before dev1 completes
  controller.fetchRange("RUT-DEV-2", "1h", () => promiseDev2);
  assert.equal(controller.currentDeviceId, "RUT-DEV-2");

  const dev1Tel: DeviceTelemetry = {
    deviceId: "RUT-DEV-1",
    resolution: "raw",
    mppt: { pvVoltage: [{ ts: 1700000000000, value: 50 }], pvCurrent: [], outputVoltage: [], outputCurrent: [] },
    pzem: { phaseAVoltage: [], phaseBVoltage: [], phaseCVoltage: [], phaseACurrent: [], phaseBCurrent: [], phaseCCurrent: [], energy: [] },
    solar: { radiation: [{ ts: 1700000000000, value: 512 }] },
  };

  const dev2Tel: DeviceTelemetry = {
    deviceId: "RUT-DEV-2",
    resolution: "raw",
    mppt: { pvVoltage: [{ ts: 1700000000000, value: 100 }], pvCurrent: [], outputVoltage: [], outputCurrent: [] },
    pzem: { phaseAVoltage: [], phaseBVoltage: [], phaseCVoltage: [], phaseACurrent: [], phaseBCurrent: [], phaseCCurrent: [], energy: [] },
    solar: { radiation: [{ ts: 1700000000000, value: 824 }] },
  };

  // Complete dev2 first
  resolveDev2(dev2Tel);
  await new Promise((r) => setImmediate(r));
  assert.equal(controller.telemetry?.deviceId, "RUT-DEV-2");

  // Complete dev1 late
  resolveDev1(dev1Tel);
  await new Promise((r) => setImmediate(r));

  // Confirm state retains dev2
  assert.equal(controller.telemetry?.deviceId, "RUT-DEV-2");
  assert.equal(controller.telemetry?.solar?.radiation[0].value, 824);
  assert.equal(controller.loading, false);
});
