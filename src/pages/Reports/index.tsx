import { useEffect, useMemo, useState } from "react";
import { format, parseISO, startOfWeek } from "date-fns";
import { formatInTimeZone } from "date-fns-tz";
import { ChevronDown, Download, FileSpreadsheet, FileText, Play, Printer } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import PageHeader from "@/components/layout/PageHeader";
import EmptyState from "@/components/common/EmptyState";
import LoadingState from "@/components/common/LoadingState";
import ErrorState from "@/components/common/ErrorState";
import { TextureButton } from "@/components/ui/texture-button";
import TelemetryChart from "@/components/telemetry/TelemetryChart";
import { getDevices, getDeviceTelemetry } from "@/services/api";
import type { Device } from "@/types/device";
import type { DeviceTelemetry, DataPoint } from "@/types/telemetry";
import type { TimeRangeKey } from "@/utils/timeRange";
import { jakartaDayStartMs } from "@/utils/timeRange";
import { axisFor, metricNoteKey, SERIES_COLORS } from "@/utils/chartConfig";
import { formatTimestamp } from "@/utils/format";
import { siteLabel } from "@/lib/deviceSemantics";
import { useI18n } from "@/i18n/I18nContext";

type MetricCategory = "mppt" | "pzem" | "solar";

const METRICS: Record<string, { labelKey: string; unit: string; category: MetricCategory }> = {
  pvInputPower:  { labelKey: "reports.metric.pvInputPower",  unit: "W",    category: "mppt" },
  outputPower:   { labelKey: "reports.metric.outputPower",   unit: "W",    category: "mppt" },
  pvVoltage:     { labelKey: "reports.metric.pvVoltage",     unit: "V",    category: "mppt" },
  pvCurrent:     { labelKey: "reports.metric.pvCurrent",     unit: "A",    category: "mppt" },
  outputVoltage: { labelKey: "reports.metric.outputVoltage", unit: "V",    category: "mppt" },
  outputCurrent: { labelKey: "reports.metric.outputCurrent", unit: "A",    category: "mppt" },
  phaseAVoltage: { labelKey: "reports.metric.phaseAVoltage", unit: "V",    category: "pzem" },
  phaseBVoltage: { labelKey: "reports.metric.phaseBVoltage", unit: "V",    category: "pzem" },
  phaseCVoltage: { labelKey: "reports.metric.phaseCVoltage", unit: "V",    category: "pzem" },
  phaseACurrent: { labelKey: "reports.metric.phaseACurrent", unit: "A",    category: "pzem" },
  phaseBCurrent: { labelKey: "reports.metric.phaseBCurrent", unit: "A",    category: "pzem" },
  phaseCCurrent: { labelKey: "reports.metric.phaseCCurrent", unit: "A",    category: "pzem" },
  energy:        { labelKey: "reports.metric.energy",        unit: "kWh",  category: "pzem" },
  radiation:     { labelKey: "reports.metric.radiation",     unit: "W/m²", category: "solar" },
};

const CATEGORY_KEYS: MetricCategory[] = ["mppt", "pzem", "solar"];
const RANGE_PRESETS = ["daily", "weekly", "monthly", "yearly", "custom"] as const;
type RangePreset = (typeof RANGE_PRESETS)[number];

const JAKARTA_TZ = "Asia/Jakarta";
const DAY_MS = 86_400_000;

/** Preset windows end "now" and start at the natural Jakarta boundary for the period. */
function resolvePresetRange(preset: Exclude<RangePreset, "custom">): { from: number; to: number } {
  const now = Date.now();
  const today = formatInTimeZone(new Date(now), JAKARTA_TZ, "yyyy-MM-dd");
  if (preset === "daily") return { from: jakartaDayStartMs(today), to: now };
  if (preset === "weekly") {
    const monday = startOfWeek(parseISO(today), { weekStartsOn: 1 });
    return { from: jakartaDayStartMs(format(monday, "yyyy-MM-dd")), to: now };
  }
  if (preset === "monthly") return { from: jakartaDayStartMs(`${today.slice(0, 7)}-01`), to: now };
  return { from: jakartaDayStartMs(`${today.slice(0, 4)}-01-01`), to: now };
}

