import { translate } from "@/i18n/translate";
import { Badge } from "@/components/ui/badge";
import type { AlarmSeverity } from "@/types/alarm";

interface SeverityBadgeProps {
  severity: AlarmSeverity;
}

/** Dictionary keys, not copy. */
const SEVERITY_KEYS: Record<AlarmSeverity, string> = {
  disaster:    "badge.severity.disaster",
  high:        "badge.severity.high",
  average:     "badge.severity.average",
  warning:     "badge.severity.warning",
  information: "badge.severity.information",
};
export const SEVERITY_VAR: Record<AlarmSeverity, string> = {
  disaster:    "--severity-disaster",
  high:        "--severity-high",
  average:     "--severity-average",
  warning:     "--severity-warning",
  information: "--severity-info",
};

export default function SeverityBadge({ severity }: SeverityBadgeProps) {
  const cssVar = `var(${SEVERITY_VAR[severity]})`;
  return (
    <Badge
      variant="outline"
      style={{ color: cssVar, borderColor: cssVar }}
      className="text-[11px] font-semibold"
    >
      {translate(SEVERITY_KEYS[severity])}
    </Badge>
  );
}
