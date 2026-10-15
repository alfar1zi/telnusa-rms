/**
 * Frontend contract verification suite.
 * Asserts data contracts, time ranges, formatters, and API boundary behavior.
 */
import { TIME_RANGES, filterByTimeRange } from "./utils/timeRange.ts";
import { formatTimestamp, formatRelative, formatValue } from "./utils/format.ts";
import { IS_MOCK, IS_REAL, DATA_MODE } from "./services/dataMode.ts";
import { apiFetch, AuthError } from "./services/http.ts";
import { getAuthUser, changePassword } from "./services/api.ts";
import { axisFor } from "./utils/chartConfig.ts";
function assert(condition: boolean, msg: string) {
  if (!condition) {
    throw new Error(`Assertion failed: ${msg}`);
  }
}

// 1. Time range contract
assert(TIME_RANGES["24h"].ms === 24 * 3_600_000, "24h duration in ms");
assert(TIME_RANGES["1h"].ms === 60 * 60_000, "1h duration in ms");
const mockSeries = Array.from({ length: 288 }, (_, i) => ({ ts: 1700000000000 + i * 300_000, value: i }));
const filtered1h = filterByTimeRange(mockSeries, TIME_RANGES["1h"].ms);
assert(filtered1h.length === 13, "1h filter includes boundary point");

// 2. Formatters
const ts = 1700000000000;
const formatted = formatTimestamp(ts);
assert(typeof formatted === "string" && formatted.length > 0, "formatTimestamp returns string");
assert(formatValue(24.523, "V", 2) === "24.52 V", "formatValue formats with 2 decimals");
assert(formatValue(null, "V") === "NO DATA", "formatValue formats null as NO DATA");
assert(typeof formatRelative(ts) === "string", "formatRelative returns string");
assert(typeof IS_MOCK === "boolean", "IS_MOCK is boolean");
assert(typeof IS_REAL === "boolean", "IS_REAL is boolean");
assert(DATA_MODE === "mock" || DATA_MODE === "real", "DATA_MODE valid");

// 2b. Chart axis policy (one place for every chart on every page)
assert(axisFor("mppt.pvInputPower").min === 0, "produced power keeps a zero baseline");
assert(axisFor("pzem.energy").min === 0, "a filled energy area keeps a zero baseline");
assert(axisFor("solar.radiation").min === 0, "irradiance keeps a zero baseline");
for (const path of ["mppt.pvVoltage", "mppt.outputVoltage", "pzem.phaseAVoltage", "pzem.phaseCCurrent"]) {
  assert(axisFor(path).min === undefined, `${path} is a band gauge and must not be zero-based`);
}

// 3. Auth Fetch & 401 Redirect Opt-Out Contract
async function testAuthFetchContracts() {
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as unknown as { window?: { location: { href: string } } }).window;

  const mockLocation = { href: "http://localhost:3000/login" };
  (globalThis as unknown as { window: { location: { href: string } } }).window = { location: mockLocation };

  try {
    // A. 401 on /auth/me with redirectOn401: false -> throws AuthError but does NOT redirect window.location
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ error: "Authentication required." }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });

    mockLocation.href = "http://localhost:3000/login";
    let caughtAuthError = false;
    try {
      await apiFetch("/auth/me", { redirectOn401: false });
    } catch (err) {
      if (err instanceof AuthError) {
        caughtAuthError = true;
      }
    }
    assert(caughtAuthError, "apiFetch with redirectOn401: false throws AuthError on 401");
    assert(
      mockLocation.href === "http://localhost:3000/login",
      "apiFetch with redirectOn401: false must not change window.location.href",
    );

    // B. getAuthUser() calls /auth/me with redirectOn401: false and returns null without redirecting
    mockLocation.href = "http://localhost:3000/login";
    const userResult = await getAuthUser();
    assert(userResult === null, "getAuthUser() returns null on 401");
    assert(
      mockLocation.href === "http://localhost:3000/login",
      "getAuthUser() must not trigger navigation on 401",
    );

    // C. 401 on protected endpoint (default redirectOn401: true) -> redirects window.location.href = "/login"
    let caughtProtectedError = false;
    try {
      await apiFetch("/devices");
    } catch (err) {
      if (err instanceof AuthError) {
        caughtProtectedError = true;
      }
    }
    assert(caughtProtectedError, "Protected apiFetch throws AuthError on 401");
    assert(
      mockLocation.href === "/login",
      "Protected apiFetch redirects to /login on 401",
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalWindow) {
      (globalThis as unknown as { window: unknown }).window = originalWindow;
    } else {
      delete (globalThis as unknown as { window?: unknown }).window;
    }
  }
}

await testAuthFetchContracts();

// 4. changePassword CSRF & Payload Contract
async function testChangePasswordContracts() {
  const originalFetch = globalThis.fetch;
  const originalLocalStorage = (globalThis as Record<string, unknown>).localStorage;
  const originalSessionStorage = (globalThis as Record<string, unknown>).sessionStorage;

  const mockStorage = new Map<string, string>();
  const storageShim = {
    getItem: (k: string) => mockStorage.get(k) ?? null,
    setItem: (k: string, v: string) => mockStorage.set(k, String(v)),
    removeItem: (k: string) => mockStorage.delete(k),
    clear: () => mockStorage.clear(),
    get length() { return mockStorage.size; },
    key: (i: number) => Array.from(mockStorage.keys())[i] ?? null,
  };
  (globalThis as Record<string, unknown>).localStorage = storageShim;
  (globalThis as Record<string, unknown>).sessionStorage = storageShim;

  const fetchCalls: { url: string; method?: string; headers?: Record<string, string>; body?: string }[] = [];

  try {
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const headers = (init?.headers as Record<string, string>) || {};
      fetchCalls.push({
        url,
        method: init?.method,
        headers,
        body: typeof init?.body === "string" ? init?.body : undefined,
      });

      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrfToken: "csrf-sec-token-999" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/auth/change-password")) {
        if (headers["X-CSRF-Token"] !== "csrf-sec-token-999") {
          return new Response(JSON.stringify({ error: "CSRF token required." }), {
            status: 403,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("Not Found", { status: 404 });
    };

    const result = await changePassword({
      currentPassword: "OldPassword123!",
      newPassword: "NewPassword456!",
    });

    assert(result.ok === true, "changePassword returns { ok: true } on success");
    assert(fetchCalls.length === 2, "changePassword performs exactly 2 fetch calls (GET /csrf then POST /change-password)");
    assert(fetchCalls[0].url.endsWith("/auth/csrf"), "First call must be GET /auth/csrf");
    assert(!fetchCalls[0].method || fetchCalls[0].method === "GET", "First call method is GET");
    assert(fetchCalls[1].url.endsWith("/auth/change-password"), "Second call must be POST /auth/change-password");
    assert(fetchCalls[1].method === "POST", "Second call method is POST");
    assert(fetchCalls[1].headers?.["X-CSRF-Token"] === "csrf-sec-token-999", "X-CSRF-Token header matches retrieved token");

    const parsedBody = JSON.parse(fetchCalls[1].body || "{}");
    assert(parsedBody.currentPassword === "OldPassword123!", "currentPassword preserved in POST body");
    assert(parsedBody.newPassword === "NewPassword456!", "newPassword preserved in POST body");
    assert(mockStorage.size === 0, "No password or CSRF token stored in localStorage/sessionStorage");
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as Record<string, unknown>).localStorage = originalLocalStorage;
    (globalThis as Record<string, unknown>).sessionStorage = originalSessionStorage;
  }
}

await testChangePasswordContracts();

console.log("ALL FRONTEND CONTRACT TESTS PASSED");
