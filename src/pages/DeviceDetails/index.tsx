import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getDevice, getDeviceTelemetry, getDeviceLatestTelemetry,
  getDeviceAlarms, getDeviceEvents,
  getDeviceHealthHistory, getDeviceHealthSnapshot, getIntegrationStatus,
  type IntegrationStatus,
} from "@/services/api";
import { useDeviceLive } from "@/hooks/useDeviceLive";
import type { Device } from "@/types/device";
import type {
  DeviceTelemetry, DeviceLatestTelemetry,
  DeviceHealthHistory, DeviceHealthSnapshot,
} from "@/types/telemetry";
import type { Alarm } from "@/types/alarm";
import type { DeviceEvent } from "@/types/event";
import { TIME_RANGES, filterByTimeRange, type TimeRangeKey } from "@/utils/timeRange";
import { axisFor, chartNoteKey, metricNoteKey, SERIES_COLORS } from "@/utils/chartConfig";
import { useI18n } from "@/i18n/I18nContext";
import { formatTimestamp, formatRelative } from "@/utils/format";
import { connectivityLabel, mqttStateLabel, siteLabel } from "@/lib/deviceSemantics";
import { healthSourceState, healthStateLabel } from "@/lib/telemetrySemantics";
import StatusBadge from "@/components/common/StatusBadge";
import HealthBadge from "@/components/common/HealthBadge";
import SensorStateBadge from "@/components/common/SensorStateBadge";
import ErrorState from "@/components/common/ErrorState";
import TelemetryCard from "@/components/telemetry/TelemetryCard";
import TelemetryChart from "@/components/telemetry/TelemetryChart";
import TimeRangeSelector from "@/components/telemetry/TimeRangeSelector";
import AlarmTable from "@/components/alarms/AlarmTable";
import EventTable from "@/components/alarms/EventTable";
import RemoteAccessCard from "@/components/remote/RemoteAccessCard";
import { TextureButton } from "@/components/ui/texture-button";
import type { ReactNode } from "react";
import {
  Sun,
  Zap,
  Activity,
  Terminal,
  Globe,
} from "lucide-react";

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between items-center py-2.5 border-b border-border/40 last:border-0 text-xs sm:text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{children}</span>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col justify-between shadow-xs">
      <CardHeader className="p-4 sm:p-5 pb-2">
        <CardTitle className="text-sm font-semibold text-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent className="p-4 sm:p-5 pt-0">{children}</CardContent>
    </Card>
  );
}

