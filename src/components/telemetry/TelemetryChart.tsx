import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import type { TooltipItem } from "chart.js";
import { formatInTimeZone } from "date-fns-tz";
import { TIME_RANGES, tickFormatFor, type TimeRangeKey } from "@/utils/timeRange";
import { CHART_FONT_FAMILY } from "@/utils/chartConfig";
import { detectNoDataSpans, medianSpacing } from "@/lib/chartGaps";
import { useI18n } from "@/i18n/I18nContext";
import LoadingState from "@/components/common/LoadingState";
import EmptyState from "@/components/common/EmptyState";
import { metricEmptyMessage, metricStateLabel } from "@/lib/telemetrySemantics";
import type { MetricState } from "@/types/telemetry";
import ErrorState from "@/components/common/ErrorState";

const TZ = "Asia/Jakarta";

interface TelemetryChartDataset {
  label: string;
  // min/max/count ride along when the backend aggregated the point, so the
  // tooltip can show the range the plotted average stands for.
  data: { ts: number; value: number; min?: number; max?: number; count?: number }[];
  color: string;
}

interface TelemetryChartProps {
  title: string;
  // One short line under the canvas saying what the chart computes, accumulates
  // or measures. The card title names the metric; this explains the shape.
  description?: string;
  datasets: TelemetryChartDataset[];
  unit: string;
  yAxisLabel?: string;
  min?: number;
  max?: number;
  timeRange: TimeRangeKey;
  // Explicit selected window. When present it becomes the x-axis domain, so a
  // partial or sparse series still reads against the window the operator asked
  // for rather than the extent of whatever came back.
  timeWindow?: { fromMs: number; toMs: number };
  fill?: boolean;
  loading?: boolean;
  empty?: boolean;
  error?: boolean;
  // Plot interval the backend used for this series. It is stated under the chart
  // (so an aggregate is never read as raw samples) and lets the tooltip mark the
  // bucket that is still collecting.
  seriesIntervalMs?: number;
  // why this metric has no samples. Without it an unintegrated metric
  // falls back to the generic "no data in range" wording, which is the exact
  // misreport the state this exists to remove.
  metricState?: MetricState;
}

