import type { Dict } from "../types";

/**
 * Shared chrome: navigation, header, common actions, data-state labels, time
 * ranges and chart descriptions. Page-specific copy lives in its own slice.
 *
 * Data-state tokens ("NO DATA", "Loading") stay identical in both languages on
 * purpose: they are status vocabulary of the console, not prose.
 */
const core: Dict = {
  // Navigation and page chrome
  "nav.navigation": "NAVIGATION",
  "nav.dashboard": "Dashboard",
  "nav.devices": "Devices",
  "nav.monitoring": "Monitoring",
  "nav.alarms": "Alarms",
  "nav.reports": "Reports",
  "nav.settings": "Settings",
  "brand.name": "TELNUSA NMS",
  "brand.tagline": "NOC OPERATIONS",
  "header.title.dashboard": "Overview Dashboard",
  "header.title.devices": "Device Fleet",
  "header.title.monitoring": "Telemetry Analytics",
  "header.title.alarms": "Alarms & Security",
  "header.title.reports": "Historical Reports",
  "header.title.settings": "System Settings",
  "header.title.deviceDetail": "Device Telemetry & Controls",
  "header.title.remote": "Secure Remote Access",
  "header.toggleTheme": "Toggle theme",
  "header.theme.light": "Light (Linen)",
  "header.theme.dark": "Dark (Charcoal)",
  "header.theme.system": "System Synchronized",
  "header.userMenu": "User menu",
  "header.language": "Language",
  "header.signOut": "Sign out",
  "header.sidebarToggle": "Toggle navigation",

  // Common actions and states
  "common.loading": "Loading",
  "common.retry": "Retry",
  "common.cancel": "Cancel",
  "common.save": "Save",
  "common.close": "Close",
  "common.contactAdmin": "If this persists, contact your administrator.",
  "common.expandSidebar": "Expand navigation",
  "outage.title": "Service Unavailable",
  "outage.message": "The TELNUSA backend is not reachable, so this page cannot load. Your session has not been rejected.",

  // Router health semantics (labels asserted by lib/*.test.ts)
  "state.connected": "Connected",
  "state.degraded": "Degraded",
  "state.disconnected": "Disconnected",
  "state.unknown": "Unknown",
  "state.unknownUpper": "UNKNOWN",
  "state.noDataLabel": "No Data",
  "state.noData": "NO DATA",
  "state.unassigned": "UNASSIGNED",
  "state.healthy": "HEALTHY",
  "state.warning": "WARNING",
  "state.critical": "CRITICAL",
  "state.notIntegrated": "NOT INTEGRATED",

  // Metric state
  "metricState.data": "DATA AVAILABLE",
  "metricState.noDataInRange": "NO DATA IN SELECTED RANGE",
  "metricState.notIntegrated": "NOT INTEGRATED",
  "metricState.error": "ERROR",
  "metricState.empty.notIntegrated": "This metric has no data source configured.",
  "metricState.empty.error": "This metric could not be read from its source.",
  "metricState.empty.noDataInRange": "The source is integrated but sent nothing in the selected range.",
  "metricState.empty.data": "Data is available.",

  // Health source state
  "healthState.available": "DATA AVAILABLE",
  "healthState.noData": "NO DATA",
  "healthState.notIntegrated": "NOT INTEGRATED",
  "healthState.error": "ERROR",

  // Event types
  "event.alarmCreated": "Alarm Created",
  "event.alarmResolved": "Alarm Resolved",
  "event.statusChange": "Status Change",
  "event.configChange": "Config Change",
  "event.reboot": "Reboot",
  "event.unknown": "Unknown Event",
  "event.unnamed": "Unnamed event",

  // Status and sensor badges
  "badge.status.online": "Online",
  "badge.status.offline": "Offline",
  "badge.status.stale": "Telemetry Stale",
  "badge.status.clockSkew": "Clock Skew",
  "badge.status.sensorFault": "Sensor Fault",
  "badge.status.partial": "Partial Data",
  "badge.status.degraded": "Needs Attention",
  "badge.status.noData": "No Data",
  "badge.status.unknown": "Unknown",
  "badge.status.allOk": "Normal",
  "badge.status.notConnected": "Not Connected",
  "badge.severity.disaster": "DISASTER",
  "badge.severity.high": "HIGH",
  "badge.severity.average": "AVERAGE",
  "badge.severity.warning": "WARNING",
  "badge.severity.information": "INFO",
  "badge.sensor.allOk": "Normal",
  "badge.sensor.ok": "Normal",
  "badge.sensor.partial": "Partial Data",
  "badge.sensor.sensorFault": "Sensor Fault",
  "badge.sensor.warning": "Attention",
  "badge.sensor.noData": "No Data",
  "badge.sensor.notInstalled": "Not Installed",
  "badge.sensor.notIntegrated": "Not Integrated",

  // Authentication
  "auth.passwordChangeFailed": "Could not change the password. Check the current password.",
  "auth.passwordChangeFailedGeneric": "Failed to change password.",

  // Time ranges
  "range.15m": "Last 15 min",
  "range.1h": "Last 1 hour",
  "range.6h": "Last 6 hours",
  "range.12h": "Last 12 hours",
  "range.24h": "Last 24 hours",
  "range.7d": "Last 7 days",
  "range.30d": "Last 30 days",

  // Telemetry chart shell
  "chart.loadFailed": "Failed to load telemetry data.",
  "chart.partial": "partial",

  // Login
  "login.brand": "Network Management System",
  "login.organizedBy": "Organized by Telnusa Intrakom",
  "login.product": "NETWORK MANAGEMENT SYSTEM",
  "login.welcome": "Welcome to Network Management System",
  "login.subtitle": "Sign in to continue to the NMS console.",
  "login.email": "Email",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.submitting": "Signing in...",
  "login.signInRequired": "Sign in to continue.",
  "login.sessionExpired": "Session expired. Sign in again.",

  // Chart descriptions: one per metric (single-series canvas)
  "chart.metric.mppt.pvInputPower": "How much DC power the array produces right now, computed from volts x amps at each sample.",
  "chart.metric.mppt.outputPower": "How much DC power the MPPT delivers right now, computed from volts x amps at each sample.",
  "chart.metric.mppt.pvVoltage": "Voltage the array feeds into the MPPT; it drops to 0 V when the array is not producing.",
  "chart.metric.mppt.pvCurrent": "Current the array supplies; it follows the daily sunlight curve.",
  "chart.metric.mppt.outputVoltage": "Voltage the MPPT delivers at its output terminals.",
  "chart.metric.mppt.outputCurrent": "Current the MPPT delivers; 0 A while the output side is idle.",
  "chart.metric.pzem.phaseAVoltage": "Line voltage of phase A on the site supply.",
  "chart.metric.pzem.phaseBVoltage": "Line voltage of phase B on the site supply.",
  "chart.metric.pzem.phaseCVoltage": "Line voltage of phase C on the site supply.",
  "chart.metric.pzem.phaseACurrent": "Load current drawn on phase A; 0 A means that phase carries no load.",
  "chart.metric.pzem.phaseBCurrent": "Load current drawn on phase B; 0 A means that phase carries no load.",
  "chart.metric.pzem.phaseCCurrent": "Load current drawn on phase C; 0 A means that phase carries no load.",
  "chart.metric.pzem.energy": "How much energy the site has drawn in total, as the meter's own cumulative register.",
  "chart.metric.solar.radiation": "How much sunlight reaches the panels, measured in the plane of the array.",
  "chart.metric.system.temperature": "How warm the router itself runs, as the device reports it.",
  "chart.metric.modem.signal": "How strong the cellular link is; closer to 0 dBm means a stronger signal.",
  "chart.metric.modem.temperature": "How warm the modem module runs, as the device reports it.",

  // Chart descriptions: one per comparison canvas
  "chart.scope.power.dc": "How much DC power the array produces versus how much the MPPT delivers, computed per sample.",
  "chart.scope.voltage.dc": "Panel voltage feeding the MPPT against the voltage the MPPT delivers.",
  "chart.scope.voltage.ac": "Line voltage on all three AC phases supplied to the site.",
  "chart.scope.current.ac": "How much current each AC phase draws; 0 A means that phase carries no load.",

  // Unknown URL
  "notFound.title": "Page not found",
  "notFound.subtitle": "The address you opened does not match any console page.",
  "notFound.message": "The page does not exist, or the link that led here is out of date.",
  "notFound.action": "Back to dashboard",
};

export default core;
