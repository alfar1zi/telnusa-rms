/**
 * Runtime validation for one SSE frame arriving from /api/live/:deviceId.
 *
 * SSE-01: the previous implementation cast `JSON.parse(e.data) as SseEvent`, so
 * any well-formed JSON: a server bug, a proxy error body, a captive portal,
 * reached the UI as if it were real device data. Everything crossing this
 * boundary is validated here, once, before it is typed.
 *
 * Pure module: no React, no network. Lives in lib/ so it is directly testable.
 */
import type { SseEvent, SseEventType } from "@/types/auth.ts";

const KNOWN_EVENT_TYPES: readonly SseEventType[] = [
  "telemetry", "status", "health", "alarm", "keepalive", "resync",
];

/**
 * Parse and validate one SSE frame.
 *
 * Returns null for anything that is not a well-formed frame of a known type.
 */
export function parseSseFrame(raw: unknown): SseEvent | null {
  if (typeof raw !== "string" || raw.length === 0) return null;

  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    return null;
  }

  if (typeof decoded !== "object" || decoded === null || Array.isArray(decoded)) {
    return null;
  }
  if (!("type" in decoded) || !("payload" in decoded)) return null;

  const { type, payload } = decoded;

  if (typeof type !== "string") return null;
  if (!(KNOWN_EVENT_TYPES as readonly string[]).includes(type)) return null;

  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return null;
  }

  // Every payload must carry the deviceId it claims to describe; without it a
  // frame cannot be attributed and must not be applied to the current device.
  // "resync" is exempt: it is a stream-level signal, not a device reading.
  if (type !== "resync" && (!("deviceId" in payload) || typeof payload.deviceId !== "string")) {
    return null;
  }

  return { type: type as SseEventType, payload } as SseEvent;
}