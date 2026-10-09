import { translate } from "@/i18n/translate";
import type { SensorState } from "@/types/device";

// Labels are dictionary keys; the tone stays here.
const STATE_MAP: Record<SensorState, { labelKey: string; color?: string }> = {
  all_ok:         { labelKey: "badge.sensor.allOk",         color: "var(--muted-foreground)" },
  ok:             { labelKey: "badge.sensor.ok",            color: "var(--muted-foreground)" },
  partial:        { labelKey: "badge.sensor.partial",       color: "var(--chart-1)" },
  sensor_fault:   { labelKey: "badge.sensor.sensorFault",   color: "var(--destructive)" },
  warning:        { labelKey: "badge.sensor.warning",       color: "var(--chart-1)" },
  no_data:        { labelKey: "badge.sensor.noData",        color: "var(--muted-foreground)" },
  not_installed:  { labelKey: "badge.sensor.notInstalled",  color: "var(--muted-foreground)" },
  not_integrated: { labelKey: "badge.sensor.notIntegrated", color: "var(--muted-foreground)" },
};

export default function SensorStateBadge({
  state,
}: {
  state: SensorState;
}) {
  // A healthy sensor still states its state: a blank cell reads as missing data
  // in an operations table, and the label is what makes the other states legible.

  const config = STATE_MAP[state];
  const label = config ? translate(config.labelKey) : String(state).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const color = config?.color ?? "var(--muted-foreground)";

  return (
    <span
      style={{ color }}
      className="text-xs font-medium"
    >
      {label}
    </span>
  );
}
