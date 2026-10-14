import type { Dict } from "../types";

/**
 * Dashboard page: fleet KPI bar, device hero cards, subsystem grids, alarms and
 * recent events.
 *
 * Shared data-state tokens ("Loading", "NO DATA", "UNKNOWN") come from the core
 * slice instead of being duplicated here. Device data, metric units and the
 * product token "RUT906" stay as rendered values, not copy.
 */
const dashboard: Dict = {
  "dashboard.title": "Operations Command",
  "dashboard.subtitle": "Real-time renewable energy telemetry & edge site status",
  "dashboard.fleetUnavailable": "Fleet Telemetry Unavailable",
  "dashboard.selectDevice": "Select Device",
  "dashboard.details": "Details",

  // Fleet KPI bar
  "dashboard.kpi.online": "Online",
  "dashboard.kpi.attention": "Attention",
  "dashboard.kpi.offline": "Offline",

  // Device hero cards
  "dashboard.hero.siteState": "Site & Edge State",
  "dashboard.hero.lastSeen": "Last Seen",
  "dashboard.hero.pvGeneration": "PV Generation",
  "dashboard.hero.stale": "Stale",
  "dashboard.hero.pvPowerNote": "Real-time instantaneous solar PV power input",
  "dashboard.hero.arrayVoltage": "Array Voltage",
  "dashboard.hero.arrayCurrent": "Array Current",
  "dashboard.hero.accumulatedEnergy": "Accumulated Energy",
  "dashboard.hero.energyNote": "Total energy measured by AC power meter",

  // Solar array and MPPT conversion
  "dashboard.mppt.title": "Solar Array & MPPT",
  "dashboard.status.title": "Fleet status",
  "dashboard.model.title": "Device model",
  "dashboard.alarms.breakdownTitle": "Fleet alarms",
  "dashboard.alarms.none": "No active alarms",
  "dashboard.mppt.arrayPower": "Array power",
  "dashboard.mppt.outputPower": "Output power",
  "dashboard.mppt.subtitle": "DC power conversion and MPPT output telemetry",
  "dashboard.mppt.pvVoltage": "PV Voltage",
  "dashboard.mppt.pvCurrent": "PV Current",
  "dashboard.mppt.outVoltage": "Out Voltage",
  "dashboard.mppt.outCurrent": "Out Current",
  "dashboard.mppt.irradiance": "Solar Irradiance",
  "dashboard.mppt.historicalTrends": "Historical Trends",

  // AC 3-phase power meter
  "dashboard.ac.title": "AC 3-Phase Power Monitoring",
  "dashboard.ac.subtitle": "Tri-phase voltage, load current, and cumulative kWh",
  "dashboard.ac.phaseA": "Phase A",
  "dashboard.ac.phaseB": "Phase B",
  "dashboard.ac.phaseC": "Phase C",
  "dashboard.ac.voltage": "Voltage",
  "dashboard.ac.current": "Current",
  "dashboard.ac.energyReports": "Energy Reports",

  // Active alarms
  "dashboard.alarms.title": "Active System Alarms",
  "dashboard.alarms.subtitle": "Real-time problems scoped to {site}",
  "dashboard.alarms.siteFallback": "selected site",
  "dashboard.alarms.activeCount": "{count} Active",

  // Recent events
  "dashboard.events.title": "Recent Device Events",
  "dashboard.events.subtitle": "Telemetry transitions and edge notifications",
  "dashboard.events.all": "All Events",
};

export default dashboard;
