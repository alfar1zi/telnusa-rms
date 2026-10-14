/**
 * TELNUSA NMS - API abstraction layer.
 * UI imports only this file. Real mode never falls back to mock data.
 */
import { IS_MOCK } from "./dataMode.ts";
import { apiFetch, ApiError, AuthError } from "./http.ts";
import type { Device } from "@/types/device";
import type {
  DeviceTelemetry,
  DeviceLatestTelemetry,
  DeviceHealthHistory,
  DeviceHealthSnapshot,
} from "@/types/telemetry";
import type { Alarm, AlarmRule, FleetSummary } from "@/types/alarm";
import type { DeviceEvent } from "@/types/event";
import type { AuthProviders, AuthUser, CreateUserPayload, UpdateUserPayload, UserRecord } from "@/types/auth";
export type IntegrationStatus = {
  mqtt?: { state?: string; configured?: boolean | number; metrics?: Record<string, unknown> };
  zabbix?: { state?: string; configured?: boolean | number; reachable?: boolean | number | null; authenticated?: boolean | number | null; last_success?: number | null; last_error?: string | null; metrics?: Record<string, unknown> };
  sse?: { clients?: number; publishedEvents?: number; droppedEvents?: number };
};

function mockDelay<T>(val: T): Promise<T> {
  const { promise, resolve } = Promise.withResolvers<T>();
  setTimeout(() => resolve(val), 0);
  return promise;
}

async function mockDevices() {
  return import("@/mock/devices");
}
async function mockTelemetry() {
  return import("@/mock/telemetry");
}
async function mockAlarms() {
  return import("@/mock/alarms");
}
async function mockEvents() {
  return import("@/mock/events");
}
async function mockHealth() {
  return import("@/mock/health");
}

export async function getAuthUser(): Promise<AuthUser | null> {
  if (IS_MOCK) {
    return mockDelay<AuthUser>({
      id:       "mock-001",
      email:    "admin@telnusa.dev",
      name:     "Admin (Mock)",
      role:     "admin",
      provider: "local",
    });
  }
  try {
    return await apiFetch<AuthUser>("/auth/me", { redirectOn401: false });
  } catch (e) {
    // 401 is the only answer that means "no session". A 5xx, a timeout or an
    // unreachable backend must reach the caller: treating an outage as "logged
    // out" is what bounced operators to the login form while the API was down.
    if (e instanceof ApiError && e.status === 401) return null;
    if (e instanceof AuthError) return null;
    throw e;
  }
}

export async function getAuthProviders(): Promise<AuthProviders> {
  if (IS_MOCK) {
    return mockDelay({ providers: { local: { enabled: true } } });
  }
  return apiFetch<AuthProviders>("/auth/providers");
}

export async function changePassword(payload: { currentPassword: string; newPassword: string }): Promise<{ ok: boolean; user?: AuthUser }> {
  if (IS_MOCK) {
    return mockDelay({ ok: true });
  }
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<{ ok: boolean; user?: AuthUser }>("/auth/change-password", {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(payload),
  });
}

export async function getUsers(): Promise<UserRecord[]> {
  if (IS_MOCK) {
    return mockDelay<UserRecord[]>([
      {
        id: "mock-001",
        email: "admin@telnusa.dev",
        name: "Admin (Mock)",
        role: "admin",
        provider: "local",
        mustChangePassword: false,
        disabled: false,
        createdAt: 1700000000000,
        updatedAt: 1700000000000,
      },
    ]);
  }
  const res = await apiFetch<{ users: UserRecord[] }>("/users");
  return res.users;
}

export async function createUser(payload: CreateUserPayload): Promise<UserRecord> {
  if (IS_MOCK) {
    return mockDelay<UserRecord>({
      id: "mock-new",
      email: payload.email,
      name: payload.name,
      role: payload.role,
      provider: "local",
      mustChangePassword: true,
      disabled: false,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    });
  }
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<UserRecord>("/users", {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(payload),
  });
}

