import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { TextureButton } from "@/components/ui/texture-button";
import {
  getDevices, getAllAlarms, getDeviceLatestTelemetry,
  getDeviceEvents, getDeviceHealthSnapshot, getIntegrationStatus,
  type IntegrationStatus,
} from "@/services/api";
import PageHeader from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDeviceLive } from "@/hooks/useDeviceLive";
import { useAlarmSound } from "@/hooks/useAlarmSound";
import Bar, { BarWithMarker } from "@/components/common/Bar";
import { SEVERITY_VAR } from "@/components/common/SeverityBadge";
import type { AlarmSeverity } from "@/types/alarm";
import type { Device } from "@/types/device";
import type { Alarm } from "@/types/alarm";
import type { DeviceEvent } from "@/types/event";
import type { DeviceLatestTelemetry, DeviceHealthSnapshot } from "@/types/telemetry";
import StatusBadge from "@/components/common/StatusBadge";
import SensorStateBadge from "@/components/common/SensorStateBadge";
import AlarmTable from "@/components/alarms/AlarmTable";
import EventTable from "@/components/alarms/EventTable";
import { formatRelative } from "@/utils/format";
import { siteLabel } from "@/lib/deviceSemantics";
import { useI18n } from "@/i18n/I18nContext";
import {
  Zap,
  Sun,
  ArrowUpRight,
  Clock,
} from "lucide-react";
import ErrorState from "@/components/common/ErrorState";
import { describeFailure, type LoadFailure } from "@/pages/Devices/loadDevices";
export default function Dashboard() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const { playAlarm } = useAlarmSound();

  const [devices, setDevices]                     = useState<Device[]>([]);
  const [allAlarms, setAllAlarms]                 = useState<Alarm[]>([]);
  const [selectedId, setSelectedId]               = useState<string>("");
  const [latestTel, setLatestTel]                 = useState<DeviceLatestTelemetry | undefined>(undefined);
  const [events, setEvents]                       = useState<DeviceEvent[]>([]);
  const [, setHealthSnap]                         = useState<DeviceHealthSnapshot | undefined>(undefined);
  const [loadingBase, setLoadingBase]             = useState(true);
  const [loadingDev, setLoadingDev]               = useState(true);
  const [fleetFailure, setFleetFailure]           = useState<LoadFailure | null>(null);
  const [alarmsUnavailable, setAlarmsUnavailable] = useState(false);
  const [, setMqttRuntimeState]                   = useState<string | undefined>(undefined);

  useEffect(() => {
    let live = true;
    getIntegrationStatus()
      .then((status: IntegrationStatus) => {
        if (live) setMqttRuntimeState(status.mqtt?.state);
      })
      .catch(() => {
        if (live) setMqttRuntimeState(undefined);
      });
    return () => { live = false; };
  }, []);

  // Load fleet once
  const loadFleet = () => {
    setLoadingBase(true);
    setFleetFailure(null);
    Promise.allSettled([getDevices(), getAllAlarms()]).then(([devsRes, almsRes]) => {
      if (devsRes.status === "rejected") {
        setFleetFailure(describeFailure(devsRes.reason));
        setDevices([]);
        setAllAlarms([]);
        setLoadingBase(false);
        return;
      }
      const devs = devsRes.value;
      const alarmsOk = almsRes.status === "fulfilled";
      setAlarmsUnavailable(!alarmsOk);
      setDevices(devs);
      setAllAlarms(alarmsOk ? almsRes.value : []);
      if (devs.length > 0) {
        setSelectedId((prev) => (prev && devs.some((d) => d.id === prev) ? prev : devs[0].id));
      } else {
        setSelectedId("");
      }
      setLoadingBase(false);
    });
  };

  useEffect(() => {
    loadFleet();
  }, []);

  // Reload selected-device data on selector change
  useEffect(() => {
    if (!selectedId) {
      setLoadingDev(false);
      return;
    }
    setLoadingDev(true);
    Promise.allSettled([
      getDeviceLatestTelemetry(selectedId),
      getDeviceEvents(selectedId),
      getDeviceHealthSnapshot(selectedId),
    ]).then(([telRes, evtsRes, snapRes]) => {
      if (telRes.status === "fulfilled") setLatestTel(telRes.value);
      if (evtsRes.status === "fulfilled") setEvents(evtsRes.value);
      if (snapRes.status === "fulfilled") setHealthSnap(snapRes.value);
      setLoadingDev(false);
    });
  }, [selectedId]);

  useDeviceLive(selectedId || undefined, {
    onTelemetry: (payload) => {
      setLatestTel((prev) => ({
        deviceId: payload.deviceId,
        timestamp: payload.timestamp,
        dataAge: payload.dataAge ?? prev?.dataAge ?? "no_data",
        mppt: payload.mppt as unknown as DeviceLatestTelemetry["mppt"],
        pzem: payload.pzem as unknown as DeviceLatestTelemetry["pzem"],
        // An explicit null in the frame means "no reading" and must render as
        // NO DATA; only a frame that omits solar entirely keeps the REST value.
        solar: payload.solar !== undefined ? payload.solar : (prev?.solar ?? null),
        derived: (payload.derived ?? prev?.derived ?? null) as DeviceLatestTelemetry["derived"],
      }));
    },
    onStatus: (payload) => {
      setDevices((prev) =>
        prev.map((d) =>
          d.id === payload.deviceId
            ? {
                ...d,
                status: payload.status,
                connectivity: payload.connectivity,
                dataAge: payload.dataAge,
                lastSeen: payload.lastSeen,
                clockSkew: payload.clockSkew,
                clockSkewSeconds: payload.clockSkewSeconds,
                mpptState: payload.mpptState ?? d.mpptState,
                pzemState: payload.pzemState ?? d.pzemState,
                sensor3State: payload.sensor3State ?? d.sensor3State,
                acquisitionState: payload.acquisitionState ?? d.acquisitionState,
              }
            : d
        )
      );
    },
    onHealth: (payload) => {
      setHealthSnap((prev) => ({
        deviceId: payload.deviceId,
        cpuPercent: payload.cpuPercent,
        ramPercent: payload.ramPercent,
        flashPercent: prev?.flashPercent ?? null,
        temperatureC: payload.temperatureC,
        uptimeSeconds: prev?.uptimeSeconds ?? null,
        rssi: payload.rssi,
        networkState: prev?.networkState ?? null,
        mqttConnected: prev?.mqttConnected ?? null,
        lastTelemetryAge: payload.lastTelemetryAge,
      }));
    },
    onAlarm: (severity, alarmId) => {
      playAlarm(severity, alarmId);
    },
    onResync: () => {
      getDevices().then(setDevices).catch(() => {});
      if (!selectedId) return;
      getDeviceLatestTelemetry(selectedId).then(setLatestTel).catch(() => {});
    },
  });

  const online       = devices.filter((d) => d.status === "online").length;
  const degraded     = devices.filter((d) => d.status === "degraded").length;
  const offline      = devices.filter((d) => d.status === "offline").length;
  const activeAlarms = allAlarms.filter((a) => a.status === "active").length;
  // Severity mix of the active alarms: the count alone cannot say whether the
  // fleet is one warning away from noise or one disaster away from an outage.
  const activeBySeverity = allAlarms
    .filter((a) => a.status === "active")
    .reduce<Record<string, number>>((acc, a) => {
      acc[a.severity] = (acc[a.severity] ?? 0) + 1;
      return acc;
    }, {});
  const severityOrder: AlarmSeverity[] = ["disaster", "high", "average", "warning", "information"];

  // Fleet composition rows. Length = share of the fleet, so the same perceptual
  // reading works in all three cards; a zero keeps its row and shows an empty
  // track, because "none in this state" is information and the card should not
  // change shape as states come and go.
  const statusRows = [
    { key: "online", label: t("dashboard.kpi.online"), value: online, className: "bg-[var(--status-online)]" },
    { key: "attention", label: t("dashboard.kpi.attention"), value: degraded, className: "bg-[var(--chart-1)]" },
    { key: "offline", label: t("dashboard.kpi.offline"), value: offline, className: "bg-[var(--destructive)]" },
  ];
  const modelCounts = devices.reduce<Record<string, number>>((acc, d) => {
    const model = d.model || t("state.unknown");
    acc[model] = (acc[model] ?? 0) + 1;
    return acc;
  }, {});
  const modelRows = Object.entries(modelCounts)
    .sort((a, b) => b[1] - a[1])
    // Neutral tone: the model dimension carries no severity, and reusing the
    // attention colour here would give one hue two meanings on one screen.
    .map(([model, count]) => ({ key: model, label: model, value: count, className: "bg-foreground/70" }));
  const alarmRows = severityOrder
    .filter((sev) => (activeBySeverity[sev] ?? 0) > 0)
    .map((sev) => ({
      key: sev,
      label: t(`badge.severity.${sev}`),
      value: activeBySeverity[sev] ?? 0,
      className: `bg-[var(${SEVERITY_VAR[sev]})]`,
    }));

  const selectedDevice = devices.find((d) => d.id === selectedId);
  const deviceAlarms   = allAlarms.filter((a) => a.deviceId === selectedId);

  // Derived telemetry metrics
  const pvPower = latestTel?.derived?.pvInputPower ?? (
    latestTel?.mppt?.pvVoltage != null && latestTel?.mppt?.pvCurrent != null
      ? +(latestTel.mppt.pvVoltage * latestTel.mppt.pvCurrent).toFixed(2)
      : null
  );
  const outPower = latestTel?.derived?.outputPower ?? (
    latestTel?.mppt?.outputVoltage != null && latestTel?.mppt?.outputCurrent != null
      ? +(latestTel.mppt.outputVoltage * latestTel.mppt.outputCurrent).toFixed(2)
      : null
  );
  const accumulatedEnergy = latestTel?.pzem?.energy ?? null;
  // Shared scales for the two comparisons the snapshot can honestly make:
  // array vs MPPT output power, and the three phase currents against each other.
  const powerFlowMax = Math.max(pvPower ?? 0, outPower ?? 0);
  const phaseCurrentMax = Math.max(
    latestTel?.pzem?.phaseACurrent ?? 0,
    latestTel?.pzem?.phaseBCurrent ?? 0,
    latestTel?.pzem?.phaseCCurrent ?? 0,
  );

  const isStale = latestTel?.dataAge != null && latestTel?.dataAge !== "fresh";

  return (
    <div className="space-y-6">
      {fleetFailure && (
        <ErrorState
                hint={t("common.contactAdmin")}
          title={t("dashboard.fleetUnavailable")}
          message={fleetFailure.message}
          onRetry={loadFleet}
        />
      )}

      {/* Top Header & Device Selector */}
      <div className="border-b border-border/60 pb-5">
        <PageHeader
          title={t("dashboard.title")}
          subtitle={t("dashboard.subtitle")}
          actions={<>
          <Select value={selectedId} onValueChange={setSelectedId}>
            <SelectTrigger className="w-[200px] sm:w-[240px] bg-card text-xs font-medium">
              <SelectValue placeholder={t("dashboard.selectDevice")} />
            </SelectTrigger>
            <SelectContent>
              {devices.map((d) => (
                <SelectItem key={d.id} value={d.id} className="text-xs">
                  {siteLabel(d.siteName, d.siteId)} <span className="text-muted-foreground tabular-nums">{d.id}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {selectedId && (
            <TextureButton
              variant="minimal"
              size="sm"
              onClick={() => navigate(`/devices/${selectedId}`)}
            >
              <ArrowUpRight className="size-3.5" />
              <span>{t("dashboard.details")}</span>
            </TextureButton>
          )}
          </>}
        />
      </div>

      {/*  FLEET BREAKDOWN: three distributions, one visual vocabulary  */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Card>
          <CardHeader className="p-4 sm:p-5 pb-2">
            <CardTitle className="text-sm font-semibold text-foreground">{t("dashboard.status.title")}</CardTitle>
          </CardHeader>
          <CardContent className="p-4 sm:p-5 pt-0 space-y-3">
            {statusRows.map((row) => (
              <div key={row.key} className="space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">{row.label}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {loadingBase ? t("common.loading") : row.value}
                  </span>
                </div>
                <Bar height={5} max={devices.length} segments={[{ value: row.value, className: row.className }]} />
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 sm:p-5 pb-2">
            <CardTitle className="text-sm font-semibold text-foreground">{t("dashboard.model.title")}</CardTitle>
          </CardHeader>
          <CardContent className="p-4 sm:p-5 pt-0 space-y-3">
            {modelRows.length === 0 ? (
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{loadingBase ? t("common.loading") : t("state.noData")}</span>
                <span className="font-semibold text-foreground tabular-nums">0</span>
              </div>
            ) : (
              modelRows.map((row) => (
                <div key={row.key} className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{row.label}</span>
                    <span className="font-semibold text-foreground tabular-nums">{row.value}</span>
                  </div>
                  <Bar height={5} max={devices.length} segments={[{ value: row.value, className: row.className }]} />
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 sm:p-5 pb-2">
            <CardTitle className="text-sm font-semibold text-foreground">{t("dashboard.alarms.breakdownTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="p-4 sm:p-5 pt-0 space-y-3">
            {alarmRows.length === 0 ? (
              <p className="flex min-h-[88px] items-center justify-center text-center text-xs text-muted-foreground">
                {alarmsUnavailable ? t("state.unknownUpper") : t("dashboard.alarms.none")}
              </p>
            ) : (
              alarmRows.map((row) => (
                <div key={row.key} className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{row.label}</span>
                    <span className="font-semibold text-foreground tabular-nums">{row.value}</span>
                  </div>
                  <Bar height={5} max={activeAlarms} segments={[{ value: row.value, className: row.className }]} />
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/*  3-5 SECOND RECOGNITION HERO SECTION  */}
      {selectedDevice && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {/* Hero 1: Site & Operational State */}
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm flex flex-col">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">
                  {t("dashboard.hero.siteState")}
                </span>
                <StatusBadge status={selectedDevice.status} />
              </div>

              <div className="mt-3">
                <CardTitle className="text-lg font-bold sm:text-lg">
                  {siteLabel(selectedDevice.siteName, selectedDevice.siteId)}
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {selectedDevice.id} · {selectedDevice.model || "RUT906"}
                </p>
              </div>
            </div>

            <div className="mt-4 pt-3 border-t border-border/50 flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Clock className="size-3.5" />
                <span>{t("dashboard.hero.lastSeen")}</span>
              </div>
              <span className="font-medium text-foreground">
                {formatRelative(selectedDevice.lastSeen)}
              </span>
            </div>
          </div>

          {/* Hero 2: PV Generation Power (Primary Energy Metric) */}
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm flex flex-col relative overflow-hidden">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Sun className="size-3.5 text-[var(--chart-1)]" />
                <span>{t("dashboard.hero.pvGeneration")}</span>
              </span>
              {isStale && (
                <span className="text-[10px] font-medium text-[var(--chart-1)] bg-[var(--chart-1)]/10 px-1.5 py-0.5 rounded">
                  {t("dashboard.hero.stale")}
                </span>
              )}
            </div>

            <div className="mt-3">
              <div className="flex items-baseline gap-1.5">
                <span className="text-3xl sm:text-4xl font-extrabold tracking-tight tabular-nums text-foreground">
                  {pvPower !== null ? pvPower.toFixed(2) : t("state.noData")}
                </span>
                <span className="text-sm font-semibold text-muted-foreground">W</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {t("dashboard.hero.pvPowerNote")}
              </p>
            </div>

            <div className="mt-4 pt-3 border-t border-border/50 space-y-1.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-[11px]">{t("dashboard.hero.arrayVoltage")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.pvVoltage != null ? `${latestTel.mppt.pvVoltage.toFixed(2)} V` : t("state.noData")}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-[11px]">{t("dashboard.hero.arrayCurrent")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.pvCurrent != null ? `${latestTel.mppt.pvCurrent.toFixed(2)} A` : t("state.noData")}
                </span>
              </div>
            </div>
          </div>

          {/* Hero 3: Accumulated Energy & Output */}
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm flex flex-col">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Zap className="size-3.5 text-foreground" />
                <span>{t("dashboard.hero.accumulatedEnergy")}</span>
              </span>
              <SensorStateBadge state={selectedDevice.pzemState} />
            </div>

            <div className="mt-3">
              <div className="flex items-baseline gap-1.5">
                <span className="text-3xl sm:text-4xl font-extrabold tracking-tight tabular-nums text-foreground">
                  {accumulatedEnergy !== null ? accumulatedEnergy.toFixed(3) : t("state.noData")}
                </span>
                <span className="text-sm font-semibold text-muted-foreground">kWh</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {t("dashboard.hero.energyNote")}
              </p>
            </div>
          </div>
        </div>
      )}

      {/*  CONSOLIDATED SUBSYSTEM TELEMETRY (CLEAN NOC GRIDS)  */}
      {selectedDevice && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Subsystem 1: Solar & MPPT Conversion */}
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between border-b border-border/50 pb-3">
              <div>
                <CardTitle className="text-sm font-bold sm:text-sm">
                  {t("dashboard.mppt.title")}
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t("dashboard.mppt.subtitle")}
                </p>
              </div>
              <SensorStateBadge state={selectedDevice.mpptState} />
            </div>

            <div className="mt-3 space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.pvVoltage")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.pvVoltage != null ? `${latestTel?.mppt?.pvVoltage.toFixed(2)} V` : t("state.noData")}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.pvCurrent")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.pvCurrent != null ? `${latestTel?.mppt?.pvCurrent.toFixed(2)} A` : t("state.noData")}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.outVoltage")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.outputVoltage != null ? `${latestTel?.mppt?.outputVoltage.toFixed(2)} V` : t("state.noData")}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.outCurrent")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {latestTel?.mppt?.outputCurrent != null ? `${latestTel?.mppt?.outputCurrent.toFixed(2)} A` : t("state.noData")}
                </span>
              </div>
            </div>

            <div className="mt-3 space-y-2 border-t border-border/50 pt-3">
              {/* Bullet-style: the output bar carries a marker at the array level,
                  so "how much of the harvest arrives at the output" is a length,
                  not a subtraction the reader has to do. */}
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.arrayPower")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {pvPower != null ? `${pvPower.toFixed(2)} W` : t("state.noData")}
                </span>
              </div>
              <BarWithMarker value={pvPower ?? 0} max={powerFlowMax} />
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("dashboard.mppt.outputPower")}</span>
                <span className="font-semibold text-foreground tabular-nums">
                  {outPower != null ? `${outPower.toFixed(2)} W` : t("state.noData")}
                </span>
              </div>
              <BarWithMarker
                value={outPower ?? 0}
                max={powerFlowMax}
                marker={pvPower}
                markerLabel={t("dashboard.mppt.arrayPower")}
              />
            </div>
            <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-2">
                <span>
                  {t("dashboard.mppt.irradiance")}:{" "}
                  <span className="font-bold text-foreground tabular-nums">
                    {latestTel?.solar?.radiation != null ? `${latestTel.solar.radiation.toFixed(0)} W/m²` : t("state.noData")}
                  </span>
                </span>
                <SensorStateBadge state={selectedDevice.sensor3State} />
              </span>
              <TextureButton
                variant="minimal"
                size="sm"
                onClick={() => navigate(`/monitoring`)}
              >
                {t("dashboard.mppt.historicalTrends")}
              </TextureButton>
            </div>
          </div>

          {/* Subsystem 2: AC 3-Phase Power Meter */}
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between border-b border-border/50 pb-3">
              <div>
                <CardTitle className="text-sm font-bold sm:text-sm">
                  {t("dashboard.ac.title")}
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t("dashboard.ac.subtitle")}
                </p>
              </div>
              <SensorStateBadge state={selectedDevice.pzemState} />
            </div>

            <div className="grid grid-cols-3 gap-3">
              {/* Phase A */}
              <div className="rounded-lg bg-secondary/40 p-3 space-y-1">
                <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">{t("dashboard.ac.phaseA")}</span>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.voltage")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseAVoltage != null ? `${latestTel.pzem.phaseAVoltage.toFixed(1)} V` : t("state.noData")}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.current")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseACurrent != null ? `${latestTel.pzem.phaseACurrent.toFixed(2)} A` : t("state.noData")}
                  </span>
                </div>
                <Bar
                  className="mt-1"
                  height={4}
                  max={phaseCurrentMax}
                  segments={[{ value: latestTel?.pzem?.phaseACurrent ?? 0, className: "bg-foreground/70" }]}
                />
              </div>

              {/* Phase B */}
              <div className="rounded-lg bg-secondary/40 p-3 space-y-1">
                <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">{t("dashboard.ac.phaseB")}</span>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.voltage")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseBVoltage != null ? `${latestTel.pzem.phaseBVoltage.toFixed(1)} V` : t("state.noData")}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.current")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseBCurrent != null ? `${latestTel.pzem.phaseBCurrent.toFixed(2)} A` : t("state.noData")}
                  </span>
                </div>
                <Bar
                  className="mt-1"
                  height={4}
                  max={phaseCurrentMax}
                  segments={[{ value: latestTel?.pzem?.phaseBCurrent ?? 0, className: "bg-foreground/70" }]}
                />
              </div>

              {/* Phase C */}
              <div className="rounded-lg bg-secondary/40 p-3 space-y-1">
                <span className="text-[11px] font-bold text-foreground uppercase tracking-wider block">{t("dashboard.ac.phaseC")}</span>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.voltage")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseCVoltage != null ? `${latestTel.pzem.phaseCVoltage.toFixed(1)} V` : t("state.noData")}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground flex justify-between">
                  <span>{t("dashboard.ac.current")}</span>
                  <span className="font-semibold text-foreground tabular-nums">
                    {latestTel?.pzem?.phaseCCurrent != null ? `${latestTel.pzem.phaseCCurrent.toFixed(2)} A` : t("state.noData")}
                  </span>
                </div>
                <Bar
                  className="mt-1"
                  height={4}
                  max={phaseCurrentMax}
                  segments={[{ value: latestTel?.pzem?.phaseCCurrent ?? 0, className: "bg-foreground/70" }]}
                />
              </div>
            </div>

            <div className="flex items-center justify-end pt-1 text-xs">
              <TextureButton
                variant="minimal"
                size="sm"
                onClick={() => navigate(`/reports`)}
              >
                {t("dashboard.ac.energyReports")}
              </TextureButton>
            </div>
          </div>
        </div>
      )}

      {/*  ACTIVE ALARMS & RECENT ACTIVITY  */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Alarms Card */}
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between border-b border-border/50 pb-3">
            <div>
              <CardTitle className="text-sm font-bold sm:text-sm">
                {t("dashboard.alarms.title")}
              </CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t("dashboard.alarms.subtitle", { site: selectedId || t("dashboard.alarms.siteFallback") })}
              </p>
            </div>
            {deviceAlarms.filter((a) => a.status === "active").length > 0 && (
              <span className="text-xs font-medium text-[var(--destructive)]">
                {t("dashboard.alarms.activeCount", { count: deviceAlarms.filter((a) => a.status === "active").length })}
              </span>
            )}
          </div>
          <div className="overflow-hidden">
            <AlarmTable
              alarms={deviceAlarms.filter((a) => a.status === "active")}
              loading={loadingBase}
              emptyMessage={t("dashboard.alarms.none")}
            />
          </div>
        </div>

        {/* Recent Events Card */}
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between border-b border-border/50 pb-3">
            <div>
              <CardTitle className="text-sm font-bold sm:text-sm">
                {t("dashboard.events.title")}
              </CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t("dashboard.events.subtitle")}
              </p>
            </div>
            <TextureButton
              variant="minimal"
              size="sm"
              onClick={() => navigate(`/alarms`)}
            >
              {t("dashboard.events.all")}
            </TextureButton>
          </div>
          <div className="overflow-hidden">
            <EventTable events={events.slice(0, 5)} loading={loadingDev} />
          </div>
        </div>
      </div>
    </div>
  );
}
