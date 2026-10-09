import type { Dict } from "../types";

/**
 * Historical reports page: period presets, metric selection, per-metric charts,
 * summary KPIs, record table, and the export actions. Units (W, A, V, kWh, W/m2)
 * and the CSV/XLSX header lines are data, not copy, so they stay out of the
 * dictionary.
 */
const reports: Dict = {
  "reports.title": "Reports & Analytics",
  "reports.subtitle": "Historical telemetry extraction with per-metric charts and Excel, CSV or PDF output",

  // Query form
  "reports.filter.device": "Device",
  "reports.filter.devicePlaceholder": "Select Device",
  "reports.filter.range": "Period",
  "reports.filter.metrics": "Metrics",
  "reports.filter.metricsSelected": "{count} selected",
  "reports.filter.startDate": "Start Date",
  "reports.filter.endDate": "End Date",

  // Period presets
  "reports.range.daily": "Daily (today)",
  "reports.range.weekly": "Weekly (this week)",
  "reports.range.monthly": "Monthly (this month)",
  "reports.range.yearly": "Yearly (this year)",
  "reports.range.custom": "Custom range",

  // Metric groups
  "reports.category.mppt": "Solar DC and MPPT",
  "reports.category.pzem": "AC 3-phase and energy",
  "reports.category.solar": "Irradiance",

  // Actions
  "reports.action.querying": "Querying…",
  "reports.action.generate": "Generate",
  "reports.action.exportCsv": "Export CSV",
  "reports.action.exportXlsx": "Export Excel",
  "reports.action.print": "Print or PDF",

  // States and errors
  "reports.error.dateRange": "Start date must be before or equal to end date.",
  "reports.error.noMetric": "Select at least one metric.",
  "reports.error.fetch": "Failed to fetch telemetry history for the selected device.",
  "reports.truncated": "Query returned capped span ({limit} samples limit). Displaying authoritative telemetry records.",
  "reports.empty.initial": "Select a device, a period and the metrics to query historical records.",
  "reports.empty.noPoints": "No telemetry points found for the selected device and parameters.",

  // Summary KPIs
  "reports.kpi.samples": "Samples",
  "reports.kpi.minimum": "Minimum",
  "reports.kpi.startCounter": "Start Counter",
  "reports.kpi.maximum": "Maximum",
  "reports.kpi.endCounter": "End Counter",
  "reports.kpi.average": "Average",
  "reports.kpi.periodDelta": "Period Delta",

  // Per-metric summary (multi-metric selection)
  "reports.summary.title": "Per-metric summary",
  "reports.summary.metric": "Metric",
  "reports.summary.samples": "Samples",
  "reports.summary.min": "Min",
  "reports.summary.avg": "Avg / delta",
  "reports.summary.max": "Max",

  // Record table
  "reports.table.summaryMulti": "{metrics} metrics · {count} rows",
  "reports.table.dateTime": "Date & Time (WIB)",

  // Metric names (units are appended at the call site)
  "reports.metric.pvInputPower": "PV Input Power",
  "reports.metric.outputPower": "MPPT Output Power",
  "reports.metric.pvVoltage": "PV Array Voltage",
  "reports.metric.pvCurrent": "PV Array Current",
  "reports.metric.outputVoltage": "MPPT Output Voltage",
  "reports.metric.outputCurrent": "MPPT Output Current",
  "reports.metric.phaseAVoltage": "Phase A Voltage",
  "reports.metric.phaseBVoltage": "Phase B Voltage",
  "reports.metric.phaseCVoltage": "Phase C Voltage",
  "reports.metric.phaseACurrent": "Phase A Current",
  "reports.metric.phaseBCurrent": "Phase B Current",
  "reports.metric.phaseCCurrent": "Phase C Current",
  "reports.metric.energy": "Accumulated Energy",
  "reports.metric.radiation": "Solar Irradiance",
};

export default reports;