export async function updateUser(id: string, payload: UpdateUserPayload): Promise<UserRecord> {
  if (IS_MOCK) {
    return mockDelay<UserRecord>({
      id,
      email: "mock@telnusa.dev",
      name: payload.name || "Mock User",
      role: payload.role || "viewer",
      provider: "local",
      mustChangePassword: !!payload.mustChangePassword,
      disabled: !!payload.disabled,
      createdAt: 1700000000000,
      updatedAt: Date.now(),
    });
  }
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<UserRecord>(`/users/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(payload),
  });
}

export async function resetUserPassword(id: string, newPassword: string): Promise<void> {
  if (IS_MOCK) return mockDelay(undefined);
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  await apiFetch<{ ok: boolean }>(`/users/${encodeURIComponent(id)}/reset-password`, {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ newPassword }),
  });
}

export async function revokeUserSessions(id: string): Promise<void> {
  if (IS_MOCK) return mockDelay(undefined);
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  await apiFetch<{ ok: boolean }>(`/users/${encodeURIComponent(id)}/revoke-sessions`, {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
  });
}

export async function getIntegrationStatus(): Promise<IntegrationStatus> {
  if (IS_MOCK) {
    return mockDelay({
      mqtt: { state: "MOCK", configured: false },
      zabbix: { state: "MOCK", configured: false },
      sse: { clients: 0, publishedEvents: 0, droppedEvents: 0 },
    });
  }
  return await apiFetch<IntegrationStatus>("/integration/status");
}

export async function logout(): Promise<void> {
  if (IS_MOCK) {
    window.location.href = "/login";
    return;
  }
  try {
    await apiFetch<void>("/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}

export async function getDevices(): Promise<Device[]> {
  if (IS_MOCK) return mockDelay((await mockDevices()).MOCK_DEVICES);
  return apiFetch<Device[]>("/devices");
}

export async function getDevice(id: string): Promise<Device | undefined> {
  if (IS_MOCK) {
    const { MOCK_DEVICES } = await mockDevices();
    return mockDelay(MOCK_DEVICES.find((d) => d.id === id));
  }
  try {
    return await apiFetch<Device>(`/devices/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}

export type TelemetryQueryParams = {
  from?: number;
  to?: number;
  limit?: number;
  range?: string;
  resolution?: "raw" | "hourly" | "daily";
};

export async function getDeviceTelemetry(
  id: string,
  params?: TelemetryQueryParams,
): Promise<DeviceTelemetry | undefined> {
  if (IS_MOCK) {
    const { MOCK_TELEMETRY } = await mockTelemetry();
    if (id !== MOCK_TELEMETRY.deviceId) return mockDelay(undefined);
    return mockDelay(MOCK_TELEMETRY);
  }
  try {
    const searchParams = new URLSearchParams();
    if (params?.from !== undefined) searchParams.set("from", String(params.from));
    if (params?.to !== undefined) searchParams.set("to", String(params.to));
    if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));
    if (params?.range !== undefined) searchParams.set("range", String(params.range));
    if (params?.resolution !== undefined) searchParams.set("resolution", String(params.resolution));
    const qs = searchParams.toString();
    const url = `/devices/${encodeURIComponent(id)}/telemetry${qs ? `?${qs}` : ""}`;
    return await apiFetch<DeviceTelemetry>(url);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}

