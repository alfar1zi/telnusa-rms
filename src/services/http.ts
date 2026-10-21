/**
 * Fetch wrapper for all TELNUSA backend API calls.
 *
 * - Attaches credentials (HttpOnly cookies managed by browser automatically)
 * - On 401: redirects to /login, and NEVER continues silently as if authenticated
 * - On non-OK response: throws with status + message from body if available
 * - NEVER exposes credentials or secrets; cookies are managed by browser/server
 */

import { API_BASE } from "./dataMode.ts";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name   = "ApiError";
    this.status = status;
  }
}

export class AuthError extends ApiError {
  constructor() {
    super(401, "Authentication required");
    this.name = "AuthError";
  }
}

export class ForbiddenError extends ApiError {
  constructor() {
    super(403, "Access denied");
    this.name = "ForbiddenError";
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { message?: string; error?: string };
    return body.message ?? body.error ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

export interface ApiFetchOptions extends RequestInit {
  redirectOn401?: boolean;
}

/**
 * Every outbound call is bounded. An unanswered request must surface as a
 * failure, not as a skeleton that spins forever. Measured worst case on
 * production (24h telemetry for a device with real history) is ~37 s, so the
 * bound sits above it; SSE is a separate, unbounded stream by design.
 */
const REQUEST_TIMEOUT_MS = 60_000;

export async function apiFetch<T>(
  path: string,
  options?: ApiFetchOptions,
): Promise<T> {
  const { redirectOn401 = true, ...init } = options ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      credentials: "include", // HttpOnly session cookies, no manual token handling
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        ...init.headers,
      },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, `Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401) {
    // Session expired or not authenticated: redirect to login if enabled
    // Never silently fall back to mock data or an unauthenticated state
    if (redirectOn401 && typeof window !== "undefined" && window.location) {
      window.location.href = "/login";
    }
    throw new AuthError();
  }

  if (res.status === 403) {
    throw new ForbiddenError();
  }

  if (!res.ok) {
    const msg = await parseError(res);
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const http = {
  get: <T>(path: string, options?: ApiFetchOptions) => apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: ApiFetchOptions) =>
    apiFetch<T>(path, {
      ...options,
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string, options?: ApiFetchOptions) => apiFetch<T>(path, { ...options, method: "DELETE" }),
};
export const get = http.get;
/**
 * Open an SSE connection to a backend endpoint.
 * Returns an EventSource; the caller is responsible for closing it.
 *
 * SSE uses cookie credentials automatically in same-origin deployments.
 * For cross-origin, use the fetch-based SSE polyfill if needed.
 */
export function openSse(path: string): EventSource {
  return new EventSource(`${API_BASE}${path}`, { withCredentials: true });
}
