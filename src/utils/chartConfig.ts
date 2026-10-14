import {
  Chart,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import "chartjs-adapter-date-fns";

// Canvas text cannot inherit CSS, so chart axes, legends and tooltips name the
// interface font explicitly; keep this string in step with --font-sans in index.css.
export const CHART_FONT_FAMILY = '"Inter", sans-serif';

// react-chartjs-2 v5.3.1 <Line> calls Chart.register(LineController) automatically
// via createTypedChart('line', LineController). Do NOT register LineController here.
Chart.register(LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend, Filler);

export interface NoDataBandOptions {
  spans: ReadonlyArray<{ fromMs: number; toMs: number }>;
  fill?: string;
}

/**
 * Paints the parts of the requested window that have no samples. A trace that
 * only covers part of its window is honest but looks broken without this: the
 * band states "nothing was received here" in the chart itself.
 */
export const noDataBandsPlugin = {
  id: "noDataBands",
  beforeDatasetsDraw(chart: Chart, _args: unknown, options: NoDataBandOptions) {
    const spans = options?.spans ?? [];
    if (!spans.length) return;
    const x = chart.scales?.x;
    const area = chart.chartArea;
    if (!x || !area) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.fillStyle = options.fill ?? "color-mix(in srgb, var(--muted-foreground) 12%, transparent)";
    for (const span of spans) {
      const a = x.getPixelForValue(span.fromMs);
      const b = x.getPixelForValue(span.toMs);
      const left = Math.max(area.left, Math.min(a, b));
      const right = Math.min(area.right, Math.max(a, b));
      if (right - left < 2) continue;
      ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
    }
    ctx.restore();
  },
};

Chart.register(noDataBandsPlugin);

/**
 * Y-axis policy, declared once for every chart on every page.
 *
 * Filled marks and non-negative magnitudes start at zero: ONS guidance is that
 * anything drawing from the axis (bar, area) must, and a produced quantity is
 * read as "how much of the day's yield is this". Gauges that live inside a band
 * must not, or 220 V of grid voltage renders as a dead flat line. The previous
 * per-page `min: 0` props disagreed with each other for the same physical
 * quantity, which is what made two voltage charts look unrelated.
 *
 * Add a path here when it gets charted (temperatures, dBm and percentages
 * belong in BAND_GAUGES / a 0-100 bound respectively).
 */
const BAND_GAUGES = new Set<string>([
  "mppt.pvVoltage",
  "mppt.outputVoltage",
  "mppt.pvCurrent",
  "mppt.outputCurrent",
  "pzem.phaseAVoltage",
  "pzem.phaseBVoltage",
  "pzem.phaseCVoltage",
  "pzem.phaseACurrent",
  "pzem.phaseBCurrent",
  "pzem.phaseCCurrent",
]);

export interface AxisBounds {
  min?: number;
  max?: number;
}

export function axisFor(metricPath: string): AxisBounds {
  return BAND_GAUGES.has(metricPath) ? {} : { min: 0 };
}

/**
 * Chart.js paints on a canvas and cannot parse `var(--token)`, so theme colours
 * must be resolved to a concrete value. Read at call time: the stylesheet may not
 * be applied when a module is first evaluated.
 */
export function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Series colours carry no state: ink and two greys, darkest first, so a phase
 *  cannot be confused with a severity colour and the order survives greyscale. */
export const SERIES_COLORS = {
  primary: () => cssVar("--chart-2", "#1E1E1E"),
  secondary: () => cssVar("--chart-3", "#5E5A52"),
  tertiary: () => cssVar("--chart-4", "#A89F8F"),
};

/**
 * Chart descriptions live in src/i18n (en/id) keyed by the metric path itself,
 * so a description can never drift from the chart it belongs to:
 * `chart.metric.<metricPath>` for a single-series canvas and
 * `chart.scope.<chartKey>` for a comparison canvas (every series, never one).
 */
export function metricNoteKey(metricPath: string): string {
  return `chart.metric.${metricPath}`;
}

export function chartNoteKey(chartKey: string): string {
  return `chart.scope.${chartKey}`;
}
