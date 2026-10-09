import { translate } from "@/i18n/translate";
// RC-3: accept any string; the server may send states newer than this build
// (e.g. clock-skew/degraded). Unknown values render as UNKNOWN, never crash.
// Labels are dictionary keys; the tone stays here.
const STATUS_CONFIG: Record<string, { labelKey: string; color: string }> = {
  online:         { labelKey: "badge.status.online",       color: "var(--foreground)" },
  offline:        { labelKey: "badge.status.offline",      color: "var(--destructive)" },
  stale:          { labelKey: "badge.status.stale",        color: "var(--chart-1)" },
  clock_skew:     { labelKey: "badge.status.clockSkew",    color: "var(--chart-1)" },
  sensor_fault:   { labelKey: "badge.status.sensorFault",  color: "var(--destructive)" },
  partial:        { labelKey: "badge.status.partial",      color: "var(--chart-1)" },
  degraded:       { labelKey: "badge.status.degraded",     color: "var(--chart-1)" },
  no_data:        { labelKey: "badge.status.noData",       color: "var(--muted-foreground)" },
  unknown:        { labelKey: "badge.status.unknown",      color: "var(--muted-foreground)" },
  all_ok:         { labelKey: "badge.status.allOk",        color: "var(--foreground)" },
  not_integrated: { labelKey: "badge.status.notConnected", color: "var(--muted-foreground)" },
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export default function StatusBadge({ status, className }: StatusBadgeProps) {
  const key = Object.prototype.hasOwnProperty.call(STATUS_CONFIG, status) ? status : "unknown";
  const { labelKey, color } = STATUS_CONFIG[key] ?? STATUS_CONFIG.unknown;
  const label = translate(labelKey);
  return (
    <span
      style={{ color }}
      className={`text-xs font-medium ${className || ""}`}
    >
      {label}
    </span>
  );
}
