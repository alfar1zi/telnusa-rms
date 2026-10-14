/**
 * DATA MODE
 *
 * Controlled by the build-time env variable VITE_DATA_MODE.
 * - "mock": uses deterministic fixture data; safe for development/testing
 * - "real": calls the TELNUSA backend API; required for production
 *
 * Production builds default to "real".
 * REAL MODE MUST NEVER silently fall back to mock data.
 *
 * Only public configuration (API base path) belongs in VITE_* variables.
 * NEVER put secrets, credentials, or tokens in VITE_* variables.
 */

export type DataMode = "mock" | "real";

function resolveMode(): DataMode {
  const raw = typeof import.meta !== "undefined" && import.meta.env ? import.meta.env.VITE_DATA_MODE : undefined;
  if (raw === "mock") return "mock";
  // Production default: real. Unknown/unset values treated as real.
  return "real";
}
export const DATA_MODE: DataMode = resolveMode();

export const IS_MOCK = DATA_MODE === "mock";
export const IS_REAL = DATA_MODE === "real";

// API base URL: public config only; no auth tokens here
export const API_BASE: string =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE) || "/api";