export async function getDeviceLatestTelemetry(id: string): Promise<DeviceLatestTelemetry | undefined> {
  if (IS_MOCK) {
    const { MOCK_LATEST_TELEMETRY } = await mockTelemetry();
    if (id !== MOCK_LATEST_TELEMETRY.deviceId) return mockDelay(undefined);
    return mockDelay(MOCK_LATEST_TELEMETRY);
  }
  try {
    return await apiFetch<DeviceLatestTelemetry>(`/devices/${encodeURIComponent(id)}/telemetry/latest`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}

export async function getDeviceHealthHistory(id: string): Promise<DeviceHealthHistory | undefined> {
  if (IS_MOCK) {
    const { MOCK_HEALTH_HISTORY } = await mockHealth();
    return mockDelay(MOCK_HEALTH_HISTORY.find((h) => h.deviceId === id));
  }
  try {
    return await apiFetch<DeviceHealthHistory>(`/devices/${encodeURIComponent(id)}/health/history`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}

export async function getDeviceHealthSnapshot(id: string): Promise<DeviceHealthSnapshot | undefined> {
  if (IS_MOCK) {
    const { MOCK_HEALTH_SNAPSHOTS } = await mockHealth();
    return mockDelay(MOCK_HEALTH_SNAPSHOTS.find((h) => h.deviceId === id));
  }
  try {
    return await apiFetch<DeviceHealthSnapshot>(`/devices/${encodeURIComponent(id)}/health`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}

export async function getFleetSummary(): Promise<FleetSummary> {
  if (IS_MOCK) {
    const { MOCK_DEVICES } = await mockDevices();
    const { MOCK_ALARMS } = await mockAlarms();
    const total = MOCK_DEVICES.length;
    const online = MOCK_DEVICES.filter((d) => d.status === "online").length;
    const degraded = MOCK_DEVICES.filter((d) => d.status === "degraded" || d.status === "stale" || d.status === "clock_skew").length;
    const offline = MOCK_DEVICES.filter((d) => d.status === "offline").length;
    const unknown = MOCK_DEVICES.filter((d) => d.status === "unknown" || d.status === "no_data").length;
    const activeAlarms = MOCK_ALARMS.filter((a) => a.status === "active").length;
    return mockDelay({
      total,
      online,
      degraded,
      offline,
      unknown,
      activeAlarms,
      latestTelemetryAt: Date.now(),
    });
  }
  return apiFetch<FleetSummary>("/fleet/summary");
}

export async function getAllAlarms(): Promise<Alarm[]> {
  if (IS_MOCK) return mockDelay((await mockAlarms()).MOCK_ALARMS);
  return apiFetch<Alarm[]>("/alarms");
}

export async function getAlarms(params?: {
  deviceId?: string;
  state?: string;
  severity?: string;
  ruleId?: string;
  limit?: number;
}): Promise<Alarm[]> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    let res = [...MOCK_ALARMS];
    if (params?.deviceId) res = res.filter((a) => a.deviceId === params.deviceId);
    if (params?.severity) res = res.filter((a) => a.severity === params.severity);
    const stateFilter = params?.state;
    if (stateFilter) {
      if (stateFilter === "active" || stateFilter === "OPEN_OR_ACK") {
        res = res.filter((a) => a.status === "active");
      } else {
        res = res.filter((a) => a.state === stateFilter || a.status === stateFilter.toLowerCase());
      }
    }
    return mockDelay(res);
  }
  const query = new URLSearchParams();
  if (params?.deviceId) query.set("deviceId", params.deviceId);
  if (params?.state) query.set("state", params.state);
  if (params?.severity) query.set("severity", params.severity);
  if (params?.ruleId) query.set("ruleId", params.ruleId);
  if (params?.limit) query.set("limit", String(params.limit));
  const q = query.toString();
  return apiFetch<Alarm[]>(`/alarms${q ? `?${q}` : ""}`);
}

export async function getAlarmHistory(params?: {
  deviceId?: string;
  limit?: number;
}): Promise<Alarm[]> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    let res = MOCK_ALARMS.filter((a) => a.status === "resolved");
    if (params?.deviceId) res = res.filter((a) => a.deviceId === params.deviceId);
    return mockDelay(res);
  }
  const query = new URLSearchParams();
  if (params?.deviceId) query.set("deviceId", params.deviceId);
  if (params?.limit) query.set("limit", String(params.limit));
  const q = query.toString();
  return apiFetch<Alarm[]>(`/alarms/history${q ? `?${q}` : ""}`);
}

export async function getAlarm(id: string): Promise<Alarm> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    const a = MOCK_ALARMS.find((x) => x.id === id);
    if (!a) throw new ApiError(404, "Alarm not found");
    return mockDelay(a);
  }
  return apiFetch<Alarm>(`/alarms/${encodeURIComponent(id)}`);
}

