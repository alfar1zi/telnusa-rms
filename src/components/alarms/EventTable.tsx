import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/i18n/I18nContext";
import type { DeviceEvent } from "@/types/event";
import { eventMessage, eventTypeLabel } from "@/lib/eventSemantics";
import { canonicalEventType } from "@/lib/eventSemantics";
import { formatTimestamp } from "@/utils/format";
import LoadingState from "@/components/common/LoadingState";
import EmptyState from "@/components/common/EmptyState";

interface EventTableProps {
  events: DeviceEvent[];
  loading?: boolean;
}

// One hue per event family, matching the severity vocabulary used elsewhere:
// a problem is warm, a recovery is green, a state or config change is neutral.
const EVENT_DOT: Record<string, string> = {
  alarm_created: "bg-[var(--severity-high)]",
  alarm_resolved: "bg-[var(--status-online)]",
  status_change: "bg-muted-foreground",
  config_change: "bg-muted-foreground",
  reboot: "bg-[var(--chart-1)]",
  unknown: "bg-muted-foreground",
};

export default function EventTable({ events, loading }: EventTableProps) {
  const { t } = useI18n();
  if (loading) return <LoadingState />;
  if (events.length === 0) {
    return <EmptyState message={t("alarms.events.empty")} />;
  }

  const sorted = [...events].sort((a, b) => b.timestamp - a.timestamp);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[180px]">{t("alarms.events.time")}</TableHead>
          <TableHead className="w-[160px]">{t("alarms.events.type")}</TableHead>
          <TableHead>{t("alarms.events.message")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((row) => (
          <TableRow key={row.id}>
            <TableCell className="text-xs tabular-nums whitespace-nowrap text-muted-foreground">
              {formatTimestamp(row.timestamp)}
            </TableCell>
            <TableCell className="text-xs font-medium text-foreground">
              <span className="flex items-center gap-1.5">
                <span
                  className={`size-1.5 shrink-0 rounded-full ${EVENT_DOT[canonicalEventType(row.type)] ?? EVENT_DOT.unknown}`}
                  aria-hidden="true"
                />
                {eventTypeLabel(row.type)}
              </span>
            </TableCell>
            <TableCell className="text-xs sm:text-sm text-foreground">{eventMessage(row.message)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
