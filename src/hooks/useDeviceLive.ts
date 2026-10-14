/**
 * useDeviceLive: subscribes to server-sent events for a single device.
 *
 * In mock mode: no-op. Updates come from the initial API fetch only.
 * In real mode: opens SSE connection to /api/live/:deviceId.
 *
 * The SSE stream pushes normalized events from the integration service:
 *   "telemetry" | "status" | "health" | "alarm" | "keepalive" | "resync"
 *
 * SSE-01 behaviour:
 * - Every frame is validated at runtime. A malformed or unknown frame is dropped,
 *   never cast into a typed payload and rendered as if it were real.
 * - "resync" means the server truncated this client's buffer. The caller refetches
 *   the authoritative snapshot; the device is NOT reported as offline.
 * - Reconnection is bounded and backed off, so a dead backend cannot become a
 *   reconnect storm against the live endpoint.
 *
 * NEVER connects directly to MQTT; that is the integration service's job.
 */

import { useEffect, useLayoutEffect, useRef } from "react";
import { IS_MOCK }           from "@/services/dataMode";
import { openSse }           from "@/services/http";
import { parseSseFrame }     from "@/lib/sseFrame";
import type {
  LiveStreamState,
  SseTelemetryPayload,
  SseStatusPayload,
  SseHealthPayload,
} from "@/types/auth";

/** Reconnect backoff bounds, in ms. The server's Retry-Ms seeds the first wait. */
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 30000;
const RETRY_FACTOR = 2;

/**
 * Severity from an alarm frame payload, read defensively: an alarm frame is
 * only proven to be an object with a deviceId, so anything but a string
 * severity is reported as unknown rather than asserted into a type.
 */
function alarmSeverity(payload: unknown): string | undefined {
  if (typeof payload !== "object" || payload === null) return undefined;
  if (!("severity" in payload)) return undefined;
  const severity: unknown = payload.severity;
  return typeof severity === "string" ? severity : undefined;
}

/** Same discipline as alarmSeverity: a non-string id is absent, never coerced. */
function alarmId(payload: unknown): string | undefined {
  if (typeof payload !== "object" || payload === null) return undefined;
  if (!("alarmId" in payload)) return undefined;
  const id: unknown = payload.alarmId;
  return typeof id === "string" && id.length > 0 ? id : undefined;
}


export interface DeviceLiveCallbacks {
  onTelemetry?: (payload: SseTelemetryPayload) => void;
  onStatus?:    (payload: SseStatusPayload)    => void;
  onHealth?:    (payload: SseHealthPayload)    => void;
  /** An alarm frame arrived. The wire type is "alarm"; its payload is not
      validated beyond severity here, so only a string severity is passed on. */
  onAlarm?:     (severity: string | undefined, alarmId: string | undefined) => void;
  onError?:     (err: Event)                  => void;
  /** Server said our buffer was truncated: refetch the authoritative snapshot. */
  onResync?:    ()                            => void;
  /** Stream-level state, kept distinct from device status. */
  onStreamState?: (state: LiveStreamState)    => void;
}

export function useDeviceLive(
  deviceId: string | undefined,
  callbacks: DeviceLiveCallbacks,
): void {
  const cbRef = useRef(callbacks);
  // Keep ref in sync with latest callbacks via layout effect (before paint,
  // so the SSE handler always reads current callbacks without re-subscribing)
  useLayoutEffect(() => { cbRef.current = callbacks; });

  useEffect(() => {
    if (IS_MOCK || !deviceId) return;
    // Narrowed copy: deviceId is optional to the hook, and the closures below
    // would otherwise lose the narrowing done by the guard.
    const target: string = deviceId;
    let es: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryMs = RETRY_MIN_MS;
    let closed = false;

    function clearRetry() {
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
    }

    function handleMessage(e: MessageEvent) {
      const parsed = parseSseFrame(e.data);
      if (!parsed) return; // malformed or unknown frame: drop it, never cast

      if (parsed.type === "resync") {
        cbRef.current.onStreamState?.("resyncing");
        cbRef.current.onResync?.();
        cbRef.current.onStreamState?.("connected");
        return;
      }

      switch (parsed.type) {
        case "telemetry":
          cbRef.current.onTelemetry?.(parsed.payload as SseTelemetryPayload);
          break;
        case "status":
          cbRef.current.onStatus?.(parsed.payload as SseStatusPayload);
          break;
        case "health":
          cbRef.current.onHealth?.(parsed.payload as SseHealthPayload);
          break;
        case "alarm":
          cbRef.current.onAlarm?.(alarmSeverity(parsed.payload), alarmId(parsed.payload));
          break;
        case "keepalive":
          break; // intentional no-op; keeps connection alive
        default:
          break;
      }
    }

    function scheduleReconnect() {
      if (closed) return;
      clearRetry();
      cbRef.current.onStreamState?.("reconnecting");
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        open();
      }, retryMs);
      // Bounded exponential backoff: no unbounded retry storm.
      retryMs = Math.min(retryMs * RETRY_FACTOR, RETRY_MAX_MS);
    }

    function handleError(e: Event) {
      if (closed) return;
      cbRef.current.onError?.(e);

      // EventSource fires "error" both for a transient drop and when the server
      // refuses the connection outright (e.g. SSE-01 capacity 503). Closing and
      // backing off covers both; a refusal must not become a tight loop.
      if (es) {
        es.close();
        es = null;
      }
      scheduleReconnect();
    }

    function open() {
      if (closed) return;
      es = openSse(`/live/${encodeURIComponent(target)}`);
      es.addEventListener("message", handleMessage);
      es.addEventListener("error",   handleError);
      es.addEventListener("open", () => {
        if (closed) return;
        retryMs = RETRY_MIN_MS;
        cbRef.current.onStreamState?.("connected");
      });
    }

    open();

    return () => {
      closed = true;
      clearRetry();
      if (es) {
        es.removeEventListener("message", handleMessage);
        es.removeEventListener("error",   handleError);
        es.close();
        es = null;
      }
    };
  }, [deviceId]); // reconnect only when deviceId changes
}
