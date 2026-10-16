import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { getAlarms, getAlarmHistory, getAlarmRules, acknowledgeAlarm, resolveAlarm, getDevices } from "@/services/api";
import type { Alarm, AlarmSeverity, AlarmRule } from "@/types/alarm";
import type { Device } from "@/types/device";
import { siteLabel } from "@/lib/deviceSemantics";
import { useI18n } from "@/i18n/I18nContext";
import { useAlarmSound } from "@/hooks/useAlarmSound";
import { Bell } from "lucide-react";
import PageHeader from "@/components/layout/PageHeader";
import AlarmTable from "@/components/alarms/AlarmTable";
import ErrorState from "@/components/common/ErrorState";

/** Dictionary keys, not copy: the same words the severity badge renders. */
const SEVERITY_KEYS: Record<AlarmSeverity, string> = {
  disaster:    "badge.severity.disaster",
  high:        "badge.severity.high",
  average:     "badge.severity.average",
  warning:     "badge.severity.warning",
  information: "badge.severity.information",
};

const SEVERITIES: AlarmSeverity[] = ["disaster", "high", "average", "warning", "information"];

export default function Alarms() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const alarmSound = useAlarmSound();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialDevice = searchParams.get("device") || searchParams.get("deviceId") || "all";

  const [tab, setTab] = useState<"active" | "history" | "rules">("active");
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [historyAlarms, setHistoryAlarms] = useState<Alarm[]>([]);
  const [rules, setRules] = useState<AlarmRule[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<string>(initialDevice);
  const [severityFilter, setSeverityFilter] = useState<"all" | AlarmSeverity>("all");
  const [searchFilter, setSearchFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [actionAlarm, setActionAlarm] = useState<{ alarm: Alarm; action: "ack" | "resolve" } | null>(null);
  const [actionInput, setActionInput] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const loadData = () => {
    setLoading(true);
    const devParam = selectedDevice !== "all" ? selectedDevice : undefined;
    Promise.all([
      getAlarms({ deviceId: devParam, state: "active" }),
      getAlarmHistory({ deviceId: devParam, limit: 100 }),
      getAlarmRules(),
      getDevices(),
    ])
      .then(([activeAlms, histAlms, rls, devs]) => {
        setAlarms(activeAlms);
        setHistoryAlarms(histAlms);
        setRules(rls);
        setDevices(devs);
        setError(null);
      })
      .catch((err: Error) => {
        setError(err);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    loadData();
  }, [selectedDevice]);

  const handleDeviceChange = (devId: string) => {
    setSelectedDevice(devId);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (devId === "all") next.delete("device");
      else next.set("device", devId);
      return next;
    });
  };

  const handleConfirmAction = async () => {
    if (!actionAlarm) return;
    setActionLoading(true);
    try {
      if (actionAlarm.action === "ack") {
        await acknowledgeAlarm(actionAlarm.alarm.id, actionInput);
      } else {
        await resolveAlarm(actionAlarm.alarm.id, actionInput);
      }
      setActionAlarm(null);
      setActionInput("");
      loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("alarms.actionFailed"));
    } finally {
      setActionLoading(false);
    }
  };

  const filterAlarms = (list: Alarm[]) => {
    return list.filter((a) => {
      if (severityFilter !== "all" && a.severity !== severityFilter) return false;
      if (searchFilter.trim()) {
        const q = searchFilter.trim().toLowerCase();
        const matchProblem = a.problem.toLowerCase().includes(q);
        const matchDevice = a.deviceId.toLowerCase().includes(q);
        const matchEvidence = a.evidence ? a.evidence.toLowerCase().includes(q) : false;
        if (!matchProblem && !matchDevice && !matchEvidence) return false;
      }
      return true;
    });
  };

  const displayedActive = filterAlarms(alarms);
  const displayedHistory = filterAlarms(historyAlarms);
  // "no match" is only honest when a filter is actually narrowing the list
  const filtersActive = severityFilter !== "all" || searchFilter.trim() !== "" || selectedDevice !== "all";
  if (error) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("alarms.title")}
          subtitle={t("alarms.subtitle")}
        />
        <ErrorState
                hint={t("common.contactAdmin")}
          title={t("alarms.unavailable")}
          message={error.message}
          onRetry={loadData}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("alarms.title")}
        subtitle={t("alarms.subtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {tab !== "rules" && (
              <Input
                placeholder={t("alarms.filterPlaceholder")}
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                className="w-[180px] sm:w-[220px] bg-background text-xs h-8"
              />
            )}
            <Select value={selectedDevice} onValueChange={handleDeviceChange}>
              <SelectTrigger className="w-[160px] sm:w-[180px] bg-background text-xs h-8">
                <SelectValue placeholder={t("alarms.allDevices")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("alarms.allGateways")}</SelectItem>
                {devices.map((d) => (
                  <SelectItem key={d.id} value={d.id} className="text-xs">
                    {siteLabel(d.siteName, d.siteId)} <span className="text-muted-foreground tabular-nums">{d.id}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {tab !== "rules" && (
              <Select value={severityFilter} onValueChange={(v) => setSeverityFilter(v as "all" | AlarmSeverity)}>
                <SelectTrigger className="w-[140px] sm:w-[160px] bg-background text-xs h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("alarms.allSeverities")}</SelectItem>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>{t(SEVERITY_KEYS[s])}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            <TextureButton
              variant="secondary"
              size="sm"
              className="h-8"
              aria-pressed={alarmSound.enabled}
              onClick={() => alarmSound.setEnabled(!alarmSound.enabled)}
            >
              <Bell className="size-3.5" aria-hidden="true" />
              {alarmSound.enabled ? t("alarms.sound.on") : t("alarms.sound.off")}
            </TextureButton>
          </div>
        }
      />

      {/* Navigation Tabs and Severity Summary */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <div className="flex items-center gap-2">
          <TextureButton
            variant={tab === "active" ? "primary" : "minimal"}
            size="sm"
            onClick={() => setTab("active")}
            className="h-8"
          >
            {t("alarms.tab.active", { count: alarms.length })}
          </TextureButton>
          <TextureButton
            variant={tab === "history" ? "primary" : "minimal"}
            size="sm"
            onClick={() => setTab("history")}
            className="h-8"
          >
            {t("alarms.tab.history", { count: historyAlarms.length })}
          </TextureButton>
          <TextureButton
            variant={tab === "rules" ? "primary" : "minimal"}
            size="sm"
            onClick={() => setTab("rules")}
            className="h-8"
          >
            {t("alarms.tab.rules", { count: rules.length })}
          </TextureButton>
        </div>

        {tab !== "rules" && (
          <div className="flex flex-wrap items-center gap-1.5">
            {SEVERITIES.map((s) => {
              const count = (tab === "active" ? alarms : historyAlarms).filter((a) => a.severity === s).length;
              if (count === 0) return null;
              return (
                <Badge
                  key={s}
                  variant="outline"
                  style={{ color: `var(--severity-${s === "information" ? "info" : s})`, borderColor: `var(--severity-${s === "information" ? "info" : s})` }}
                  className="text-[10px] font-semibold"
                >
                  {t(SEVERITY_KEYS[s])}: {count}
                </Badge>
              );
            })}
          </div>
        )}
      </div>

      {/* Tab Contents */}
      {tab === "active" && (
        <Card>
          <CardContent className="p-0">
            <AlarmTable
              alarms={displayedActive}
              loading={loading}
              showSite
              emptyMessage={filtersActive ? t("alarms.empty.noMatch") : t("alarms.empty.none")}
              onDeviceClick={(id) => navigate(`/devices/${id}`)}
              onAcknowledge={(al) => {
                setActionAlarm({ alarm: al, action: "ack" });
                setActionInput("");
              }}
              onResolve={(al) => {
                setActionAlarm({ alarm: al, action: "resolve" });
                setActionInput("");
              }}
            />
          </CardContent>
        </Card>
      )}

      {tab === "history" && (
        <Card>
          <CardContent className="p-0">
            <AlarmTable
              alarms={displayedHistory}
              loading={loading}
              showSite
              emptyMessage={filtersActive ? t("alarms.empty.noMatch") : t("alarms.empty.history")}
              onDeviceClick={(id) => navigate(`/devices/${id}`)}
            />
          </CardContent>
        </Card>
      )}

      {tab === "rules" && (
        <Card>
          <CardContent className="p-0">
            <div className="divide-y divide-border">
              {rules.map((r) => (
                <div key={r.rule_id} className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">{r.name}</span>
                      <Badge variant={r.enabled ? "default" : "secondary"} className="text-[10px] ">
                        {r.enabled ? t("alarms.rules.enabled") : t("alarms.rules.disabled")}
                      </Badge>
                      <Badge variant="outline" className="text-[10px] uppercase">
                        {r.severity}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {t("alarms.rules.summary", {
                        id: r.rule_id,
                        metric: r.metric,
                        operator: r.operator,
                        threshold: r.threshold ?? t("alarms.rules.noThreshold"),
                        pattern: r.device_pattern,
                      })}
                    </div>
                    {r.description && (
                      <div className="text-xs text-muted-foreground">{r.description}</div>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground text-right shrink-0">
                    {t("alarms.rules.schedule", { interval: r.evaluation_interval, consecutive: r.consecutive_count })}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Modal Dialog for Ack / Resolve */}
      {actionAlarm && (
        <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-xs flex items-center justify-center p-4">
          <Card className="max-w-md w-full shadow-lg border-border">
            <CardContent className="p-5 space-y-4">
              <div>
                <CardTitle className="text-base">
                  {actionAlarm.action === "ack" ? t("alarms.dialog.ackTitle") : t("alarms.dialog.resolveTitle")}
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-1">
                  {t("alarms.dialog.subject", {
                    problem: actionAlarm.alarm.problem,
                    deviceId: actionAlarm.alarm.deviceId,
                  })}
                </p>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  {actionAlarm.action === "ack" ? t("alarms.dialog.ackNoteLabel") : t("alarms.dialog.resolveReasonLabel")}
                </label>
                <Input
                  placeholder={actionAlarm.action === "ack" ? t("alarms.dialog.ackNotePlaceholder") : t("alarms.dialog.resolveReasonPlaceholder")}
                  value={actionInput}
                  onChange={(e) => setActionInput(e.target.value)}
                  className="text-xs"
                  autoFocus
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <TextureButton
                  variant="minimal"
                  size="sm"
                  onClick={() => setActionAlarm(null)}
                  disabled={actionLoading}
                >
                  {t("common.cancel")}
                </TextureButton>
                <TextureButton
                  variant={actionAlarm.action === "ack" ? "accent" : "primary"}
                  size="sm"
                  onClick={handleConfirmAction}
                  disabled={actionLoading}
                >
                  {actionLoading
                    ? t("alarms.dialog.processing")
                    : actionAlarm.action === "ack"
                      ? t("alarms.dialog.confirmAck")
                      : t("alarms.dialog.confirmResolve")}
                </TextureButton>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
