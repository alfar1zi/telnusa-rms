import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { TextureButton } from "@/components/ui/texture-button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getDevices, getDeviceTelemetry } from "@/services/api";
import type { Device } from "@/types/device";
import type { DeviceTelemetry } from "@/types/telemetry";
import { TIME_RANGES, filterByTimeRange, formatDurationMs, resolveWindow, type TimeWindow, type TimeRangeKey } from "@/utils/timeRange";
import PageHeader from "@/components/layout/PageHeader";
import TelemetryChart from "@/components/telemetry/TelemetryChart";
import TimeRangeSelector from "@/components/telemetry/TimeRangeSelector";
import ErrorState from "@/components/common/ErrorState";
import { metricState } from "@/lib/telemetrySemantics";
import { siteLabel } from "@/lib/deviceSemantics";
import { useI18n } from "@/i18n/I18nContext";
import { axisFor, chartNoteKey, metricNoteKey } from "@/utils/chartConfig";
import { Sun, Zap, Info } from "lucide-react";

interface ChartDef {
  titleKey: string;
  unit: string;
  color: string;
  metricPath: string;
  getData: (tel: DeviceTelemetry) => DeviceTelemetry["mppt"]["pvVoltage"];
  fill?: boolean;
  group: "mppt" | "pzem" | "solar";
}

