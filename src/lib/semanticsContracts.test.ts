/**
 * Contracts for event, health and metric resolution.
 *
 * These test the pure resolution functions, because they are the single place
 * where "do we have data / is this wired up / what is this event" is decided.
 * A blank label or a wrong "no data" string is a consumer-visible defect, so
 * each case is pinned to the exact rendered text.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalEventType,
  eventTypeLabel,
  eventMessage,
} from "./eventSemantics.ts";
import {
  HEALTH_STATE_LABEL,
  METRIC_STATE_LABEL,
  healthSourceState,
  healthStateLabel,
  metricEmptyMessage,
  metricState,
  metricStateLabel,
} from "./telemetrySemantics.ts";

// canonical event type

test(" every canonical type has a non-blank label", () => {
  const types = [
    "alarm_created", "alarm_resolved", "status_change",
    "config_change", "reboot", "unknown",
  ] as const;
  for (const t of types) {
    assert.equal(canonicalEventType(t), t, `${t} must survive canonicalisation`);
    const label = eventTypeLabel(t);
    assert.ok(label.length > 0, `${t} rendered a blank label`);
    assert.equal(label.trim(), label);
  }
});

test(" legacy wire names translate, 'system' never claims a recovery", () => {
  assert.equal(canonicalEventType("alarm"), "alarm_created");
  assert.equal(eventTypeLabel("alarm"), "Alarm Created");
  // "system" was the everything-else bucket; asserting a recovery would
  // fabricate a fact the payload never carried.
  assert.equal(canonicalEventType("system"), "unknown");
  assert.notEqual(eventTypeLabel("system"), "Alarm Resolved");
});

test(" raw Zabbix trigger values classify", () => {
  assert.equal(canonicalEventType("1"), "alarm_created");
  assert.equal(canonicalEventType("0"), "alarm_resolved");
});

test(" malformed / missing / hostile input still yields a label", () => {
  const bad: unknown[] = [
    "", "   ", "  alarm  ", "ALARM", "reboot_now", "2", "0; DROP TABLE",
    null, undefined, 0, 1, 7, true, false, [], {}, ["alarm"], { type: "alarm" },
    Symbol("alarm"), () => "alarm",
  ];
  for (const v of bad) {
    const label = eventTypeLabel(v);
    assert.ok(label.length > 0, `${String(v)} rendered a blank label`);
    assert.equal(typeof label, "string");
  }
  // Whitespace-padded canonical names still resolve.
  assert.equal(canonicalEventType("  alarm_created  "), "alarm_created");
  // Case is not silently accepted: an upper-cased token is not a known type.
  assert.equal(canonicalEventType("ALARM"), "unknown");
});

test(" an event with no name never renders a blank message cell", () => {
  assert.equal(eventMessage("High load"), "High load");
  assert.equal(eventMessage("   "), "Unnamed event");
  for (const v of [null, undefined, "", 0, [], {}] as unknown[]) {
    assert.equal(eventMessage(v), "Unnamed event");
    assert.ok(eventMessage(v).length > 0);
  }
});

// metric states: the four distinct facts

test("all four metric states exist and are labelled", () => {
  const states = ["not_integrated", "no_data_in_range", "data", "error"] as const;
  for (const s of states) {
    // The map holds dictionary keys (one label set, translated at render time);
    // the label function is what the console displays.
    assert.ok(METRIC_STATE_LABEL[s].length > 0);
    assert.ok(metricStateLabel(s).length > 0);
    assert.notEqual(metricStateLabel(s), METRIC_STATE_LABEL[s], "the label is translated, not the key");
  }
  // The four must be four distinct strings: a copy/paste collision would
  // collapse two different facts into one.
  assert.equal(new Set(states.map((s) => metricStateLabel(s))).size, 4);
});

test("backend metricState is the authority when present", () => {
  const states = {
    "mppt.pvVoltage": "data",
    "pzem.energy": "no_data_in_range",
    "pzem.phaseAVoltage": "not_integrated",
  } as const;
  // A declared state wins even when the point count contradicts it:
  // the server saw the item, the client only sees a filtered window.
  assert.equal(metricState(states, "mppt.pvVoltage", 0), "data");
  assert.equal(metricState(states, "pzem.energy", 99), "no_data_in_range");
  assert.equal(metricState(states, "pzem.phaseAVoltage", 0), "not_integrated");
});

test("absent metricState map degrades on series length, never to not_integrated", () => {
  // Claiming not_integrated from an empty array would fabricate a wiring fact.
  assert.equal(metricState(undefined, "pzem.energy", 0), "no_data_in_range");
  assert.equal(metricState(undefined, "mppt.pvVoltage", 12), "data");
  assert.equal(metricState({}, "pzem.energy", 0), "no_data_in_range");
  assert.equal(metricState({}, "pzem.energy", 5), "data");
});

test("an unintegrated metric never says 'no data for selected time range'", () => {
  const generic = /no data for selected time range/i;
  assert.equal(metricEmptyMessage("not_integrated"), "This metric has no data source configured.");
  assert.ok(!generic.test(metricEmptyMessage("not_integrated")),
    "an unintegrated metric must not reuse the generic no-data wording");
  // The generic wording is only ever produced for the genuinely-empty case.
  assert.ok(!generic.test(metricEmptyMessage("no_data_in_range")),
    "message must use the explicit state wording, not the old chart string");
  assert.equal(metricEmptyMessage("error"), "This metric could not be read from its source.");
  assert.equal(metricEmptyMessage("data"), "Data is available.");
  for (const s of ["not_integrated", "no_data_in_range", "data", "error"] as const) {
    assert.ok(metricEmptyMessage(s).length > 0);
  }
});

// source state

test(" a declared state is returned verbatim", () => {
  for (const s of ["not_integrated", "available", "no_data", "error"] as const) {
    assert.equal(healthSourceState(s, [12, null]), s);
    assert.ok(HEALTH_STATE_LABEL[s].length > 0);
    assert.notEqual(healthStateLabel(s), HEALTH_STATE_LABEL[s], "the label is translated, not the key");
  }
});

test(" all-null readings degrade to not_integrated, not no_data", () => {
  // Asserting "available" would imply a live source; asserting "no_data" would
  // imply a wired-up source that is merely quiet. Neither is evidenced.
  assert.equal(healthSourceState(undefined, [null, null, null]), "not_integrated");
  assert.equal(healthSourceState(undefined, []), "not_integrated");
  assert.equal(healthSourceState(undefined, [null, undefined, null]), "not_integrated");
});

test(" any real reading implies a live source", () => {
  assert.equal(healthSourceState(undefined, [null, 42]), "available");
  assert.equal(healthSourceState(undefined, [0]), "available");
  assert.equal(healthSourceState(undefined, [null, ""]), "available");
  assert.equal(healthSourceState(undefined, [null, false]), "available");
});

test(" no state label is blank", () => {
  for (const s of ["not_integrated", "available", "no_data", "error"] as const) {
    assert.ok(healthStateLabel(s).length > 0);
  }
  assert.equal(new Set(Object.values(HEALTH_STATE_LABEL)).size, 4);
});
