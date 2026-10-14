/**
 * Chart gap detection.
 *
 * A window with samples only in part of it is a fact about the device, not a
 * rendering bug, so the chart has to say which parts are empty instead of
 * looking half-drawn. Spans are derived from the plotted points and the
 * requested window, using the trace's own cadence as the threshold (a raw trace
 * has 20 s samples, an hourly one has 1 h buckets), so a normal cadence is never
 * mistaken for a gap.
 */

export interface NoDataSpan {
  fromMs: number;
  toMs: number;
}

export function detectNoDataSpans(
  points: ReadonlyArray<{ ts: number }>,
  window: { fromMs: number; toMs: number },
  minGapMs: number,
): NoDataSpan[] {
  if (!(window.toMs > window.fromMs)) return [];
  const stamps = points.map((p) => p.ts).filter((ts) => Number.isFinite(ts)).sort((a, b) => a - b);
  const edges = [window.fromMs, ...stamps, window.toMs];
  const spans: NoDataSpan[] = [];
  for (let i = 1; i < edges.length; i += 1) {
    const from = edges[i - 1];
    const to = edges[i];
    if (to - from > minGapMs) spans.push({ fromMs: from, toMs: to });
  }
  return spans;
}

/** Median sample spacing, used as the gap threshold when the caller has no idea. */
export function medianSpacing(points: ReadonlyArray<{ ts: number }>, fallbackMs: number): number {
  const stamps = points.map((p) => p.ts).sort((a, b) => a - b);
  if (stamps.length < 3) return fallbackMs;
  const deltas: number[] = [];
  for (let i = 1; i < stamps.length; i += 1) deltas.push(stamps[i] - stamps[i - 1]);
  deltas.sort((a, b) => a - b);
  const median = deltas[Math.floor(deltas.length / 2)];
  return median > 0 ? median : fallbackMs;
}