export async function acknowledgeAlarm(id: string, note?: string): Promise<Alarm> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    const a = MOCK_ALARMS.find((x) => x.id === id);
    if (!a) throw new ApiError(404, "Alarm not found");
    a.state = "ACKNOWLEDGED";
    a.acknowledgedAt = Date.now();
    a.acknowledgedBy = "operator@telnusa.com";
    a.ackNote = note || null;
    return mockDelay(a);
  }
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<Alarm>(`/alarms/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ note: note || "" }),
  });
}

export async function resolveAlarm(id: string, reason?: string): Promise<Alarm> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    const a = MOCK_ALARMS.find((x) => x.id === id);
    if (!a) throw new ApiError(404, "Alarm not found");
    a.state = "RESOLVED";
    a.status = "resolved";
    a.resolvedAt = Date.now();
    a.resolvedBy = "operator@telnusa.com";
    a.resolutionReason = reason || null;
    return mockDelay(a);
  }
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<Alarm>(`/alarms/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ reason: reason || "" }),
  });
}

export async function getAlarmRules(): Promise<AlarmRule[]> {
  if (IS_MOCK) {
    return mockDelay([
      {
        rule_id: "rule.device.offline",
        name: "Device Offline",
        metric: "data_age",
        device_pattern: "*",
        severity: "high",
        operator: "==",
        threshold: "offline",
        evaluation_interval: 60,
        consecutive_count: 1,
        enabled: 1,
      },
    ]);
  }
  return apiFetch<AlarmRule[]>("/alarms/rules");
}

export async function upsertAlarmRule(rule: Partial<AlarmRule>): Promise<{ ok: boolean }> {
  if (IS_MOCK) return mockDelay({ ok: true });
  const { csrfToken } = await apiFetch<{ csrfToken: string }>("/auth/csrf");
  return apiFetch<{ ok: boolean }>("/alarms/rules", {
    method: "POST",
    headers: {
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(rule),
  });
}

export async function getDeviceAlarms(id: string): Promise<Alarm[]> {
  if (IS_MOCK) {
    const { MOCK_ALARMS } = await mockAlarms();
    return mockDelay(MOCK_ALARMS.filter((a) => a.deviceId === id));
  }
  return apiFetch<Alarm[]>(`/devices/${encodeURIComponent(id)}/alarms`);
}

export async function getDeviceEvents(id: string): Promise<DeviceEvent[]> {
  if (IS_MOCK) {
    const { MOCK_EVENTS } = await mockEvents();
    return mockDelay(MOCK_EVENTS.filter((e) => e.deviceId === id));
  }
  return apiFetch<DeviceEvent[]>(`/devices/${encodeURIComponent(id)}/events`);
}

export async function getAllEvents(): Promise<DeviceEvent[]> {
  if (IS_MOCK) return mockDelay((await mockEvents()).MOCK_EVENTS);
  return apiFetch<DeviceEvent[]>("/events");
}

export async function getDeviceStatus(id: string): Promise<{ status: Device["status"]; lastSeen: Device["lastSeen"] } | undefined> {
  if (IS_MOCK) {
    const { MOCK_DEVICES } = await mockDevices();
    const d = MOCK_DEVICES.find((dev) => dev.id === id);
    return mockDelay(d ? { status: d.status, lastSeen: d.lastSeen } : undefined);
  }
  try {
    return await apiFetch<{ status: Device["status"]; lastSeen: Device["lastSeen"] }>(
      `/devices/${encodeURIComponent(id)}/status`,
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return undefined;
    throw e;
  }
}