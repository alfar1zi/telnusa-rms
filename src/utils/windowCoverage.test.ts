import test from "node:test";
import assert from "node:assert/strict";
import { formatDurationMs } from "./timeRange.ts";

test("formatDurationMs formats minutes, hours, and days accurately", () => {
  assert.equal(formatDurationMs(0), "0m");
  assert.equal(formatDurationMs(45_000), "0m");
  assert.equal(formatDurationMs(60_000), "1m");
  assert.equal(formatDurationMs(96 * 60_000), "1h 36m");
  assert.equal(formatDurationMs(3600_000), "1h");
  assert.equal(formatDurationMs(6 * 3600_000), "6h");
  assert.equal(formatDurationMs(24 * 3600_000), "1d");
  assert.equal(formatDurationMs(28 * 3600_000), "1d 4h");
  assert.equal(formatDurationMs(7 * 86400_000), "7d");
  assert.equal(formatDurationMs(30 * 86400_000), "30d");
});

test("formatDurationMs handles partial hour intervals without zero minute display", () => {
  assert.equal(formatDurationMs(3600_000 + 120_000), "1h 2m");
});
