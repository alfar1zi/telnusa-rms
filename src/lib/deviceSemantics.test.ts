import assert from "node:assert/strict";
import test from "node:test";
import {
  siteLabel, connectivityLabel, healthSemantic, mqttStateLabel, deviceStatusLabel,
} from "./deviceSemantics.ts";
import type { ConnectivityState, HealthState } from "@/types/device.ts";

// The two colors that encode "broken". Any state meaning "we could not tell"
// must never resolve to either of them.
const FAILURE_COLORS = ["var(--status-offline)", "var(--status-critical)"];

// -------------------------------------------------------
test("site name wins when present", () => {
  assert.equal(siteLabel("Situs Jaya", "RUT-TEST-001"), "Situs Jaya");
});

test("falls back to site id when name is absent", () => {
  assert.equal(siteLabel(null, "RUT-TEST-001"), "RUT-TEST-001");
});

test("UNASSIGNED only when both are genuinely absent", () => {
  assert.equal(siteLabel(null, null), "UNASSIGNED");
  assert.equal(siteLabel(undefined as unknown as null, undefined as unknown as null), "UNASSIGNED");
});

test("empty-string name is not treated as known", () => {
  // A blank string is not an identity; the id is more informative.
  assert.equal(siteLabel("", "RUT-TEST-001"), "RUT-TEST-001");
});


test("all five connectivity states stay distinct", () => {
  const states: ConnectivityState[] = ["connected", "degraded", "disconnected", "unknown", "no_data"];
  const labels = states.map(connectivityLabel);
  assert.deepEqual(labels, ["Connected", "Degraded", "Disconnected", "Unknown", "No Data"]);
  assert.equal(new Set(labels).size, 5, "each connectivity state needs its own label");
});

test("unknown and no_data never render as Disconnected", () => {
  assert.notEqual(connectivityLabel("unknown"), "Disconnected");
  assert.notEqual(connectivityLabel("no_data"), "Disconnected");
});

test("an unrecognised backend value degrades to Unknown, not Disconnected", () => {
  const bogus = "teapot" as ConnectivityState;
  assert.equal(connectivityLabel(bogus), "Unknown");
});


test("all health states stay distinct", () => {
  const states: HealthState[] = ["healthy", "warning", "critical", "unknown", "no_data", "not_integrated"];
  const labels = states.map((s) => healthSemantic(s).label);
  assert.deepEqual(labels, ["HEALTHY", "WARNING", "CRITICAL", "UNKNOWN", "NO DATA", "NOT INTEGRATED"]);
});

test("unknown and no_data never receive failure/offline styling", () => {
  for (const state of ["unknown", "no_data"] as HealthState[]) {
    const { color } = healthSemantic(state);
    assert.ok(
      !FAILURE_COLORS.includes(color),
      `${state} must not use a failure color, got ${color}`,
    );
  }
});

test("healthy and warning do not look like failure either", () => {
  assert.ok(!FAILURE_COLORS.includes(healthSemantic("healthy").color));
  assert.ok(!FAILURE_COLORS.includes(healthSemantic("warning").color));
});

test("critical is the only state that uses a failure color", () => {
  const ALL_HEALTH_STATES: HealthState[] = ["healthy", "warning", "critical", "unknown", "no_data", "not_integrated"];
  const failureStates = ALL_HEALTH_STATES.filter((s) => FAILURE_COLORS.includes(healthSemantic(s).color));
  assert.deepEqual(failureStates, ["critical"]);
});

test("an unrecognised health value degrades to unknown styling", () => {
  assert.deepEqual(healthSemantic("nonsense" as HealthState), healthSemantic("unknown"));
});


test("a live broker session reports Connected", () => {
  assert.equal(mqttStateLabel("SUBSCRIBED"), "Connected");
  assert.equal(mqttStateLabel("CONNECTED"), "Connected");
});

test("every other collector state stays Unknown", () => {
  for (const s of ["DISCONNECTED", "CONNECTING", "ERROR", "NOT CONFIGURED"]) {
    assert.equal(mqttStateLabel(s), "Unknown", `${s} must not be reported as Connected`);
  }
});

test("an absent runtime state is UNKNOWN, never Connected", () => {
  assert.equal(mqttStateLabel(undefined), "UNKNOWN");
  assert.equal(mqttStateLabel(null), "UNKNOWN");
  // An unreachable status endpoint must not read as a healthy transport.
  assert.notEqual(mqttStateLabel(undefined), "Connected");
});

// ---------------------------------------------------------------- status
test("device status keeps NO DATA distinct from OFFLINE", () => {
  assert.equal(deviceStatusLabel("offline", "offline"), "OFFLINE");
  assert.equal(deviceStatusLabel(undefined, "no_data"), "NO DATA");
  assert.equal(deviceStatusLabel(undefined, "fresh"), "UNKNOWN");
});

test("NO DATA is never rendered as a zero", () => {
  assert.notEqual(deviceStatusLabel(undefined, "no_data"), "0");
  assert.notEqual(healthSemantic("no_data").label, "0");
  assert.notEqual(connectivityLabel("no_data"), "0");
});