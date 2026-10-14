import test from "node:test";
import assert from "node:assert/strict";
import {
  TIME_RANGES,
  filterByTimeRange,
  jakartaDayStartMs,
  tickFormatFor,
  resolveWindow,
} from "./timeRange.ts";

// Fixed epoch so every expectation below is literal, not relative to wall clock.
const NOW = 1_700_000_000_000;

const series = (startMs: number, count: number, stepMs: number) =>
  Array.from({ length: count }, (_, i) => ({ ts: startMs + i * stepMs, value: i }));

test("window end is the caller's authoritative time, never a sample timestamp", () => {
  const w = resolveWindow("1h", NOW);
  assert.equal(w.toMs, NOW, "end must be the supplied authoritative time");
  assert.equal(w.fromMs, NOW - 3_600_000, "start = end - selected range");
  assert.equal(w.rangeMs, TIME_RANGES["1h"].ms);
  assert.equal(w.rangeKey, "1h");
});

test("each range resolves to its own explicit bounds", () => {
  for (const key of ["15m", "1h", "6h", "24h"] as const) {
    const w = resolveWindow(key, NOW);
    assert.equal(w.toMs, NOW, `${key}: end anchored to now`);
    assert.equal(w.fromMs, NOW - TIME_RANGES[key].ms, `${key}: start derived from end`);
    assert.equal(w.toMs - w.fromMs, TIME_RANGES[key].ms, `${key}: window width`);
  }
});

test("arbitrary from/to is honoured verbatim by the filter", () => {
  const from = NOW - 45 * 60_000;
  const to = NOW - 5 * 60_000;
  const data = series(NOW - 60 * 60_000, 120, 60_000); // 2 hours, 1 min steps
  const kept = filterByTimeRange(data, to - from, to);
  assert.ok(kept.length > 0, "arbitrary window must not be empty");
  assert.ok(kept.every((p) => p.ts >= from && p.ts <= to), "no point outside the window");
  assert.equal(kept[0].ts >= from, true);
  assert.equal(kept[kept.length - 1].ts <= to, true);
});

test("window is inclusive on both bounds", () => {
  const data = [{ ts: NOW - 60_000, value: 1 }, { ts: NOW - 30_000, value: 2 }];
  const kept = filterByTimeRange(data, 60_000, NOW);
  assert.deepEqual(kept.map((p) => p.ts), [NOW - 60_000, NOW - 30_000]);
});

test("empty result stays empty and does not throw", () => {
  assert.deepEqual(filterByTimeRange([], 3_600_000, NOW), []);
  const data = series(NOW - 5 * 60_000, 5, 20_000);
  assert.deepEqual(filterByTimeRange(data, 60_000, NOW), [], "window with no data yields no points");
});

test("stale last sample does not slide the window anchor", () => {
  // Feed stopped 6 hours ago. Anchoring on now must return nothing, whereas the
  // old last-sample anchor would have drawn a full-looking chart.
  const stale = series(NOW - 6 * 3_600_000, 30, 20_000);
  const kept = filterByTimeRange(stale, TIME_RANGES["1h"].ms, NOW);
  assert.equal(kept.length, 0, "a stale feed must not be presented as current");
});

test("legacy anchor is retained only when no end is supplied", () => {
  const stale = series(NOW - 6 * 3_600_000, 30, 20_000);
  const last = stale[stale.length - 1].ts;
  assert.equal(filterByTimeRange(stale, TIME_RANGES["1h"].ms).length, 30);
  assert.ok(last > 0);
});

test("out-of-window samples are dropped regardless of position", () => {
  const shuffled = [
    { ts: NOW - 20_000, value: 2 },
    { ts: NOW - 5 * 3_600_000, value: 0 }, // outside window, sits in the middle
    { ts: NOW - 200_000, value: 1 },
    { ts: NOW - 80_000, value: 3 },
  ];
  const kept = filterByTimeRange(shuffled, TIME_RANGES["1h"].ms, NOW);
  assert.deepEqual(
    kept.map((p) => p.ts),
    [NOW - 20_000, NOW - 200_000, NOW - 80_000],
    "filter keeps input order; chronological sorting is the chart's invariant",
  );
});

test("every range resolves to a closed window the backend can fetch in full", () => {
  for (const key of ["15m", "1h", "6h", "12h", "24h", "7d", "30d"] as const) {
    const w = resolveWindow(key, NOW);
    assert.equal(w.toMs, NOW, `${key}: end is the caller's time`);
    assert.equal(w.rangeMs, TIME_RANGES[key].ms, `${key}: span matches the selection`);
    assert.equal(w.fromMs, NOW - TIME_RANGES[key].ms, `${key}: start is span before the end`);
  }
});

test("axis tick format carries date context as the window widens", () => {
  assert.equal(tickFormatFor(TIME_RANGES["15m"].ms), "HH:mm", "15m cannot cross midnight");
  assert.equal(tickFormatFor(TIME_RANGES["1h"].ms), "dd MMM HH:mm", "1h can cross midnight");
  assert.equal(tickFormatFor(TIME_RANGES["6h"].ms), "dd MMM HH:mm", "6h can cross midnight");
  assert.equal(tickFormatFor(TIME_RANGES["24h"].ms), "dd MMM HH:mm", "24h always spans days");
  assert.equal(tickFormatFor(TIME_RANGES["7d"].ms), "dd MMM", "7d: time of day is noise");
  assert.equal(tickFormatFor(TIME_RANGES["30d"].ms), "dd MMM", "30d: time of day is noise");
});

test("no sub-day window is formatted as a bare date", () => {
  // "dd MMM" on a 1h window repeats one identical label on every tick.
  for (const key of ["15m", "1h", "6h", "12h"] as const) {
    assert.notEqual(tickFormatFor(TIME_RANGES[key].ms), "dd MMM");
  }
});

test("every window that can cross midnight keeps a date on the axis", () => {
  for (const key of ["1h", "6h", "12h", "24h"] as const) {
    assert.equal(tickFormatFor(TIME_RANGES[key].ms), "dd MMM HH:mm");
  }
});

test("report day input means Jakarta midnight, not UTC midnight", () => {
  // 2026-10-07 00:00 WIB = 2026-10-06 17:00Z.
  assert.equal(jakartaDayStartMs("2026-10-07"), Date.UTC(2026, 9, 6, 17, 0, 0));
});
