import { format, formatDistanceToNow } from "date-fns";
import { toZonedTime } from "date-fns-tz";

export const TZ = "Asia/Jakarta";

export function formatTimestamp(ms: number | null, fmt = "dd MMM yyyy HH:mm:ss"): string {
  if (ms === null) return "UNKNOWN";
  return format(toZonedTime(ms, TZ), fmt);
}
export function formatRelative(ms: number | null): string {
  if (ms === null) return "UNKNOWN";
  return formatDistanceToNow(ms, { addSuffix: true });
}
export function formatValue(value: number | null, unit: string, decimals = 1): string {
  if (value === null) return "NO DATA";
  return `${value.toFixed(decimals)} ${unit}`;
}
