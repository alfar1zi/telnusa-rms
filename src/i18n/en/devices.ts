import type { Dict } from "../types";

/**
 * Devices page: fleet registry, filters, table and load failures.
 *
 * The caps status tokens ("NO DATA", "UNKNOWN") are console vocabulary and stay
 * identical in both languages, so they are not keys here.
 */
const slice: Dict = {
  "devices.title": "Devices",
  "devices.subtitle": "Registered RUT906 gateways, fleet health, and remote telemetry",

  "devices.kpi.total": "Total Gateways",
  "devices.kpi.online": "Online",
  "devices.kpi.degraded": "Degraded / Stale",
  "devices.kpi.offline": "Offline",
  "devices.kpi.alarms": "Active Alarms",

  "devices.empty": "No devices registered.",
  "devices.error.title": "Devices Unavailable",
  "devices.error.alarmsTitle": "Alarm Data Unavailable",
  "devices.error.alarmsMessage": "Device list loaded, but alarm counts could not be retrieved. Counts are unknown, not zero.",

  "devices.col.site": "Site",
  "devices.col.deviceId": "Device ID",
  "devices.col.model": "Model",
  "devices.col.status": "Status",
  "devices.col.health": "Health",
  "devices.col.network": "Network",
  "devices.col.lastSeen": "Last Seen",
  "devices.col.alarms": "Alarms",
  "devices.col.actions": "Actions",

  "devices.action.monitor": "Monitor",
  "devices.action.details": "Details",

  "devices.load.network": "Network unreachable - the API could not be contacted.",
  "devices.load.unauthenticated": "Session expired or not authenticated. Sign in again.",
  "devices.load.forbidden": "Not authorized to view devices.",
  "devices.load.notFound": "Device registry endpoint not found.",
  "devices.load.rateLimited": "Rate limited by the API. Retry shortly.",
  "devices.load.server": "Backend unavailable (HTTP {status}).",
  "devices.load.rejected": "Request rejected (HTTP {status}).",
};

export default slice;