export default function TelemetryChart({
  datasets,
  unit,
  timeRange,
  timeWindow,
  fill,
  loading,
  empty,
  error,
  min,
  max,
  metricState = "no_data_in_range",
  yAxisLabel,
  seriesIntervalMs,
  description,
}: TelemetryChartProps) {
  const { t } = useI18n();
  const plottedPoints = datasets.reduce((total, ds) => total + ds.data.length, 0);

  // A band gauge normally auto-scales, but a series that never leaves one value
  // gives Chart.js nothing to scale to and it pads a flat zero line into a
  // negative axis (-1 to 1), which reads as negative current. Flat and
  // non-negative pins the floor at zero instead.
  const flatNonNegative = useMemo(() => {
    let low = Number.POSITIVE_INFINITY;
    let high = Number.NEGATIVE_INFINITY;
    for (const ds of datasets) {
      for (const point of ds.data) {
        if (!Number.isFinite(point.value)) continue;
        if (point.value < low) low = point.value;
        if (point.value > high) high = point.value;
      }
    }
    return Number.isFinite(low) && low >= 0 && high - low < 1e-9;
  }, [datasets]);
  const axisMin = min !== undefined ? min : flatNonNegative ? 0 : undefined;

  // Gaps are measured per chart against the requested window; the threshold
  // follows the trace's own cadence so ordinary spacing never counts as a gap.
  const gapSpans = useMemo(() => {
    if (!timeWindow || plottedPoints === 0) return [];
    const primary = datasets[0]?.data ?? [];
    const cadence = medianSpacing(primary, 60_000);
    return detectNoDataSpans(primary, timeWindow, Math.max(cadence * 2.5, 120_000));
  }, [datasets, timeWindow, plottedPoints]);

  const chartData = useMemo(
    () => ({
      datasets: datasets.map((ds) => {
        const color = ds.color;
        const isCssVar = color.startsWith("var(");
        const bgColor = isCssVar ? `color-mix(in srgb, ${color} 15%, transparent)` : color + "1A";
        // Chronological order is a charting invariant, not a backend promise:
        // out-of-order history must not draw a zig-zag line.
        const sorted = [...ds.data].sort((a, b) => a.ts - b.ts);
        // Chart.js joins consecutive points whatever the distance between them, so
        // a sensor with an eleven-day hole would be drawn as one straight line
        // across it: an implied reading that was never received. Break the line
        // wherever two samples are further apart than the same threshold that
        // paints the no-data band, so the trace stops exactly where the band says
        // nothing arrived.
        const cadence = medianSpacing(sorted, 60_000);
        const gapThresholdMs = Math.max(cadence * 2.5, 120_000);
        const data: { x: number; y: number | null }[] = [];
        let previousTs: number | null = null;
        for (const point of sorted) {
          if (previousTs !== null && point.ts - previousTs > gapThresholdMs) {
            data.push({ x: previousTs + 1, y: null });
          }
          data.push({ x: point.ts, y: point.value });
          previousTs = point.ts;
        }
        return {
          label: ds.label,
          data,
          borderColor: color,
          backgroundColor: bgColor,
          fill: fill ?? false,
          // Straight segments: a smoothed curve bulges past the samples it joins,
          // which both invents readings (a zero-baseline series can visibly dip
          // below zero) and pushes a peak above the axis top until the canvas
          // cuts it off.
          tension: 0,
          spanGaps: false,
          // Lines read cleanly at any density: no markers on the curve, the
          // tooltip still highlights the hovered sample.
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHitRadius: 8,
          borderWidth: 2,
        };
      }),
    }),
    [datasets, fill, timeWindow],
  );

  // Tick granularity follows the selected window, not a fixed format: a 15 min
  // window wants clock time, a multi-day window must carry the date.
  const tickFormat = tickFormatFor(
    timeWindow ? timeWindow.toMs - timeWindow.fromMs : TIME_RANGES[timeRange].ms,
  );

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false as const,
      parsing: false as const,
      interaction: {
        mode: "index" as const,
        intersect: false,
      },
      plugins: {
        legend: {
          display: datasets.length > 1,
          position: "top" as const,
          align: "end" as const,
          labels: {
            boxWidth: 12,
            boxHeight: 12,
            usePointStyle: true,
            font: {
              family: CHART_FONT_FAMILY,
              size: 11,
              weight: 500,
            },
          },
        },
        tooltip: {
          callbacks: {
            title: (items: TooltipItem<"line">[]) => {
              const at = items[0].parsed.x ?? 0;
              const label = formatInTimeZone(at, TZ, "dd MMM yyyy HH:mm");
              // The newest bucket is still collecting, so its value is a running
              // average rather than a finished interval.
              return seriesIntervalMs && at + seriesIntervalMs > Date.now() ? `${label} · ${t("chart.partial")}` : label;
            },
            label: (ctx: TooltipItem<"line">) => {
              const point = ctx.dataset.data[ctx.dataIndex] as { min?: number; max?: number } | undefined;
              const plotted = ctx.parsed.y;
              const value = (plotted ?? 0).toFixed(2);
              // The line plots the interval average, so a peak inside the bucket
              // is only visible here: state the range the average stands for.
              if (plotted != null && point && point.min != null && point.max != null && point.min !== point.max && (point.min < plotted || point.max > plotted)) {
                return `${ctx.dataset.label}: ${value} ${unit} · range ${point.min} to ${point.max}`;
              }
              return `${ctx.dataset.label}: ${value} ${unit}`;
            },
          },
        },
        decimation: {
          // Safety net for windows that still exceed the canvas; the backend
          // already sizes series to the panel. min-max keeps spikes visible,
          // which is the failure mode that matters for an operations chart.
          enabled: true,
          algorithm: "min-max" as const,
        },
      },
      scales: {
        x: {
          type: "time" as const,
          min: timeWindow?.fromMs,
          max: timeWindow?.toMs,
          time: {
            unit: TIME_RANGES[timeRange].unit,
            minUnit: TIME_RANGES[timeRange].minUnit,
            tooltipFormat: "dd MMM yyyy HH:mm",
          },
          adapters: { date: {} },
          grid: {
            color: "rgba(128, 128, 128, 0.12)",
          },
          ticks: {
            maxTicksLimit: 8,
            maxRotation: 0,
            font: {
              family: CHART_FONT_FAMILY,
              size: 10,
            },
            callback(value: number | string) {
              const ms = typeof value === "string" ? parseInt(value, 10) : value;
              return formatInTimeZone(ms, TZ, tickFormat);
            },
          },
        },
        y: {
          // The policy's bound is a hard floor, not a hint: a cumulative counter
          // or a produced quantity can never go below zero, and Chart.js would
          // otherwise pad a flat-zero series into a negative axis (-1 to 1),
          // implying a reading that cannot exist. Band gauges with varying data
          // pass nothing and keep auto-scaling.
          min: axisMin,
          max,
          grid: {
            color: "rgba(128, 128, 128, 0.12)",
          },
          ticks: {
            font: {
              family: CHART_FONT_FAMILY,
              size: 10,
            },
          },
          title: {
            display: !!yAxisLabel,
            text: yAxisLabel ?? "",
            font: {
              family: CHART_FONT_FAMILY,
              size: 11,
              weight: "bold" as const,
            },
          },
        },
      },
    }),
    [timeRange, unit, axisMin, max, yAxisLabel, timeWindow, tickFormat, gapSpans, seriesIntervalMs],
  );

  // The declared state describes the metric overall; the chart only draws what
  // survives the selected window. Claiming "DATA AVAILABLE" above an empty plot
  // is the contradiction this resolves: no plotted points means the window has
  // no data, whatever the metric's overall state says. `not_integrated` and
  // `error` are stronger facts and are kept as declared.
  const plotState: MetricState = plottedPoints > 0
    ? "data"
    : metricState === "not_integrated" || metricState === "error"
      ? metricState
      : "no_data_in_range";

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={t("chart.loadFailed")} />;
  if (empty || plottedPoints === 0)
    return (
      <EmptyState
        message={metricEmptyMessage(plotState)}
        label={metricStateLabel(plotState)}
      />
    );

  return (
    <div>
      <div className="relative h-[240px]">
        <Line data={chartData} options={options} />
      </div>
      {description ? (
        <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}