const CHART_DEFS: ChartDef[] = [
  // MPPT DC Conversion & Instantaneous Power
  { titleKey: "monitoring.metric.pvInputPower",      unit: "W",   color: "#38BDF8", metricPath: "mppt.pvInputPower",  getData: (t) => t.mppt.pvInputPower ?? [], group: "mppt" },
  { titleKey: "monitoring.metric.mpptOutputPower",   unit: "W",   color: "#22C55E", metricPath: "mppt.outputPower",   getData: (t) => t.mppt.outputPower ?? [], group: "mppt" },
  { titleKey: "monitoring.metric.pvVoltage",         unit: "V",   color: "#0EA5E9", metricPath: "mppt.pvVoltage",     getData: (t) => t.mppt.pvVoltage, group: "mppt" },
  { titleKey: "monitoring.metric.pvCurrent",         unit: "A",   color: "#818CF8", metricPath: "mppt.pvCurrent",     getData: (t) => t.mppt.pvCurrent, group: "mppt" },
  { titleKey: "monitoring.metric.mpptOutputVoltage", unit: "V",   color: "#14B8A6", metricPath: "mppt.outputVoltage", getData: (t) => t.mppt.outputVoltage, group: "mppt" },
  { titleKey: "monitoring.metric.mpptOutputCurrent", unit: "A",   color: "#EC4899", metricPath: "mppt.outputCurrent", getData: (t) => t.mppt.outputCurrent, group: "mppt" },

  // PZEM AC Grid & 3-Phase
  { titleKey: "monitoring.metric.phaseAVoltage",     unit: "V",   color: "#EF4444", metricPath: "pzem.phaseAVoltage", getData: (t) => t.pzem.phaseAVoltage, group: "pzem" },
  { titleKey: "monitoring.metric.phaseBVoltage",     unit: "V",   color: "#F59E0B", metricPath: "pzem.phaseBVoltage", getData: (t) => t.pzem.phaseBVoltage, group: "pzem" },
  { titleKey: "monitoring.metric.phaseCVoltage",     unit: "V",   color: "#3B82F6", metricPath: "pzem.phaseCVoltage", getData: (t) => t.pzem.phaseCVoltage, group: "pzem" },
  { titleKey: "monitoring.metric.phaseACurrent",     unit: "A",   color: "#F97316", metricPath: "pzem.phaseACurrent", getData: (t) => t.pzem.phaseACurrent, group: "pzem" },
  { titleKey: "monitoring.metric.phaseBCurrent",     unit: "A",   color: "#10B981", metricPath: "pzem.phaseBCurrent", getData: (t) => t.pzem.phaseBCurrent, group: "pzem" },
  { titleKey: "monitoring.metric.phaseCCurrent",     unit: "A",   color: "#8B5CF6", metricPath: "pzem.phaseCCurrent", getData: (t) => t.pzem.phaseCCurrent, group: "pzem" },
  { titleKey: "monitoring.metric.accumulatedEnergy", unit: "kWh", color: "#16A34A", metricPath: "pzem.energy",        getData: (t) => t.pzem.energy, fill: true, group: "pzem" },

  // Solar Irradiance (Honde RD-PSR-01): W/m², 1:1 from the sensor register.
  { titleKey: "monitoring.metric.solarRadiation",    unit: "W/m²", color: "#FACC15", metricPath: "solar.radiation",   getData: (t) => t.solar?.radiation ?? [], group: "solar" },
];
export default function Monitoring() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const paramDeviceId = searchParams.get("device") || searchParams.get("deviceId");
  const [deviceId, setDeviceId] = useState(paramDeviceId || "");
  const [timeRange, setTimeRange] = useState<TimeRangeKey>("24h");
  const [viewMode, setViewMode] = useState<"workspace" | "metrics">("workspace");
  const [devices, setDevices] = useState<Device[]>([]);
  const [telemetry, setTelemetry] = useState<DeviceTelemetry | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDevices()
      .then((devs) => {
        setDevices(devs);
        if (devs.length > 0) {
          if (paramDeviceId && devs.some((d) => d.id === paramDeviceId)) {
            setDeviceId(paramDeviceId);
          } else if (!deviceId) {
            setDeviceId(devs[0].id);
          }
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : t("monitoring.error.devices"));
        setLoading(false);
      });
  }, [paramDeviceId]);

  const [window, setWindow] = useState<TimeWindow | null>(null);
  const [reload, setReload] = useState(0);
  const activeRequestIdRef = useRef(0);

  useEffect(() => {
    if (!deviceId) {
      setLoading(false);
      return;
    }
    const reqId = ++activeRequestIdRef.current;
    let cancelled = false;
    const w = resolveWindow(timeRange, Date.now());
    setWindow(w);
    setTelemetry(undefined);
    setLoading(true);
    setError(null);
    getDeviceTelemetry(deviceId, { from: w.fromMs, to: w.toMs, range: timeRange })
      .then((tel) => {
        if (cancelled || reqId !== activeRequestIdRef.current) return;
        setTelemetry(tel);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled || reqId !== activeRequestIdRef.current) return;
        setError(err instanceof Error ? err.message : t("monitoring.error.telemetry"));
        setLoading(false);
      });
    return () => {
      cancelled = true;
      if (activeRequestIdRef.current === reqId) {
        activeRequestIdRef.current++;
      }
    };
  }, [deviceId, timeRange, reload]);

  const rangeMs = TIME_RANGES[timeRange].ms;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("header.title.monitoring")}
        subtitle={t("monitoring.subtitle")}
      />

      <div className="flex flex-wrap items-center justify-between gap-3 bg-card border border-border/70 p-3 rounded-lg shadow-xs">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("monitoring.deviceLabel")}</span>
          <Select
            value={deviceId}
            onValueChange={(newId) => {
              setDeviceId(newId);
              setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set("device", newId);
                return next;
              });
            }}
          >
            <SelectTrigger className="w-[200px] text-xs font-medium bg-background">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {devices.map((d) => <SelectItem key={d.id} value={d.id} className="text-xs">{siteLabel(d.siteName, d.siteId)} <span className="text-muted-foreground tabular-nums">{d.id}</span></SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5">
            <TextureButton
              type="button"
              size="sm"
              variant={viewMode === "workspace" ? "primary" : "minimal"}
              onClick={() => setViewMode("workspace")}
            >
              {t("monitoring.view.workspace")}
            </TextureButton>
            <TextureButton
              type="button"
              size="sm"
              variant={viewMode === "metrics" ? "primary" : "minimal"}
              onClick={() => setViewMode("metrics")}
            >
              {t("monitoring.view.metrics")}
            </TextureButton>
          </div>
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
        </div>
      </div>
      {/* Energy & Resolution Status Bar */}
      {telemetry ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Card className="p-3 shadow-2xs">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t("monitoring.kpi.todayEnergy")}</span>
            <div className="text-lg font-semibold tracking-tight text-foreground mt-0.5 tabular-nums">
              {telemetry.energySummary?.todayKwh != null ? `${telemetry.energySummary.todayKwh.toFixed(2)} kWh` : "NO DATA"}
            </div>
            <span className="text-[10px] text-muted-foreground">{t("monitoring.kpi.todayEnergyNote")}</span>
          </Card>

          <Card className="p-3 shadow-2xs">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t("monitoring.kpi.periodDelta")}</span>
            <div className="text-lg font-semibold tracking-tight text-foreground mt-0.5 tabular-nums">
              {telemetry.energySummary?.periodDeltaKwh != null ? `${telemetry.energySummary.periodDeltaKwh.toFixed(2)} kWh` : "NO DATA"}
            </div>
            <span className="text-[10px] text-muted-foreground">{t("monitoring.kpi.periodDeltaNote", { range: timeRange })}</span>
          </Card>

          <Card className="p-3 shadow-2xs">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t("monitoring.kpi.accumulated")}</span>
            <div className="text-lg font-semibold tracking-tight text-foreground mt-0.5 tabular-nums">
              {telemetry.energySummary?.accumulatedKwh != null ? `${telemetry.energySummary.accumulatedKwh.toFixed(2)} kWh` : "NO DATA"}
            </div>
            <span className="text-[10px] text-muted-foreground">{t("monitoring.kpi.accumulatedNote")}</span>
          </Card>

          <Card className="p-3 shadow-2xs">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t("monitoring.kpi.resolutionQuality")}</span>
            <div className="flex items-center gap-1.5 mt-1">
              <span className="text-xs font-semibold uppercase px-1.5 py-0.5 bg-muted rounded">
                {telemetry.resolution ?? "NO DATA"}
              </span>
              <span className="text-[10px] font-medium text-muted-foreground">
                {(telemetry.window?.windowTimeCoverageRatio ?? telemetry.window?.coverageRatio) != null ? `${(((telemetry.window?.windowTimeCoverageRatio ?? telemetry.window?.coverageRatio) ?? 0) * 100).toFixed(0)}% ${t("monitoring.kpi.coverageAbbrev")}` : ""}
              </span>
            </div>
            <span className="text-[10px] text-muted-foreground mt-0.5 block">
              {t("monitoring.kpi.quality", { quality: telemetry.energySummary?.quality || "VALID" })}
            </span>
          </Card>
        </div>
      ) : null}

      {error ? (
        <ErrorState
                hint={t("common.contactAdmin")}
          title={t("monitoring.error.title")}
          message={error}
          onRetry={() => { if (deviceId) setReload((n) => n + 1); }}
        />
      ) : null}

      {telemetry?.window && !telemetry.window.complete && telemetry.window.dataPoints ? (
        <div className="flex items-center gap-2 rounded-md border border-border/80 bg-muted/30 px-3.5 py-2 text-xs text-muted-foreground">
          <Info className="size-4 shrink-0 text-muted-foreground" />
          <span>
            {t("monitoring.banner.head", {
              duration: formatDurationMs(telemetry.window.observedDuration ?? telemetry.window.availableDuration ?? 0),
              range: timeRange,
              coverage: ((telemetry.window.windowTimeCoverageRatio ?? telemetry.window.coverageRatio ?? 0) * 100).toFixed(1),
            })}
            {telemetry.window.sampleCoverageRatio != null && telemetry.window.sampleCoverageRatio !== (telemetry.window.windowTimeCoverageRatio ?? telemetry.window.coverageRatio) ? t("monitoring.banner.sampleCoverage", { coverage: (telemetry.window.sampleCoverageRatio * 100).toFixed(1) }) : ""}
            {telemetry.window.hasGaps && telemetry.window.gapCount ? t(telemetry.window.gapCount > 1 ? "monitoring.banner.gaps" : "monitoring.banner.gap", { count: telemetry.window.gapCount }) : ""}
            {telemetry.window.truncated ? t("monitoring.banner.truncated") : ""}
            {t("monitoring.banner.tail")}
          </span>
        </div>
      ) : null}

      {viewMode === "workspace" ? (
        /* Operational Workspace View: Consolidated High-Density Charts */
        <div className="space-y-6">
          {/* Group 1: Solar & MPPT Conversion */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Sun className="size-4 text-[var(--chart-1)]" />
            <h2 className="text-sm font-bold text-foreground uppercase tracking-wider font-sans">
              {t("monitoring.section.solarDc")}
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Power Conversion (PV In vs MPPT Out) */}
            <Card className="flex flex-col justify-between shadow-xs">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.chart.powerDc")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(W)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.chart.powerDc")}
                  datasets={[
                    {
                      label: t("monitoring.metric.pvInputPower"),
                      data: telemetry && window ? filterByTimeRange(telemetry.mppt.pvInputPower ?? [], rangeMs, window.toMs) : [],
                      color: "#38BDF8",
                    },
                    {
                      label: t("monitoring.metric.mpptOutputPower"),
                      data: telemetry && window ? filterByTimeRange(telemetry.mppt.outputPower ?? [], rangeMs, window.toMs) : [],
                      color: "#22C55E",
                    },
                  ]}
                  unit="W"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  {...axisFor("mppt.pvInputPower")}
                  description={t(chartNoteKey("power.dc"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  seriesIntervalMs={telemetry?.seriesIntervalMs}
                  metricState={metricState(telemetry?.metricState, "mppt.pvInputPower", (telemetry?.mppt.pvInputPower ?? []).length)}
                />
              </CardContent>
            </Card>

            {/* DC Voltages Comparison */}
            <Card className="flex flex-col justify-between shadow-xs">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.chart.voltageDc")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(V)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.chart.voltageDc")}
                  datasets={[
                    {
                      label: t("monitoring.metric.pvVoltage"),
                      data: telemetry && window ? filterByTimeRange(telemetry.mppt.pvVoltage ?? [], rangeMs, window.toMs) : [],
                      color: "#0EA5E9",
                    },
                    {
                      label: t("monitoring.metric.mpptOutputVoltage"),
                      data: telemetry && window ? filterByTimeRange(telemetry.mppt.outputVoltage ?? [], rangeMs, window.toMs) : [],
                      color: "#14B8A6",
                    },
                  ]}
                  unit="V"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  {...axisFor("mppt.pvVoltage")}
                  description={t(chartNoteKey("voltage.dc"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  seriesIntervalMs={telemetry?.seriesIntervalMs}
                  metricState={metricState(telemetry?.metricState, "mppt.pvVoltage", (telemetry?.mppt.pvVoltage ?? []).length)}
                />
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Group 2: AC 3-Phase Grid Subsystem */}
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Zap className="size-4 text-foreground" />
            <h2 className="text-sm font-bold text-foreground uppercase tracking-wider font-sans">
              {t("monitoring.section.acThreePhase")}
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* 3-Phase Voltage */}
            <Card className="flex flex-col justify-between shadow-xs">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.chart.voltageAc")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(V)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.chart.voltageAc")}
                  datasets={[
                    {
                      label: t("monitoring.metric.phaseAVoltage"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseAVoltage ?? [], rangeMs, window.toMs) : [],
                      color: "#EF4444",
                    },
                    {
                      label: t("monitoring.metric.phaseBVoltage"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseBVoltage ?? [], rangeMs, window.toMs) : [],
                      color: "#F59E0B",
                    },
                    {
                      label: t("monitoring.metric.phaseCVoltage"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseCVoltage ?? [], rangeMs, window.toMs) : [],
                      color: "#3B82F6",
                    },
                  ]}
                  unit="V"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  {...axisFor("pzem.phaseAVoltage")}
                  description={t(chartNoteKey("voltage.ac"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  metricState={metricState(telemetry?.metricState, "pzem.phaseAVoltage", (telemetry?.pzem.phaseAVoltage ?? []).length)}
                />
              </CardContent>
            </Card>

            {/* 3-Phase Load Current */}
            <Card className="flex flex-col justify-between shadow-xs">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.chart.currentAc")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(A)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.chart.currentAc")}
                  datasets={[
                    {
                      label: t("monitoring.metric.phaseACurrent"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseACurrent ?? [], rangeMs, window.toMs) : [],
                      color: "#F97316",
                    },
                    {
                      label: t("monitoring.metric.phaseBCurrent"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseBCurrent ?? [], rangeMs, window.toMs) : [],
                      color: "#10B981",
                    },
                    {
                      label: t("monitoring.metric.phaseCCurrent"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.phaseCCurrent ?? [], rangeMs, window.toMs) : [],
                      color: "#8B5CF6",
                    },
                  ]}
                  unit="A"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  {...axisFor("pzem.phaseACurrent")}
                  description={t(chartNoteKey("current.ac"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  metricState={metricState(telemetry?.metricState, "pzem.phaseACurrent", (telemetry?.pzem.phaseACurrent ?? []).length)}
                />
              </CardContent>
            </Card>

            {/* Accumulated Energy Counter */}
            <Card className="flex flex-col justify-between shadow-xs md:col-span-2">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.chart.accumulatedEnergy")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(kWh)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.chart.accumulatedEnergy")}
                  datasets={[
                    {
                      label: t("monitoring.metric.accumulatedEnergy"),
                      data: telemetry && window ? filterByTimeRange(telemetry.pzem.energy ?? [], rangeMs, window.toMs) : [],
                      color: "#16A34A",
                    },
                  ]}
                  unit="kWh"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  fill={true}
                  {...axisFor("pzem.energy")}
                  description={t(metricNoteKey("pzem.energy"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  seriesIntervalMs={telemetry?.seriesIntervalMs}
                  metricState={metricState(telemetry?.metricState, "pzem.energy", (telemetry?.pzem.energy ?? []).length)}
                />
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Group 3: Solar Irradiance */}
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Sun className="size-4 text-[var(--chart-1)]" />
            <h2 className="text-sm font-bold text-foreground uppercase tracking-wider font-sans">
              {t("monitoring.metric.solarRadiation")}
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card className="flex flex-col justify-between shadow-xs md:col-span-2">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                  <span>{t("monitoring.metric.solarRadiation")}</span>
                  <span className="text-xs font-medium text-muted-foreground">(W/m²)</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-5 pt-0">
                <TelemetryChart
                  title={t("monitoring.metric.solarRadiation")}
                  datasets={[
                    {
                      label: t("monitoring.metric.solarRadiation"),
                      data: telemetry && window ? filterByTimeRange(telemetry.solar?.radiation ?? [], rangeMs, window.toMs) : [],
                      color: "#FACC15",
                    },
                  ]}
                  unit="W/m²"
                  timeRange={timeRange}
                  timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                  {...axisFor("solar.radiation")}
                  description={t(metricNoteKey("solar.radiation"))}
                  loading={loading}
                  empty={!loading && !telemetry}
                  error={Boolean(error)}
                  seriesIntervalMs={telemetry?.seriesIntervalMs}
                  metricState={metricState(telemetry?.metricState, "solar.radiation", (telemetry?.solar?.radiation ?? []).length)}
                />
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
      ) : (
        /* All Metrics Breakdown View */
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {CHART_DEFS.map(({ titleKey, unit, color, metricPath, getData, fill }) => {
              const title = t(titleKey);
              const data = telemetry && window
                ? filterByTimeRange(getData(telemetry), rangeMs, window.toMs)
                : [];
              return (
                <Card key={titleKey} className="flex flex-col justify-between shadow-xs">
                  <CardHeader className="p-4 sm:p-5 pb-2">
                    <CardTitle className="text-sm font-semibold text-foreground flex items-center justify-between">
                      <span>{title}</span>
                      <span className="text-xs font-medium text-muted-foreground">({unit})</span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 sm:p-5 pt-0">
                    <TelemetryChart
                      title={title}
                      datasets={[{ label: title, data, color }]}
                      unit={unit}
                      timeRange={timeRange}
                      timeWindow={window ? { fromMs: window.fromMs, toMs: window.toMs } : undefined}
                      fill={fill}
                      {...axisFor(metricPath)}
                  description={t(metricNoteKey(metricPath))}
                      loading={loading}
                      empty={!loading && !telemetry}
                      error={Boolean(error)}
                      seriesIntervalMs={telemetry?.seriesIntervalMs}
                      metricState={metricState(telemetry?.metricState, metricPath, data.length)}
                    />
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
