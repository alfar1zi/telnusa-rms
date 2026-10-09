import type { Dict, Lang } from "./types.ts";
import enCore from "./en/core.ts";
import idCore from "./id/core.ts";
import enAlarms from "./en/alarms.ts";
import enAuth from "./en/auth.ts";
import enDashboard from "./en/dashboard.ts";
import enDeviceDetails from "./en/deviceDetails.ts";
import enDevices from "./en/devices.ts";
import enMonitoring from "./en/monitoring.ts";
import enReports from "./en/reports.ts";
import enSettings from "./en/settings.ts";
import idAlarms from "./id/alarms.ts";
import idAuth from "./id/auth.ts";
import idDashboard from "./id/dashboard.ts";
import idDeviceDetails from "./id/deviceDetails.ts";
import idDevices from "./id/devices.ts";
import idMonitoring from "./id/monitoring.ts";
import idReports from "./id/reports.ts";
import idSettings from "./id/settings.ts";

/**
 * Language registry.
 *
 * Every slice file owns one dotted prefix (`settings.*`, `dashboard.*`), so two
 * authors cannot collide and a missing slice simply falls back to the other
 * language. Slices are imported explicitly rather than discovered at build time:
 * the module is plain ES so the node test runner can import it unchanged.
 */
const EN: Dict = {
  ...enCore,
  ...enAlarms,
  ...enAuth,
  ...enDashboard,
  ...enDeviceDetails,
  ...enDevices,
  ...enMonitoring,
  ...enReports,
  ...enSettings,
};

const ID: Dict = {
  ...idCore,
  ...idAlarms,
  ...idAuth,
  ...idDashboard,
  ...idDeviceDetails,
  ...idDevices,
  ...idMonitoring,
  ...idReports,
  ...idSettings,
};

const BY_LANG: Record<Lang, Dict> = { en: EN, id: ID };

export function lookup(lang: Lang, key: string): string | undefined {
  return BY_LANG[lang][key];
}

export function lookupFallback(key: string): string | undefined {
  return EN[key];
}