function formatUptime(sec: number | null, noData: string): string {
  if (sec === null) return noData;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

export default function DeviceDetails() {
  const { t } = useI18n();
  // History fetch failure is a read failure, not an empty source.
  const [histFailed, setHistFailed] = useState(false);
  // A 404 (device gone) and a 5xx (backend down) are different facts for the operator.
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const { deviceId } = useParams<{ deviceId: string }>();
  const navigate = useNavigate();

  const [device,      setDevice]      = useState<Device | undefined>(undefined);
  const [telemetry,   setTelemetry]   = useState<DeviceTelemetry | undefined>(undefined);
  const [latestTel,   setLatestTel]   = useState<DeviceLatestTelemetry | undefined>(undefined);
  const [alarms,      setAlarms]      = useState<Alarm[]>([]);
  const [events,      setEvents]      = useState<DeviceEvent[]>([]);
  const [,            setHealthHist]  = useState<DeviceHealthHistory | undefined>(undefined);
  const [healthSnap,  setHealthSnap]  = useState<DeviceHealthSnapshot | undefined>(undefined);
  const [loading,     setLoading]     = useState(true);
  const [histLoading, setHistLoading] = useState(true);
  const [timeRange,   setTimeRange]   = useState<TimeRangeKey>("24h");
  const [mqttRuntimeState, setMqttRuntimeState] = useState<string | undefined>(undefined);

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
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId) return;
    // Only what the page needs to render: identity, live values, alarms, events
    // and the health snapshot. Zabbix-backed history is fetched separately
    // below, because its rollup sync is an order of magnitude slower and used
    // to hold the whole page on "Loading".
    Promise.allSettled([
      getDevice(deviceId),
      getDeviceLatestTelemetry(deviceId),
      getDeviceAlarms(deviceId),
      getDeviceEvents(deviceId),
      getDeviceHealthSnapshot(deviceId),
    ]).then(([devRes, latestRes, almsRes, evtsRes, hSnapRes]) => {
      if (devRes.status === "fulfilled") {
        setDevice(devRes.value);
        // A fulfilled-but-undefined answer is the API's 404 path.
        setDeviceError(devRes.value ? null : t("deviceDetails.notFound"));
      } else {
        setDeviceError(t("deviceDetails.loadFailed"));
      }
      if (latestRes.status === "fulfilled") setLatestTel(latestRes.value);
      if (almsRes.status === "fulfilled") setAlarms(almsRes.value);
      if (evtsRes.status === "fulfilled") setEvents(evtsRes.value);
      if (hSnapRes.status === "fulfilled") setHealthSnap(hSnapRes.value);
      setLoading(false);
    });
  }, [deviceId, t]);

  // History charts load independently and show their own skeleton meanwhile.
  // The selected range is the query, not a client-side slice: a 6h view must
  // come back as 6h of raw samples, not as the hourly buckets of a 24h window.
  useEffect(() => {
    if (!deviceId) return;
    let live = true;
    setHistLoading(true);
    Promise.allSettled([
      getDeviceTelemetry(deviceId, { range: timeRange }),
      getDeviceHealthHistory(deviceId),
    ]).then(([telRes, hHistRes]) => {
      if (!live) return;
      setHistFailed(telRes.status === "rejected");
      if (telRes.status === "fulfilled") setTelemetry(telRes.value);
      if (hHistRes.status === "fulfilled") setHealthHist(hHistRes.value);
      setHistLoading(false);
    });
    return () => { live = false; };
  }, [deviceId, timeRange]);

  useDeviceLive(deviceId, {
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
      setDevice((prev) =>
        prev
          ? {
              ...prev,
              status: payload.status,
              connectivity: payload.connectivity,
              dataAge: payload.dataAge,
              lastSeen: payload.lastSeen,
              clockSkew: payload.clockSkew,
              clockSkewSeconds: payload.clockSkewSeconds,
              mpptState: payload.mpptState ?? prev.mpptState,
              pzemState: payload.pzemState ?? prev.pzemState,
              sensor3State: payload.sensor3State ?? prev.sensor3State,
              acquisitionState: payload.acquisitionState ?? prev.acquisitionState,
            }
          : prev
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
    onResync: () => {
      const target: string | undefined = deviceId;
      if (!target) return;
      getDevice(target).then(setDevice).catch(() => {});
      getDeviceTelemetry(target).then(setTelemetry).catch(() => {});
      getDeviceLatestTelemetry(target).then(setLatestTel).catch(() => {});
    },
  });

  const rangeMs = TIME_RANGES[timeRange].ms;
  const mppt    = telemetry?.mppt;
  const pzem    = telemetry?.pzem;
  const noTel   = !histLoading && !telemetry;
  const chartsLoading = histLoading && !telemetry;

  const routerHealth = healthSnap?.dimensions?.routerHealth;
  const healthState = healthSourceState(healthSnap?.state, [
    healthSnap?.cpuPercent,
    healthSnap?.ramPercent,
    healthSnap?.flashPercent,
    healthSnap?.temperatureC,
    healthSnap?.uptimeSeconds,
    healthSnap?.rssi,
    healthSnap?.networkState,
  ]);
  // The badge is only trustworthy with the readings behind it in view.
  const healthEvidence = (() => {
    if (healthState !== "available" || !healthSnap) return null;
    const parts: string[] = [];
    if (healthSnap.cpuPercent != null) parts.push(`CPU ${healthSnap.cpuPercent}%`);
    if (healthSnap.ramPercent != null) parts.push(`RAM ${healthSnap.ramPercent}%`);
    if (healthSnap.flashPercent != null) parts.push(`flash ${healthSnap.flashPercent}%`);
    if (healthSnap.temperatureC != null) parts.push(`${healthSnap.temperatureC} °C`);
    return parts.length ? parts.join(" · ") : null;
  })();

  if (!loading && !device) return <ErrorState hint={t("common.contactAdmin")} message={deviceError ?? t("deviceDetails.notFound")} />;

  const pvPower = latestTel?.derived?.pvInputPower ?? (
    latestTel?.mppt?.pvVoltage != null && latestTel?.mppt?.pvCurrent != null
      ? +(latestTel.mppt.pvVoltage * latestTel.mppt.pvCurrent).toFixed(2)
      : null
  );

  return (
    <div className="space-y-6">
      {/* Device Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-border/60 pb-5">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-foreground sm:text-2xl">
              {device ? siteLabel(device.siteName, device.siteId) : t("common.loading")}
            </h1>
            {device && <StatusBadge status={device.status} />}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {device?.model || t("deviceDetails.value.modelFallback")} · {t("deviceDetails.header.serial")} <span className="tabular-nums">{device ? device.serial || t("state.noData") : t("common.loading")}</span> · <span className="tabular-nums">{device?.id ?? ""}</span>
          </p>
        </div>

        {deviceId && (
          <div className="flex items-center gap-2">
            <TextureButton
              variant="minimal"
              size="sm"
              onClick={() => navigate(`/remote/${deviceId}/webui`)}
            >
              <Globe className="size-3.5" />
              <span>{t("deviceDetails.action.nativeWebui")}</span>
            </TextureButton>
            <TextureButton
              variant="primary"
              size="sm"
              onClick={() => navigate(`/remote/${deviceId}/terminal`)}
            >
              <Terminal className="size-3.5" />
              <span>{t("deviceDetails.action.sshTerminal")}</span>
            </TextureButton>
          </div>
        )}
      </div>

      {/* Primary Tabs Navigation */}
      <Tabs defaultValue="overview">
        <TabsList className="mb-6 flex-wrap bg-card border border-border/70 p-1">
          <TabsTrigger value="overview" className="text-xs">{t("deviceDetails.tab.overview")}</TabsTrigger>
          <TabsTrigger value="power" className="text-xs">{t("deviceDetails.tab.power")}</TabsTrigger>
          <TabsTrigger value="energy" className="text-xs">{t("deviceDetails.tab.energy")}</TabsTrigger>
          <TabsTrigger value="diagnostics" className="text-xs">{t("deviceDetails.tab.diagnostics")}</TabsTrigger>
          <TabsTrigger value="alarms" className="text-xs">{t("deviceDetails.tab.alarms")}</TabsTrigger>
          <TabsTrigger value="remote" className="text-xs">{t("deviceDetails.tab.remote")}</TabsTrigger>
        </TabsList>

        {/* ── 1. Overview ── */}
        <TabsContent value="overview" className="space-y-6">
          {/* Quick KPI Trio */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">{t("deviceDetails.kpi.pvGeneration")}</span>
                <Sun className="size-4 text-[var(--chart-1)]" />
              </div>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-3xl font-extrabold text-foreground tabular-nums">
                  {pvPower !== null ? pvPower.toFixed(2) : t("state.noData")}
                </span>
                <span className="text-sm font-semibold text-muted-foreground">W</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">{t("deviceDetails.kpi.pvGenerationNote")}</p>
            </div>

            <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">{t("deviceDetails.kpi.energy")}</span>
                <Zap className="size-4 text-foreground" />
              </div>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-3xl font-extrabold text-foreground tabular-nums">
                  {latestTel?.pzem?.energy !== null && latestTel?.pzem?.energy !== undefined ? latestTel.pzem.energy.toFixed(3) : t("state.noData")}
                </span>
                <span className="text-sm font-semibold text-muted-foreground">kWh</span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">{t("deviceDetails.kpi.energyNote")}</p>
            </div>

            <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">{t("deviceDetails.kpi.health")}</span>
                <Activity className="size-4 text-muted-foreground" />
              </div>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-3xl font-extrabold text-foreground">
                  {device ? <HealthBadge health={device.health} /> : t("state.noData")}
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">{t("deviceDetails.kpi.lastSeen", { value: device ? formatRelative(device.lastSeen) : t("state.noData") })}</p>
              {healthEvidence && (
                <p className="text-[11px] text-muted-foreground mt-0.5 tabular-nums">{healthEvidence}</p>
              )}
            </div>
          </div>

          {/* Details Metadata Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground font-sans">
                  {t("deviceDetails.card.identity")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0 space-y-0">
                <InfoRow label={t("deviceDetails.field.site")}>{device ? siteLabel(device.siteName, device.siteId) : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.deviceModel")}>{device?.model ?? t("deviceDetails.value.modelFallback")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.serialNumber")}>{device ? device.serial || t("state.noData") : t("common.loading")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.networkType")}>{device?.network ?? t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.connectivity")}>{device ? connectivityLabel(device.connectivity) : t("state.noData")}</InfoRow>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground font-sans">
                  {t("deviceDetails.card.health")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0 space-y-0">
                <InfoRow label={t("deviceDetails.field.telemetryStatus")}>{device ? <StatusBadge status={device.status} /> : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.systemHealth")}>
                  {device ? (
                    <span className="inline-flex items-center gap-2">
                      <HealthBadge health={device.health} />
                      {healthEvidence && (
                        <span className="text-[11px] text-muted-foreground tabular-nums">{healthEvidence}</span>
                      )}
                    </span>
                  ) : (t("state.noData"))}
                </InfoRow>
                <InfoRow label={t("deviceDetails.field.lastTelemetry")}>{device ? formatTimestamp(device.lastSeen) : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.mpptAcquisition")}>{device ? <SensorStateBadge state={device.mpptState} /> : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.pzemAcquisition")}>{device ? <SensorStateBadge state={device.pzemState} /> : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.solarAcquisition")}>{device ? <SensorStateBadge state={device.sensor3State} /> : t("state.noData")}</InfoRow>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* ── 2. Solar & MPPT Power ── */}
        <TabsContent value="power" className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
            <span className="text-xs text-muted-foreground">{t("deviceDetails.device.mpptController")}</span>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <TelemetryCard label={t("deviceDetails.metric.pvVoltage")}     value={latestTel?.mppt?.pvVoltage     ?? null} unit="V" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.pvCurrent")}     value={latestTel?.mppt?.pvCurrent     ?? null} unit="A" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.outputVoltage")} value={latestTel?.mppt?.outputVoltage ?? null} unit="V" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.outputCurrent")} value={latestTel?.mppt?.outputCurrent ?? null} unit="A" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.solarRadiation")} value={latestTel?.solar?.radiation ?? null} unit="W/m²" decimals={0} loading={loading} dataAge={latestTel?.dataAge} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ChartCard title={t("deviceDetails.chart.pvVoltage")}>
              <TelemetryChart title={t("deviceDetails.metric.pvVoltage")} datasets={[{ label: t("deviceDetails.metric.pvVoltage"), data: mppt ? filterByTimeRange(mppt.pvVoltage, rangeMs) : [], color: SERIES_COLORS.primary() }]} unit="V" {...axisFor("mppt.pvVoltage")} description={t(metricNoteKey("mppt.pvVoltage"))} seriesIntervalMs={telemetry?.seriesIntervalMs} timeRange={timeRange} loading={chartsLoading} empty={noTel} error={histFailed} />
            </ChartCard>
            <ChartCard title={t("deviceDetails.chart.pvCurrent")}>
              <TelemetryChart title={t("deviceDetails.metric.pvCurrent")} datasets={[{ label: t("deviceDetails.metric.pvCurrent"), data: mppt ? filterByTimeRange(mppt.pvCurrent, rangeMs) : [], color: SERIES_COLORS.primary() }]} unit="A" {...axisFor("mppt.pvCurrent")} description={t(metricNoteKey("mppt.pvCurrent"))} seriesIntervalMs={telemetry?.seriesIntervalMs} timeRange={timeRange} loading={chartsLoading} empty={noTel} error={histFailed} />
            </ChartCard>
            <ChartCard title={t("deviceDetails.chart.outputVoltage")}>
              <TelemetryChart title={t("deviceDetails.metric.outputVoltage")} datasets={[{ label: t("deviceDetails.metric.outputVoltage"), data: mppt ? filterByTimeRange(mppt.outputVoltage, rangeMs) : [], color: SERIES_COLORS.primary() }]} unit="V" {...axisFor("mppt.outputVoltage")} description={t(metricNoteKey("mppt.outputVoltage"))} seriesIntervalMs={telemetry?.seriesIntervalMs} timeRange={timeRange} loading={chartsLoading} empty={noTel} error={histFailed} />
            </ChartCard>
            <ChartCard title={t("deviceDetails.chart.outputCurrent")}>
              <TelemetryChart title={t("deviceDetails.metric.outputCurrent")} datasets={[{ label: t("deviceDetails.metric.outputCurrent"), data: mppt ? filterByTimeRange(mppt.outputCurrent, rangeMs) : [], color: SERIES_COLORS.primary() }]} unit="A" {...axisFor("mppt.outputCurrent")} description={t(metricNoteKey("mppt.outputCurrent"))} seriesIntervalMs={telemetry?.seriesIntervalMs} timeRange={timeRange} loading={chartsLoading} empty={noTel} error={histFailed} />
            </ChartCard>
          </div>
        </TabsContent>

        {/* ── 3. AC & Energy ── */}
        <TabsContent value="energy" className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
            <span className="text-xs text-muted-foreground">{t("deviceDetails.device.acMeter")}</span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <TelemetryCard label={t("deviceDetails.metric.phaseAVoltage")} value={latestTel?.pzem?.phaseAVoltage ?? null} unit="V" decimals={1} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.phaseBVoltage")} value={latestTel?.pzem?.phaseBVoltage ?? null} unit="V" decimals={1} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.phaseCVoltage")} value={latestTel?.pzem?.phaseCVoltage ?? null} unit="V" decimals={1} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.phaseACurrent")}  value={latestTel?.pzem?.phaseACurrent ?? null} unit="A" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.phaseBCurrent")}  value={latestTel?.pzem?.phaseBCurrent ?? null} unit="A" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.phaseCCurrent")}  value={latestTel?.pzem?.phaseCCurrent ?? null} unit="A" decimals={2} loading={loading} dataAge={latestTel?.dataAge} />
            <TelemetryCard label={t("deviceDetails.metric.energy")}       value={latestTel?.pzem?.energy        ?? null} unit="kWh" decimals={3} loading={loading} dataAge={latestTel?.dataAge} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ChartCard title={t("deviceDetails.chart.acVoltages")}>
              <TelemetryChart
                title={t("deviceDetails.metric.acVoltages")}
                datasets={[
                  { label: t("deviceDetails.phase.a"), data: pzem ? filterByTimeRange(pzem.phaseAVoltage, rangeMs) : [], color: SERIES_COLORS.primary() },
                  { label: t("deviceDetails.phase.b"), data: pzem ? filterByTimeRange(pzem.phaseBVoltage, rangeMs) : [], color: SERIES_COLORS.secondary() },
                  { label: t("deviceDetails.phase.c"), data: pzem ? filterByTimeRange(pzem.phaseCVoltage, rangeMs) : [], color: SERIES_COLORS.tertiary() },
                ]}
                unit="V"
                {...axisFor("pzem.phaseAVoltage")}
                description={t(chartNoteKey("voltage.ac"))}
                seriesIntervalMs={telemetry?.seriesIntervalMs}
                timeRange={timeRange}
                loading={chartsLoading}
                empty={noTel}
                error={histFailed}
              />
            </ChartCard>

            <ChartCard title={t("deviceDetails.chart.energyTrend")}>
              <TelemetryChart
                title={t("deviceDetails.metric.energy")}
                datasets={[{ label: t("deviceDetails.metric.energy"), data: pzem ? filterByTimeRange(pzem.energy, rangeMs) : [], color: SERIES_COLORS.primary() }]}
                unit="kWh"
                {...axisFor("pzem.energy")}
                description={t(metricNoteKey("pzem.energy"))}
                seriesIntervalMs={telemetry?.seriesIntervalMs}
                timeRange={timeRange}
                fill={true}
                loading={chartsLoading}
                empty={noTel}
                error={histFailed}
              />
            </ChartCard>
          </div>
        </TabsContent>

        {/* ── 4. Diagnostics & Modbus Configuration ── */}
        <TabsContent value="diagnostics" className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Modbus Network Map */}
            <Card>
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground font-sans">
                  {t("deviceDetails.card.modbus")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0 space-y-0">
                <InfoRow label={t("deviceDetails.field.serialInterface")}>{t("deviceDetails.value.serialInterface")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.baudRate")}>{t("deviceDetails.value.baudRate")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.mpptController")}>{t("deviceDetails.value.mpptUnit")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.acMeter")}>{t("deviceDetails.value.meterUnit")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.radiationSensor")}>{t("deviceDetails.value.radiationSensor")}</InfoRow>
              </CardContent>
            </Card>

            {/* Edge System Health & Telemetry Transport */}
            <Card>
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground font-sans">
                  {t("deviceDetails.card.transport")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0 space-y-0">
                <InfoRow label={t("deviceDetails.field.mqttState")}>{mqttStateLabel(mqttRuntimeState)}</InfoRow>
                <InfoRow label={t("deviceDetails.field.cellularSignal")}>{healthSnap?.rssi != null ? `${healthSnap.rssi} dBm` : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.mobileNetwork")}>
                  {routerHealth?.networkState ? String(routerHealth.networkState) : t("state.noData")}
                  {routerHealth?.operator ? <span className="text-muted-foreground"> · {String(routerHealth.operator)}</span> : null}
                </InfoRow>
                <InfoRow label={t("deviceDetails.field.modemTemp")}>{healthSnap?.temperatureC != null ? `${healthSnap.temperatureC} °C` : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.memoryUsage")}>{healthSnap?.ramPercent != null ? `${healthSnap.ramPercent}%` : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.flashUsage")}>{healthSnap?.flashPercent != null ? `${healthSnap.flashPercent}%` : t("state.noData")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.cpuLoad")}>
                  {healthSnap?.cpuPercent != null ? `${healthSnap.cpuPercent}%` : t("state.noData")}
                  {routerHealth?.load1 != null && routerHealth?.cores != null ? (
                    <span className="text-muted-foreground">{t("deviceDetails.value.cpuLoadDetail", { load: String(routerHealth.load1), cores: String(routerHealth.cores) })}</span>
                  ) : null}
                </InfoRow>
                <InfoRow label={t("deviceDetails.field.edgeUptime")}>{formatUptime(healthSnap?.uptimeSeconds ?? null, t("state.noData"))}</InfoRow>
                <InfoRow label={t("deviceDetails.field.senderPeriod")}>{t("deviceDetails.value.senderPeriod")}</InfoRow>
                <InfoRow label={t("deviceDetails.field.healthSource")}>
                  <span className="text-xs text-muted-foreground">
                    {healthStateLabel(healthState)}
                  </span>
                </InfoRow>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* ── 5. Alarms & Events ── */}
        <TabsContent value="alarms" className="space-y-6">
          <div className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-3">
              <h3 className="text-sm font-bold text-foreground">{t("deviceDetails.alarms.active")}</h3>
              <AlarmTable alarms={alarms} loading={loading} />
            </div>

            <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-3">
              <h3 className="text-sm font-bold text-foreground">{t("deviceDetails.alarms.events")}</h3>
              <EventTable events={events} loading={loading} />
            </div>
          </div>
        </TabsContent>

        {/* ── 6. Remote Access ── */}
        <TabsContent value="remote">
          <RemoteAccessCard deviceId={deviceId!} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
