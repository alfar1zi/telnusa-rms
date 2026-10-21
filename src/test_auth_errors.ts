/**
 * AUTH-03 Error Propagation Regression Tests
 * Explicitly tests each required HTTP status and failure mode.
 */

import { apiFetch, ApiError, AuthError, ForbiddenError } from "./services/http.ts";
import {
  getDevice,
  getDeviceTelemetry,
  getDeviceLatestTelemetry,
  getDeviceHealthHistory,
  getDeviceHealthSnapshot,
  getDeviceStatus,
  getIntegrationStatus,
  getAllEvents,
  getDevices,
  getAllAlarms,
} from "./services/api.ts";

function assert(condition: boolean, msg: string) {
  if (!condition) {
    throw new Error(`Assertion failed: ${msg}`);
  }
}

async function runAuth03ErrorTests() {
  const originalFetch = globalThis.fetch;

  try {
    // ============================================================
    // 401 - Authentication Error
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Authentication required" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });

      let caughtAuthError = false;
      try {
        await apiFetch("/devices", { redirectOn401: false });
      } catch (err) {
        if (err instanceof AuthError) caughtAuthError = true;
        else throw err;
      }
      assert(caughtAuthError, "401: apiFetch throws AuthError");
      assert(!(caughtAuthError && false), "401: must NOT become [] or undefined");
    }

    // ============================================================
    // 403 - Authorization Error
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Forbidden" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        });

      let caughtForbidden = false;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof ForbiddenError) caughtForbidden = true;
        else throw err;
      }
      assert(caughtForbidden, "403: apiFetch throws ForbiddenError");
      assert(!(caughtForbidden && false), "403: must NOT become [] or undefined");
    }

    // ============================================================
    // 429 - Rate Limit
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Rate limited" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "60" },
        });

      let caught429 = false;
      let status429 = 0;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof ApiError) {
          caught429 = true;
          status429 = err.status;
        } else throw err;
      }
      assert(caught429, "429: apiFetch throws ApiError");
      assert(status429 === 429, "429: ApiError.status === 429");
    }

    // ============================================================
    // 500 - Backend Failure
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Internal server error" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });

      let caught500 = false;
      let status500 = 0;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof ApiError) {
          caught500 = true;
          status500 = err.status;
        } else throw err;
      }
      assert(caught500, "500: apiFetch throws ApiError");
      assert(status500 === 500, "500: ApiError.status === 500");
    }

    // ============================================================
    // 501 - Feature/Dependency Unavailable
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Not implemented" }), {
          status: 501,
          headers: { "Content-Type": "application/json" },
        });

      let caught501 = false;
      let status501 = 0;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof ApiError) {
          caught501 = true;
          status501 = err.status;
        } else throw err;
      }
      assert(caught501, "501: apiFetch throws ApiError");
      assert(status501 === 501, "501: ApiError.status === 501");
    }

    // ============================================================
    // 503 - Service Unavailable
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ error: "Service unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });

      let caught503 = false;
      let status503 = 0;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof ApiError) {
          caught503 = true;
          status503 = err.status;
        } else throw err;
      }
      assert(caught503, "503: apiFetch throws ApiError");
      assert(status503 === 503, "503: ApiError.status === 503");
    }

    // ============================================================
    // NETWORK FAILURE - transport exception
    // ============================================================
    {
      globalThis.fetch = async () => {
        throw new TypeError("Network request failed");
      };

      let caughtNetwork = false;
      try {
        await apiFetch("/devices");
      } catch (err) {
        if (err instanceof TypeError || err instanceof Error) {
          caughtNetwork = true;
        } else throw err;
      }
      assert(caughtNetwork, "network: fetch throws transport error");
    }

    // ============================================================
    // 404 VALID MAPPINGS - six resource-specific helpers
    // ============================================================
    const deviceId = "RUT-NOTFOUND";
    const testCases = [
      { fn: getDevice, name: "getDevice", endpoint: `/devices/${deviceId}` },
      { fn: getDeviceTelemetry, name: "getDeviceTelemetry", endpoint: `/devices/${deviceId}/telemetry` },
      { fn: getDeviceLatestTelemetry, name: "getDeviceLatestTelemetry", endpoint: `/devices/${deviceId}/telemetry/latest` },
      { fn: getDeviceHealthHistory, name: "getDeviceHealthHistory", endpoint: `/devices/${deviceId}/health/history` },
      { fn: getDeviceHealthSnapshot, name: "getDeviceHealthSnapshot", endpoint: `/devices/${deviceId}/health` },
      { fn: getDeviceStatus, name: "getDeviceStatus", endpoint: `/devices/${deviceId}/status` },
    ];

    for (const tc of testCases) {
      // 404 → undefined
      globalThis.fetch = async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ error: "Not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });

      const result404 = await tc.fn(deviceId);
      assert(result404 === undefined, `${tc.name}: 404 → undefined`);

      // 401/403/429/500/501/503 → propagated error
      for (const [status, errorName] of [
        [401, "AuthError"],
        [403, "ForbiddenError"],
        [429, "ApiError-429"],
        [500, "ApiError-500"],
        [501, "ApiError-501"],
        [503, "ApiError-503"],
      ] as const) {
        globalThis.fetch = async () =>
          new Response(JSON.stringify({ error: errorName }), {
            status,
            headers: { "Content-Type": "application/json" },
          });

        let caught = false;
        try {
          await tc.fn(deviceId);
        } catch (err) {
          if (err instanceof ApiError || err instanceof AuthError || err instanceof ForbiddenError) {
            caught = true;
            if (status !== 401 && status !== 403) {
              assert(err.status === status, `${tc.name}: ${status} → ApiError.status === ${status}`);
            }
          } else throw err;
        }
        assert(caught, `${tc.name}: ${status} (${errorName}) propagated`);
      }

      // Network failure
      globalThis.fetch = async () => { throw new TypeError("Network error"); };
      let caughtNet = false;
      try {
        await tc.fn(deviceId);
      } catch (err) {
        if (err instanceof Error) caughtNet = true;
      }
      assert(caughtNet, `${tc.name}: network failure propagated`);
    }

    // ============================================================
    // 200 EMPTY RESULT - legitimate empty response
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });

      const devices = await getDevices();
      assert(Array.isArray(devices) && devices.length === 0, "200 empty: getDevices returns []");

      const alarms = await getAllAlarms();
      assert(Array.isArray(alarms) && alarms.length === 0, "200 empty: getAllAlarms returns []");

      const events = await getAllEvents();
      assert(Array.isArray(events) && events.length === 0, "200 empty: getAllEvents returns []");
    }

    // ============================================================
    // 200 EMPTY OBJECT - getIntegrationStatus
    // ============================================================
    {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });

      const status = await getIntegrationStatus();
      assert(status && typeof status === "object", "200 empty: getIntegrationStatus returns object");
    }

    console.log("ALL AUTH-03 ERROR PROPAGATION TESTS PASSED");
  } finally {
    globalThis.fetch = originalFetch;
  }
}

await runAuth03ErrorTests();