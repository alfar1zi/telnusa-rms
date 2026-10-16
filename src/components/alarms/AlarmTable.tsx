import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/i18n/I18nContext";
import { TextureButton } from "@/components/ui/texture-button";
import { Badge } from "@/components/ui/badge";
import type { Alarm } from "@/types/alarm";
import { formatTimestamp } from "@/utils/format";
import SeverityBadge from "@/components/common/SeverityBadge";
import LoadingState from "@/components/common/LoadingState";
import EmptyState from "@/components/common/EmptyState";
import { siteLabel } from "@/lib/deviceSemantics";

interface AlarmTableProps {
  // Scope-correct empty text: the dashboard shows one device's alarms, so the
  // generic "matching criteria" wording (written for the filtered table) would
  // describe filters that page does not have.
  emptyMessage?: string;
  alarms: Alarm[];
  loading?: boolean;
  showSite?: boolean;
  onDeviceClick?: (deviceId: string) => void;
  onAcknowledge?: (alarm: Alarm) => void;
  onResolve?: (alarm: Alarm) => void;
}

export default function AlarmTable({ emptyMessage,
  alarms,
  loading,
  showSite,
  onDeviceClick,
  onAcknowledge,
  onResolve,
}: AlarmTableProps) {
  const { t } = useI18n();
  if (loading) return <LoadingState />;
  if (alarms.length === 0) {
    return <EmptyState message={emptyMessage ?? t("alarms.empty.noMatch")} />;
  }
  const sorted = [...alarms].sort((a, b) => (b.lastTime || b.startTime) - (a.lastTime || a.startTime));
  const showDevice = showSite ?? false;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[160px]">{t("alarms.table.triggered")}</TableHead>
          {showDevice && <TableHead className="w-[140px]">{t("alarms.table.device")}</TableHead>}
          {showDevice && <TableHead className="w-[140px]">{t("alarms.table.site")}</TableHead>}
          <TableHead>{t("alarms.table.problem")}</TableHead>
          <TableHead className="w-[110px]">{t("alarms.table.severity")}</TableHead>
          <TableHead className="w-[100px]">{t("alarms.table.state")}</TableHead>
          {(onAcknowledge || onResolve) && <TableHead className="w-[140px] text-right">{t("alarms.table.actions")}</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((row) => {
          const siteLabelText = showDevice ? siteLabel(row.siteName, row.siteId) : undefined;
          const state = row.state || (row.status === "active" ? "OPEN" : "RESOLVED");
          const occurrences = row.consecutiveOccurrences ?? 1;

          return (
            <TableRow key={row.id}>
              <TableCell className="text-xs tabular-nums whitespace-nowrap text-muted-foreground">
                <div className="flex flex-col">
                  <span>{formatTimestamp(row.lastTime || row.startTime)}</span>
                  {row.lastTime && row.lastTime !== row.startTime && (
                    <span className="text-[10px] text-muted-foreground/70">
                      {t("alarms.table.firstSeen", { time: formatTimestamp(row.startTime) })}
                    </span>
                  )}
                </div>
              </TableCell>
              {showDevice && (
                <TableCell>
                  {onDeviceClick ? (
                    <TextureButton
                      variant="ghost"
                      className="h-auto p-0 flex-col items-start text-xs hover:underline"
                      onClick={() => onDeviceClick(row.deviceId)}
                    >
                      <span>{row.deviceId}</span>
                    </TextureButton>
                  ) : (
                    <div className="flex flex-col">
                      <span className="text-xs ">{row.deviceId}</span>
                    </div>
                  )}
                </TableCell>
              )}
              {showDevice && (
                <TableCell className="text-xs text-muted-foreground">
                  {siteLabelText ?? t("state.noData")}
                </TableCell>
              )}
              <TableCell className="text-xs sm:text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{row.problem}</span>
                  {occurrences > 1 && (
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4">
                      ×{occurrences}
                    </Badge>
                  )}
                </div>
                {row.evidence && (
                  <div className="text-[11px] text-muted-foreground mt-0.5 truncate max-w-[320px]">
                    {row.evidence}
                  </div>
                )}
                {state === "ACKNOWLEDGED" && row.acknowledgedBy && (
                  <div className="text-[10px] text-[var(--severity-warning,currentColor)] mt-0.5">
                    {row.ackNote
                      ? t("alarms.table.ackByNote", { operator: row.acknowledgedBy, note: row.ackNote })
                      : t("alarms.table.ackBy", { operator: row.acknowledgedBy })}
                  </div>
                )}
                {state === "RESOLVED" && row.resolvedBy && (
                  <div className="text-[10px] text-muted-foreground mt-0.5">
                    {row.resolutionReason
                      ? t("alarms.table.resolvedByNote", { operator: row.resolvedBy, note: row.resolutionReason })
                      : t("alarms.table.resolvedBy", { operator: row.resolvedBy })}
                  </div>
                )}
              </TableCell>
              <TableCell><SeverityBadge severity={row.severity} /></TableCell>
              <TableCell>
                {state === "OPEN" && (
                  <Badge variant="destructive" className="text-[10px]">
                    {t("alarms.state.open")}
                  </Badge>
                )}
                {state === "ACKNOWLEDGED" && (
                  <Badge variant="secondary" className="text-[10px] bg-[var(--severity-warning-bg,#fef3c7)] text-[var(--severity-warning,#b45309)] border-[var(--severity-warning,#f59e0b)]">
                    {t("alarms.state.acknowledged")}
                  </Badge>
                )}
                {state === "RESOLVED" && (
                  <Badge variant="secondary" className="text-[10px]">
                    {t("alarms.state.resolved")}
                  </Badge>
                )}
              </TableCell>
              {(onAcknowledge || onResolve) && (
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    {state === "OPEN" && onAcknowledge && (
                      <TextureButton
                        variant="secondary"
                        size="sm"
                        className="h-7 px-2"
                        onClick={() => onAcknowledge(row)}
                      >
                        {t("alarms.action.ack")}
                      </TextureButton>
                    )}
                    {state !== "RESOLVED" && onResolve && (
                      <TextureButton
                        variant="minimal"
                        size="sm"
                        className="h-7 px-2"
                        onClick={() => onResolve(row)}
                      >
                        {t("alarms.action.resolve")}
                      </TextureButton>
                    )}
                  </div>
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