/** Nearest existing range key: it only steers tick density, the window itself is exact. */
function axisKeyFor(spanMs: number): TimeRangeKey {
  if (spanMs <= 3_600_000) return "1h";
  if (spanMs <= 6 * 3_600_000) return "6h";
  if (spanMs <= DAY_MS) return "24h";
  if (spanMs <= 7 * DAY_MS) return "7d";
  return "30d";
}

interface MetricRow {
  ts: number;
  values: Record<string, number>;
}

export default function Reports() {
  const { t } = useI18n();
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [metricKeys, setMetricKeys] = useState<string[]>(["pvVoltage"]);
  const [preset, setPreset] = useState<RangePreset>("daily");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [rows, setRows] = useState<MetricRow[] | null>(null);
  const [seriesByMetric, setSeriesByMetric] = useState<Record<string, DataPoint[]>>({});
  const [reportWindow, setReportWindow] = useState<DeviceTelemetry["window"]>(undefined);
  const [seriesIntervalMs, setSeriesIntervalMs] = useState<number | undefined>(undefined);
  const [windowRange, setWindowRange] = useState<{ from: number; to: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  useEffect(() => {
    getDevices().then((devs) => {
      setDevices(devs);
      if (devs.length > 0) setDeviceId(devs[0].id);
    });
  }, []);

  const selectedMetrics = useMemo(
    () => Object.keys(METRICS).filter((k) => metricKeys.includes(k)),
    [metricKeys],
  );

  function toggleMetric(key: string) {
    setMetricKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function customRange(): { from: number; to: number } | null {
    if (!startDate || !endDate) return null;
    const from = jakartaDayStartMs(startDate);
    const to = jakartaDayStartMs(endDate) + DAY_MS - 1;
    return from > to ? null : { from, to };
  }

  async function handleGenerate() {
    if (!deviceId) return;
    if (metricKeys.length === 0) {
      setErrorKey("reports.error.noMetric");
      return;
    }
    let range: { from: number; to: number };
    if (preset === "custom") {
      const custom = customRange();
      if (!custom) {
        setErrorKey("reports.error.dateRange");
        return;
      }
      range = custom;
    } else {
      range = resolvePresetRange(preset);
    }

    setLoading(true);
    setErrorKey(null);
    setReportWindow(undefined);
    try {
      // One request carries every metric: the backend returns the whole contract in
      // a single history build, so asking per metric would repeat the same work.
      const tel = await getDeviceTelemetry(deviceId, { from: range.from, to: range.to });
      setReportWindow(tel?.window);
      setSeriesIntervalMs(tel?.seriesIntervalMs);
      setWindowRange(range);
      if (!tel) {
        setRows([]);
        setSeriesByMetric({});
        return;
      }
      const byMetric: Record<string, DataPoint[]> = {};
      for (const key of selectedMetrics) {
        const section = tel[METRICS[key].category] as unknown as Record<string, DataPoint[]> | undefined;
        byMetric[key] = (section?.[key] ?? []).filter((p) => p.ts >= range.from && p.ts <= range.to);
      }
      const merged = new Map<number, Record<string, number>>();
      for (const [key, points] of Object.entries(byMetric)) {
        for (const point of points) {
          const bucket = merged.get(point.ts) ?? {};
          bucket[key] = point.value;
          merged.set(point.ts, bucket);
        }
      }
      setSeriesByMetric(byMetric);
      setRows([...merged.entries()].sort((a, b) => a[0] - b[0]).map(([ts, values]) => ({ ts, values })));
    } catch {
      setErrorKey("reports.error.fetch");
      setRows(null);
      setSeriesByMetric({});
    } finally {
      setLoading(false);
    }
  }

  function handleExportCsv() {
    if (!rows || rows.length === 0) return;
    const header = ["Timestamp", "ISO Date", ...selectedMetrics.map((k) => `${t(METRICS[k].labelKey)} [${METRICS[k].unit}]`)];
    const lines = rows.map((row) => [
      String(row.ts),
      new Date(row.ts).toISOString(),
      ...selectedMetrics.map((k) => (row.values[k] === undefined ? "" : String(row.values[k]))),
    ].join(","));
    const blob = new Blob([[header.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `telnusa-report-${deviceId}-${format(new Date(), "yyyyMMdd-HHmm")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleExportXlsx() {
    if (!windowRange) return;
    const params = new URLSearchParams({
      deviceId,
      from: String(windowRange.from),
      to: String(windowRange.to),
      metrics: selectedMetrics.join(","),
    });
    // Same-origin download: the session cookie travels with it, no token in the URL.
    window.location.href = `/api/reports/export.xlsx?${params.toString()}`;
  }

  const singleMetric = selectedMetrics.length === 1 ? selectedMetrics[0] : null;
  const NOTE_PATHS: Record<string, string> = {
    pvInputPower: "mppt.pvInputPower", outputPower: "mppt.outputPower",
    pvVoltage: "mppt.pvVoltage", pvCurrent: "mppt.pvCurrent",
    outputVoltage: "mppt.outputVoltage", outputCurrent: "mppt.outputCurrent",
    phaseAVoltage: "pzem.phaseAVoltage", phaseBVoltage: "pzem.phaseBVoltage", phaseCVoltage: "pzem.phaseCVoltage",
    phaseACurrent: "pzem.phaseACurrent", phaseBCurrent: "pzem.phaseBCurrent", phaseCCurrent: "pzem.phaseCCurrent",
    energy: "pzem.energy", radiation: "solar.radiation",
  };
  const summary = useMemo(() => {
    if (!rows || rows.length === 0) return null;
    return selectedMetrics.map((key) => {
      const values = rows.map((r) => r.values[key]).filter((v): v is number => typeof v === "number");
      if (values.length === 0) return { key, samples: 0, min: null, max: null, avg: null, delta: null };
      const min = Math.min(...values);
      const max = Math.max(...values);
      if (key === "energy") {
        let total = 0;
        for (let i = 1; i < values.length; i++) {
          const d = values[i] - values[i - 1];
          if (d >= 0) total += d;
        }
        return { key, samples: values.length, min, max, avg: null, delta: +total.toFixed(4) };
      }
      return {
        key,
        samples: values.length,
        min: +min.toFixed(3),
        max: +max.toFixed(3),
        avg: +(values.reduce((a, v) => a + v, 0) / values.length).toFixed(3),
        delta: null,
      };
    });
  }, [rows, selectedMetrics]);

  const axisKey: TimeRangeKey = useMemo(
    () => (windowRange ? axisKeyFor(windowRange.to - windowRange.from) : "24h"),
    [windowRange],
  );

  const hasData = !loading && !errorKey && rows !== null && rows.length > 0;

  return (
    <div className="space-y-6">
      <PageHeader title={t("reports.title")} subtitle={t("reports.subtitle")} />

      <Card className="shadow-xs print:hidden">
        <CardContent className="p-4 sm:p-5">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[190px]">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1">{t("reports.filter.device")}</span>
              <Select value={deviceId} onValueChange={setDeviceId}>
                <SelectTrigger className="w-full text-xs font-medium"><SelectValue placeholder={t("reports.filter.devicePlaceholder")} /></SelectTrigger>
                <SelectContent>
                  {devices.map((d) => (
                    <SelectItem key={d.id} value={d.id} className="text-xs">{siteLabel(d.siteName, d.siteId)} <span className="text-muted-foreground tabular-nums">{d.id}</span></SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex-1 min-w-[220px]">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1">{t("reports.filter.metrics")}</span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-background px-3 text-xs font-medium text-foreground hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <span className="truncate">{t("reports.filter.metricsSelected", { count: metricKeys.length })}</span>
                    <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[260px]">
                  {CATEGORY_KEYS.map((category, index) => {
                    const entries = Object.entries(METRICS).filter(([, m]) => m.category === category);
                    return (
                      <div key={category}>
                        {index > 0 && <DropdownMenuSeparator />}
                        <DropdownMenuLabel className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          {t(`reports.category.${category}`)}
                        </DropdownMenuLabel>
                        {entries.map(([key, meta]) => (
                          <DropdownMenuCheckboxItem
                            key={key}
                            checked={metricKeys.includes(key)}
                            onSelect={(event) => event.preventDefault()}
                            onCheckedChange={() => toggleMetric(key)}
                            className="text-xs"
                          >
                            {t(meta.labelKey)} ({meta.unit})
                          </DropdownMenuCheckboxItem>
                        ))}
                      </div>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            <div className="w-full sm:w-[170px]">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1">{t("reports.filter.range")}</span>
              <Select value={preset} onValueChange={(v) => setPreset(v as RangePreset)}>
                <SelectTrigger className="w-full text-xs font-medium"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {RANGE_PRESETS.map((p) => (
                    <SelectItem key={p} value={p} className="text-xs">{t(`reports.range.${p}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {preset === "custom" && (
              <>
                <div className="w-full sm:w-[160px]">
                  <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1">{t("reports.filter.startDate")}</span>
                  <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="text-xs" />
                </div>
                <div className="w-full sm:w-[160px]">
                  <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1">{t("reports.filter.endDate")}</span>
                  <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="text-xs" />
                </div>
              </>
            )}

            <div className="flex items-center gap-2">
              <TextureButton variant="primary" onClick={handleGenerate} disabled={loading || !deviceId}>
                <Play className="size-3.5" />
                <span>{loading ? t("reports.action.querying") : t("reports.action.generate")}</span>
              </TextureButton>
              {hasData && (
                <>
                  <TextureButton variant="minimal" onClick={handleExportCsv}>
                    <Download className="size-3.5" />
                    <span>{t("reports.action.exportCsv")}</span>
                  </TextureButton>
                  <TextureButton variant="minimal" onClick={handleExportXlsx}>
                    <FileSpreadsheet className="size-3.5" />
                    <span>{t("reports.action.exportXlsx")}</span>
                  </TextureButton>
                  <TextureButton variant="minimal" onClick={() => window.print()}>
                    <Printer className="size-3.5" />
                    <span>{t("reports.action.print")}</span>
                  </TextureButton>
                </>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {loading && <LoadingState />}
      {errorKey && <ErrorState hint={t("common.contactAdmin")} message={t(errorKey)} />}

      {!loading && !errorKey && reportWindow?.truncated ? (
        <div className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-3.5 py-2.5 text-xs text-muted-foreground">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <span>{t("reports.truncated", { limit: reportWindow.limit ?? 0 })}</span>
        </div>
      ) : null}

      {!loading && !errorKey && rows === null && (
        <EmptyState icon={FileText} message={t("reports.empty.initial")} />
      )}

      {!loading && !errorKey && rows !== null && rows.length === 0 && (
        <EmptyState icon={FileText} message={t("reports.empty.noPoints")} />
      )}

      {hasData && (
        <div className="space-y-4">
          {singleMetric && summary?.[0] && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="rounded-lg border border-border/70 bg-card p-3.5 shadow-xs">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">{t("reports.kpi.samples")}</span>
                <p className="mt-1 text-2xl font-bold tracking-tight tabular-nums text-foreground">{summary[0].samples}</p>
              </div>
              <div className="rounded-lg border border-border/70 bg-card p-3.5 shadow-xs">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {singleMetric === "energy" ? t("reports.kpi.startCounter") : t("reports.kpi.minimum")}
                </span>
                <p className="mt-1 text-2xl font-bold tracking-tight tabular-nums text-foreground">
                  {summary[0].min} <span className="text-xs font-normal text-muted-foreground">{METRICS[singleMetric].unit}</span>
                </p>
              </div>
              <div className="rounded-lg border border-border/70 bg-card p-3.5 shadow-xs">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {singleMetric === "energy" ? t("reports.kpi.endCounter") : t("reports.kpi.maximum")}
                </span>
                <p className="mt-1 text-2xl font-bold tracking-tight tabular-nums text-foreground">
                  {summary[0].max} <span className="text-xs font-normal text-muted-foreground">{METRICS[singleMetric].unit}</span>
                </p>
              </div>
              <div className="rounded-lg border border-border/70 bg-card p-3.5 shadow-xs">
                <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {singleMetric === "energy" ? t("reports.kpi.periodDelta") : t("reports.kpi.average")}
                </span>
                <p className="mt-1 text-2xl font-bold tracking-tight tabular-nums text-foreground">
                  {singleMetric === "energy" ? summary[0].delta : summary[0].avg}{" "}
                  <span className="text-xs font-normal text-muted-foreground">{METRICS[singleMetric].unit}</span>
                </p>
              </div>
            </div>
          )}

          {selectedMetrics.length > 1 && summary && (
            <Card className="shadow-xs">
              <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-sm font-semibold">{t("reports.summary.title")}</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("reports.summary.metric")}</TableHead>
                      <TableHead className="text-right">{t("reports.summary.samples")}</TableHead>
                      <TableHead className="text-right">{t("reports.summary.min")}</TableHead>
                      <TableHead className="text-right">{t("reports.summary.avg")}</TableHead>
                      <TableHead className="text-right">{t("reports.summary.max")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.map((s) => (
                      <TableRow key={s.key}>
                        <TableCell className="text-xs sm:text-sm">{t(METRICS[s.key].labelKey)} <span className="text-muted-foreground">({METRICS[s.key].unit})</span></TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{s.samples}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{s.min ?? t("state.noData")}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{s.key === "energy" ? (s.delta ?? t("state.noData")) : (s.avg ?? t("state.noData"))}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{s.max ?? t("state.noData")}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* One chart per metric: different units never share a scale. */}
          <div className="space-y-4">
            {selectedMetrics.map((key) => (
              <TelemetryChart
                key={key}
                title={`${t(METRICS[key].labelKey)} (${METRICS[key].unit})`}
                description={t(metricNoteKey(NOTE_PATHS[key]))}
                datasets={[{
                  label: t(METRICS[key].labelKey),
                  data: seriesByMetric[key] ?? [],
                  color: SERIES_COLORS.primary(),
                }]}
                unit={METRICS[key].unit}
                {...axisFor(`reports.${key}`)}
                timeRange={axisKey}
                timeWindow={windowRange ? { fromMs: windowRange.from, toMs: windowRange.to } : undefined}
                fill={key.includes("Power") || key === "radiation"}
                seriesIntervalMs={seriesIntervalMs}
                empty={rows !== null && rows.length === 0}
                loading={loading}
              />
            ))}
          </div>

          <Card className="shadow-xs">
            <CardHeader className="p-4 sm:p-5 pb-3">
              <CardTitle className="text-sm font-semibold">
                {t("reports.table.summaryMulti", { metrics: selectedMetrics.length, count: rows.length })}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-[560px]">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[190px]">{t("reports.table.dateTime")}</TableHead>
                      {selectedMetrics.map((key) => (
                        <TableHead key={key} className="text-right whitespace-nowrap">
                          {t(METRICS[key].labelKey)} ({METRICS[key].unit})
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((row) => (
                      <TableRow key={row.ts}>
                        <TableCell className="text-xs sm:text-sm tabular-nums">{formatTimestamp(row.ts)}</TableCell>
                        {selectedMetrics.map((key) => (
                          <TableCell key={key} className="text-right text-xs sm:text-sm tabular-nums">
                            {row.values[key] === undefined ? t("state.noData") : row.values[key]}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
