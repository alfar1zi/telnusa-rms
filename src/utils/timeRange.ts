export type TimeRangeKey = "15m" | "1h" | "6h" | "12h" | "24h" | "7d" | "30d";

export interface TimeRange {
  // Dictionary key, not copy: the text lives in src/i18n (en/id).
  labelKey: string;
  ms: number;
  unit: "minute" | "hour" | "day";
  minUnit: "minute" | "hour" | "day";
}

export const TIME_RANGES: Record<TimeRangeKey, TimeRange> = {
  "15m": { labelKey: "range.15m",   ms: 15 * 60_000,     unit: "minute", minUnit: "minute" },
  "1h":  { labelKey: "range.1h",   ms: 60 * 60_000,     unit: "minute", minUnit: "minute" },
  "6h":  { labelKey: "range.6h",  ms: 6 * 3_600_000,   unit: "hour",   minUnit: "minute" },
  "12h": { labelKey: "range.12h", ms: 12 * 3_600_000,  unit: "hour",   minUnit: "hour"   },
  "24h": { labelKey: "range.24h", ms: 24 * 3_600_000,  unit: "hour",   minUnit: "hour"   },
  "7d":  { labelKey: "range.7d",   ms: 7 * 86_400_000,  unit: "day",    minUnit: "hour"   },
  "30d": { labelKey: "range.30d",  ms: 30 * 86_400_000, unit: "day",    minUnit: "day"    },
};

// RUT906 Data Sender baseline cadence for the 11 approved telemetry items.
export const SAMPLE_INTERVAL_MS = 20_000;

export interface TimeWindow {
  rangeKey:  TimeRangeKey;
  fromMs:    number;  // explicit requested start
  toMs:      number;  // explicit requested end (authoritative, NOT last sample)
  rangeMs:   number;
}

/**
 * Resolve a "last N" selection into an explicit, closed window.
 *
 * end   = the authoritative time supplied by the caller (server/now), never
 *         a sample timestamp, so a stale feed cannot silently slide the window.
 * start = end - rangeMs
 *
 * The window alone decides how much data comes back: the backend fetches it in
 * full and its response reports what the window actually held (`window`), which
 * is why no point count is predicted or requested here.
 */
export function resolveWindow(rangeKey: TimeRangeKey, nowMs: number): TimeWindow {
  const rangeMs = TIME_RANGES[rangeKey].ms;
  const toMs = nowMs;
  const fromMs = toMs - rangeMs;

  return {
    rangeKey,
    fromMs,
    toMs,
    rangeMs,
  };
}

export function formatDurationMs(ms: number): string {
  if (ms <= 0) return "0m";
  const totalSec = Math.floor(ms / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const mins = Math.floor((totalSec % 3600) / 60);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  return `${mins}m`;
}

/**
 * Filter a series to the closed window ending at `endMs`.
 *
 * `endMs` must be an authoritative requested end. When omitted the function
 * falls back to the last sample timestamp; that legacy anchor is what made a
 * stale feed look live (MON-03), so only pass it for consumers that have not
 * yet adopted explicit windows (DeviceDetails health series).
 */
export function filterByTimeRange(
  data: { ts: number; value: number }[],
  rangeMs: number,
  endMs?: number,
): { ts: number; value: number }[] {
  if (data.length === 0) return [];
  const end = endMs ?? data[data.length - 1]?.ts ?? 0;
  const start = end - rangeMs;
  return data.filter((d) => d.ts >= start && d.ts <= end);
}

/**
 * A bare "dd MMM" collapses a sub-day window onto one indistinguishable label, and
 * "HH:mm" alone is wrong for every window that can cross midnight -- which is every
 * window of an hour or more. Only the 15 minute window drops the date.
 */
export function tickFormatFor(spanMs: number): "HH:mm" | "dd MMM HH:mm" | "dd MMM" {
  if (spanMs <= 15 * 60_000) return "HH:mm";
  if (spanMs <= 3 * 86_400_000) return "dd MMM HH:mm";
  return "dd MMM";
}

/**
 * RC-5: a `<input type="date">` value ("2026-10-07") parsed as `new Date(s)`
 * is UTC midnight, 7h before the Jakarta midnight the operator means.
 * Interpret the calendar day in Asia/Jakarta instead.
 */
export const REPORT_TZ = "Asia/Jakarta";

export function jakartaDayStartMs(dateStr: string): number {
  const [y, m, d] = dateStr.split("-").map(Number);
  // UTC instant of Jakarta midnight = 17:00Z of the previous day (WIB = UTC+7,
  // no DST). Date.UTC avoids re-introducing the local-zone dependence.
  return Date.UTC(y, m - 1, d) - 7 * 3_600_000;
}
