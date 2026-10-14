/**
 * Regression tests for the  state transition.
 *
 * Run with:  node --experimental-strip-types --test src/pages/Devices/loadDevices.test.ts
 *
 * These exercise the real module the page imports, with injected loaders, so a
 * regression in promise aggregation or in the dependency contract fails here.
 * Excluded from tsconfig.app.json (a Node test, not application code).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { loadDevicesState, describeFailure } from "./loadDevices.ts";
import type { Device } from "@/types/device";
import type { Alarm } from "@/types/alarm";

const RUT: Device = {
  id: "RUT-1234567890",
  name: "RUT-1234567890",
  model: "Teltonika RUT906",
  serial: "1234567890",
  siteId: "site-a",
  siteName: "Site A",
  status: "online",
  lastSeen: 1758604800000,
  network: "4G LTE",
  health: "unknown",
  connectivity: "connected",
  dataAge: "fresh",
  mpptState: "ok",
  pzemState: "ok",
  sensor3State: "not_installed",
};

const alarm = (deviceId: string, status: Alarm["status"]): Alarm => ({
  id: `${deviceId}-${status}`,
  deviceId,
  siteId: "site-a",
  siteName: "Site A",
  problem: "test",
  severity: "warning",
  status,
  startTime: 1758604800000,
});

const apiError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });
const okDevices = (v: Device[]) => () => Promise.resolve(v);
const okAlarms = (v: Alarm[]) => () => Promise.resolve(v);
const boom = (e: unknown) => () => Promise.reject(e);

test("A: both dependencies resolve -> devices and active counts present", async () => {
  const r = await loadDevicesState(
    okDevices([RUT]),
    okAlarms([alarm(RUT.id, "active"), alarm(RUT.id, "active"), alarm(RUT.id, "resolved")]),
  );
  assert.equal(r.failure, null);
  assert.equal(r.alarmState, "ok");
  assert.equal(r.devices.length, 1);
  assert.equal(r.alarmCounts[RUT.id], 2);
});

test("B: alarm dependency rejects -> devices still render, counts unknown not zero", async () => {
  const r = await loadDevicesState(okDevices([RUT]), boom(apiError(503)));
  assert.equal(r.failure, null, "a dependency outage must not become a page-level error");
  assert.equal(r.alarmState, "unavailable");
  assert.equal(r.devices.length, 1, "the real device list must survive the outage");
  assert.deepEqual(r.alarmCounts, {}, "counts must be absent so the UI shows UNKNOWN, not 0");
});

test("C: device dependency rejects -> page-level failure, no devices", async () => {
  const r = await loadDevicesState(boom(apiError(500)), okAlarms([]));
  assert.equal(r.devices.length, 0);
  assert.equal(r.alarmState, "unavailable");
  assert.equal(r.failure?.status, 500);
  assert.match(r.failure?.message ?? "", /Backend unavailable/);
});

test("D: rejection settles for every failure mode (loading can always terminate)", async () => {
  for (const err of [apiError(503), apiError(401), apiError(403), new TypeError("network")]) {
    const r = await loadDevicesState(boom(err), boom(err));
    assert.ok(r.failure, `expected a failure state for ${String(err)}`);
  }
});

test("E: 401 and 403 stay distinguishable from a 5xx outage", async () => {
  assert.match(describeFailure(apiError(401)).message, /Session expired/);
  assert.match(describeFailure(apiError(403)).message, /Not authorized/);
  assert.match(describeFailure(apiError(503)).message, /Backend unavailable/);
  assert.match(describeFailure(new TypeError("fetch failed")).message, /Network unreachable/);
  assert.equal(describeFailure(new TypeError("fetch failed")).status, null);
});

test("F: genuine empty registry is an empty success, distinct from a failure", async () => {
  const r = await loadDevicesState(okDevices([]), okAlarms([]));
  assert.equal(r.failure, null);
  assert.equal(r.alarmState, "ok");
  assert.equal(r.devices.length, 0);
});

test("G: a 200 that is not a list is a failure, never a thrown render-path error", async () => {
  await assert.rejects(
    () => loadDevicesState(okDevices({ devices: [] } as unknown as Device[]), okAlarms([])),
    /not a list/,
  );
  await assert.rejects(
    () => loadDevicesState(okDevices([RUT]), okAlarms({ alarms: [] } as unknown as Alarm[])),
    /not a list/,
  );
});

test("H: only active alarms are counted", async () => {
  const r = await loadDevicesState(
    okDevices([RUT]),
    okAlarms([alarm(RUT.id, "resolved"), alarm("other-device", "active")]),
  );
  assert.equal(r.alarmCounts[RUT.id], 0);
});