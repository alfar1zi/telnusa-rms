import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { getDevices, getAllAlarms } from "@/services/api";
import { formatRelative } from "@/utils/format";
import { siteLabel } from "@/lib/deviceSemantics";
import PageHeader from "@/components/layout/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import HealthBadge from "@/components/common/HealthBadge";
import LoadingState from "@/components/common/LoadingState";
import EmptyState from "@/components/common/EmptyState";
import ErrorState from "@/components/common/ErrorState";
import { useI18n } from "@/i18n/I18nContext";
import { loadDevicesState, describeFailure, type DevicesLoadState } from "./loadDevices";

const UNLOADED: DevicesLoadState = {
  devices: [], alarmCounts: {}, alarmState: "unavailable", failure: null,
};

export default function Devices() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [state, setState]     = useState<DevicesLoadState>(UNLOADED);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  // Loading always terminates: every exit path runs through finally.
  useEffect(() => {
    let cancelled = false;
    loadDevicesState(getDevices, getAllAlarms)
      .then((next) => { if (!cancelled) setState(next); })
      .catch((err: unknown) => {
        if (!cancelled) setState({ ...UNLOADED, failure: describeFailure(err) });
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [attempt]);

  const retry = () => { setLoading(true); setAttempt((n) => n + 1); };
  const { devices, alarmCounts, alarmState, failure } = state;

  const onlineCount = devices.filter((d) => d.status === "online").length;
  const degradedCount = devices.filter((d) => d.status === "degraded" || d.status === "stale" || d.status === "clock_skew" || d.status === "sensor_fault" || d.status === "partial").length;
  const offlineCount = devices.filter((d) => d.status === "offline").length;
  const totalActiveAlarms = Object.values(alarmCounts).reduce((acc, c) => acc + c, 0);

  const filteredDevices = devices.filter((d) => {
    if (statusFilter === "online") return d.status === "online";
    if (statusFilter === "degraded") return ["degraded", "stale", "clock_skew", "sensor_fault", "partial"].includes(d.status);
    if (statusFilter === "offline") return d.status === "offline";
    return true;
  });

  return (
    <div className="space-y-6">
      <PageHeader title={t("devices.title")} subtitle={t("devices.subtitle")} />

      {/* Fleet Overview KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card
          className={`cursor-pointer transition-colors ${statusFilter === "all" ? "border-primary bg-accent/40" : "hover:bg-accent/20"}`}
          onClick={() => setStatusFilter("all")}
        >
          <CardContent className="p-4 flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{t("devices.kpi.total")}</span>
            <span className="text-2xl font-bold tracking-tight tabular-nums">{devices.length}</span>
          </CardContent>
        </Card>
        <Card
          className={`cursor-pointer transition-colors ${statusFilter === "online" ? "border-primary bg-accent/40" : "hover:bg-accent/20"}`}
          onClick={() => setStatusFilter(statusFilter === "online" ? "all" : "online")}
        >
          <CardContent className="p-4 flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{t("devices.kpi.online")}</span>
            <span className={`text-2xl font-bold tracking-tight tabular-nums ${onlineCount > 0 ? "text-[var(--severity-low,currentColor)]" : ""}`}>{onlineCount}</span>
          </CardContent>
        </Card>
        <Card
          className={`cursor-pointer transition-colors ${statusFilter === "degraded" ? "border-primary bg-accent/40" : "hover:bg-accent/20"}`}
          onClick={() => setStatusFilter(statusFilter === "degraded" ? "all" : "degraded")}
        >
          <CardContent className="p-4 flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{t("devices.kpi.degraded")}</span>
            <span className={`text-2xl font-bold tracking-tight tabular-nums ${degradedCount > 0 ? "text-[var(--severity-warning,currentColor)]" : ""}`}>{degradedCount}</span>
          </CardContent>
        </Card>
        <Card
          className={`cursor-pointer transition-colors ${statusFilter === "offline" ? "border-primary bg-accent/40" : "hover:bg-accent/20"}`}
          onClick={() => setStatusFilter(statusFilter === "offline" ? "all" : "offline")}
        >
          <CardContent className="p-4 flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{t("devices.kpi.offline")}</span>
            <span className={`text-2xl font-bold tracking-tight tabular-nums ${offlineCount > 0 ? "text-destructive" : ""}`}>{offlineCount}</span>
          </CardContent>
        </Card>
        <Card
          className="cursor-pointer transition-colors hover:bg-accent/20"
          onClick={() => navigate("/alarms")}
        >
          <CardContent className="p-4 flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{t("devices.kpi.alarms")}</span>
            <span className={`text-2xl font-bold tracking-tight tabular-nums ${totalActiveAlarms > 0 ? "text-destructive" : ""}`}>
              {alarmState === "unavailable" ? "NO DATA" : totalActiveAlarms}
            </span>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <LoadingState />
          ) : failure ? (
            <div className="p-4">
              <ErrorState
                hint={t("common.contactAdmin")}
                title={t("devices.error.title")}
                message={failure.message}
                onRetry={retry}
              />
            </div>
          ) : devices.length === 0 ? (
            <EmptyState message={t("devices.empty")} />
          ) : (
            <>
              {alarmState === "unavailable" && (
                <div className="p-4 pb-0">
                  <ErrorState
                    hint={t("common.contactAdmin")}
                    title={t("devices.error.alarmsTitle")}
                    message={t("devices.error.alarmsMessage")}
                    onRetry={retry}
                  />
                </div>
              )}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[180px]">{t("devices.col.site")}</TableHead>
                    <TableHead className="w-[160px]">{t("devices.col.deviceId")}</TableHead>
                    <TableHead className="w-[140px]">{t("devices.col.model")}</TableHead>
                    <TableHead className="w-[120px]">{t("devices.col.status")}</TableHead>
                    <TableHead className="w-[120px]">{t("devices.col.health")}</TableHead>
                    <TableHead className="w-[120px]">{t("devices.col.network")}</TableHead>
                    <TableHead className="w-[140px]">{t("devices.col.lastSeen")}</TableHead>
                    <TableHead className="w-[90px]">{t("devices.col.alarms")}</TableHead>
                    <TableHead className="w-[200px] text-right">{t("devices.col.actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredDevices.map((d) => {
                    const count = alarmCounts[d.id];
                    return (
                      <TableRow key={d.id}>
                        <TableCell className="text-xs sm:text-sm font-medium">{siteLabel(d.siteName, d.siteId)}</TableCell>
                        <TableCell className="text-xs font-semibold">{d.id}</TableCell>
                        <TableCell className="text-xs sm:text-sm">{d.model}</TableCell>
                        <TableCell><StatusBadge status={d.status} /></TableCell>
                        <TableCell><HealthBadge health={d.health} /></TableCell>
                        <TableCell className="text-xs sm:text-sm">{d.network ?? "NO DATA"}</TableCell>
                        <TableCell className="text-xs tabular-nums text-muted-foreground">{formatRelative(d.lastSeen)}</TableCell>
                        <TableCell>
                          {count === undefined ? (
                            <span className="text-xs text-muted-foreground">UNKNOWN</span>
                          ) : (
                            <Badge
                              variant={count > 0 ? "destructive" : "secondary"}
                              className="cursor-pointer"
                              onClick={() => navigate(`/alarms?device=${encodeURIComponent(d.id)}`)}
                            >
                              {String(count)}
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <TextureButton variant="minimal" size="sm" onClick={() => navigate(`/monitoring?device=${encodeURIComponent(d.id)}`)}>
                              {t("devices.action.monitor")}
                            </TextureButton>
                            <TextureButton variant="secondary" size="sm" onClick={() => navigate(`/devices/${d.id}`)}>
                              {t("devices.action.details")}
                            </TextureButton>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
