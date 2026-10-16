import assert from "node:assert/strict";
import test from "node:test";
import { alarmBeepAllowed, flapSuppressed, isAudibleSeverity } from "./useAlarmSound.ts";

test("alarm sound: high and disaster are audible, every other severity is silent", () => {
  assert.equal(isAudibleSeverity("high"), true);
  assert.equal(isAudibleSeverity("disaster"), true);
  for (const silent of ["average", "warning", "information", "", "HIGH", undefined]) {
    assert.equal(isAudibleSeverity(silent), false, `${String(silent)} must not play`);
  }
});

test("alarm sound: a second beep inside 5000 ms is suppressed", () => {
  const first = 1_000;
  assert.equal(alarmBeepAllowed(first, first + 1), false);
  assert.equal(alarmBeepAllowed(first, first + 4_999), false);
  assert.equal(alarmBeepAllowed(first, first + 5_000), true);
});

test("alarm sound: the same alarm clearing and re-raising stays silent for 5 minutes", () => {
  const firstBeepAt = 1_000_000;
  assert.equal(flapSuppressed(undefined, firstBeepAt), false, "an unseen alarm is never suppressed");
  assert.equal(flapSuppressed(firstBeepAt, firstBeepAt + 1), true);
  assert.equal(flapSuppressed(firstBeepAt, firstBeepAt + 299_999), true, "inside the window it stays quiet");
  assert.equal(flapSuppressed(firstBeepAt, firstBeepAt + 300_000), false, "past the window it may sound again");
});
