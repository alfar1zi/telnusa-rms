/**
 * Devices page data aggregation.
 *
 * Deliberately free of React and of @/services imports, and takes its loaders
 * as arguments, so the state transition is directly runnable under
 * `node --test`. The page passes the real API helpers; tests pass fakes.
 *
 * Contract this module exists to enforce: a rejected dependency is never
 * reported as an empty success. When alarms fail the device list still renders
 * (it is real) but the alarm counts stay unknown rather than collapsing to a
 * fabricated zero.
 */
import { translate } from "../../i18n/translate.ts";
import type { Device } from "@/types/device";
import type { Alarm } from "@/types/alarm";

export type DependencyState = "ok" | "unavailable";

export interface LoadFailure {
  /** HTTP status, or null when the request never produced a response. */
  status: number | null;
  message: string;
}

export interface DevicesLoadState {
  devices: Device[];
  alarmCounts: Record<string, number>;
  alarmState: DependencyState;
  /** Set only when the device list itself could not be loaded. */
  failure: LoadFailure | null;
}

/**
 * Turn a rejection into a status/message pair a consumer can act on.
 *
 * The wording comes from the `devices.load.*` dictionary keys, so the message
 * the page renders follows the console language; only the HTTP status stays.
 */
export function describeFailure(error: unknown): LoadFailure {
  const raw = (error as { status?: unknown } | null | undefined)?.status;
  const status = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  if (status === null) {
    return { status, message: translate("devices.load.network") };
  }
  if (status === 401) return { status, message: translate("devices.load.unauthenticated") };
  if (status === 403) return { status, message: translate("devices.load.forbidden") };
  if (status === 404) return { status, message: translate("devices.load.notFound") };
  if (status === 429) return { status, message: translate("devices.load.rateLimited") };
  if (status >= 500) return { status, message: translate("devices.load.server", { status }) };
  return { status, message: translate("devices.load.rejected", { status }) };
}

/**
 * A 200 carrying something other than a list would make the page iterate a
 * non-iterable, which is how loading previously never terminated. Treat it as
 * a failure rather than letting it throw inside a render path.
 */
function asList<T>(value: unknown, what: string): T[] {
  if (!Array.isArray(value)) {
    throw Object.assign(new Error(`${what} response was not a list`), { status: 502 });
  }
  return value as T[];
}

export async function loadDevicesState(
  loadDevices: () => Promise<Device[]>,
  loadAlarms: () => Promise<Alarm[]>,
): Promise<DevicesLoadState> {
  const [devsRes, almsRes] = await Promise.allSettled([loadDevices(), loadAlarms()]);

  if (devsRes.status === "rejected") {
    return {
      devices: [],
      alarmCounts: {},
      alarmState: "unavailable",
      failure: describeFailure(devsRes.reason),
    };
  }

  const devices = asList<Device>(devsRes.value, "Devices");

  if (almsRes.status === "rejected") {
    return { devices, alarmCounts: {}, alarmState: "unavailable", failure: null };
  }

  const alarms = asList<Alarm>(almsRes.value, "Alarms");
  const alarmCounts: Record<string, number> = {};
  for (const device of devices) {
    alarmCounts[device.id] = alarms.filter(
      (a) => a.deviceId === device.id && a.status === "active",
    ).length;
  }
  return { devices, alarmCounts, alarmState: "ok", failure: null };
}
