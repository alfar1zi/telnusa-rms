import type { Dict } from "../types";

/**
 * Monitoring page: telemetry analytics console.
 *
 * Chart descriptions are not here: they are keyed by metric path in the shared
 * `chart.metric.*` / `chart.scope.*` space in core, so a description can never
 * drift from the chart it belongs to.
 */
const slice: Dict = {
  "monitoring.subtitle": "High-resolution time-series sensor analysis & electrical profiles",
  "monitoring.deviceLabel": "Device:",

  "monitoring.view.workspace": "Operational Summary",
  "monitoring.view.metrics": "All Metrics Breakdown",

  "monitoring.kpi.todayEnergy": "Today Energy",
  "monitoring.kpi.todayEnergyNote": "Asia/Jakarta 00:00 - Now",
  "monitoring.kpi.periodDelta": "Period Delta Energy",
  "monitoring.kpi.periodDeltaNote": "Selected {range} interval",
  "monitoring.kpi.accumulated": "Accumulated Meter",
  "monitoring.kpi.accumulatedNote": "Total counter register",
  "monitoring.kpi.resolutionQuality": "Resolution & Quality",
  "monitoring.kpi.coverageAbbrev": "cov",
  "monitoring.kpi.quality": "Quality: {quality}",

  "monitoring.error.title": "Telemetry History Unavailable",
  "monitoring.error.devices": "Failed to load devices",
  "monitoring.error.telemetry": "Failed to load telemetry history",

  "monitoring.banner.head": "Data available for {duration} of selected {range} ({coverage}% time coverage",
  "monitoring.banner.sampleCoverage": ", {coverage}% sample coverage",
  "monitoring.banner.gap": ", {count} gap detected",
  "monitoring.banner.gaps": ", {count} gaps detected",
  "monitoring.banner.truncated": ", history fetch reached its page budget before the window start",
  "monitoring.banner.tail": ").",

  "monitoring.section.solarDc": "Solar DC & MPPT Power Conversion",
  "monitoring.section.acThreePhase": "AC 3-Phase Electrical Subsystem",

  "monitoring.chart.powerDc": "Solar PV vs MPPT Output Power",
  "monitoring.chart.voltageDc": "Solar PV vs MPPT DC Voltage",
  "monitoring.chart.voltageAc": "3-Phase Voltage Profile",
  "monitoring.chart.currentAc": "3-Phase Load Current Profile",
  "monitoring.chart.accumulatedEnergy": "Accumulated Energy Meter",

  "monitoring.metric.pvInputPower": "PV Input Power",
  "monitoring.metric.mpptOutputPower": "MPPT Output Power",
  "monitoring.metric.pvVoltage": "PV Array Voltage",
  "monitoring.metric.pvCurrent": "PV Array Current",
  "monitoring.metric.mpptOutputVoltage": "MPPT Output Voltage",
  "monitoring.metric.mpptOutputCurrent": "MPPT Output Current",
  "monitoring.metric.phaseAVoltage": "Phase A Voltage",
  "monitoring.metric.phaseBVoltage": "Phase B Voltage",
  "monitoring.metric.phaseCVoltage": "Phase C Voltage",
  "monitoring.metric.phaseACurrent": "Phase A Current",
  "monitoring.metric.phaseBCurrent": "Phase B Current",
  "monitoring.metric.phaseCCurrent": "Phase C Current",
  "monitoring.metric.accumulatedEnergy": "Accumulated Energy",
  "monitoring.metric.solarRadiation": "Solar Irradiance",
};

export default slice;
