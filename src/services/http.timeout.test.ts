import test from "node:test";
import assert from "node:assert/strict";
import { apiFetch, ApiError } from "./http.ts";

/**
 * A request the backend never answers must fail, not hang: production history
 * queries can take tens of seconds, and a skeleton without a bound is
 * indistinguishable from a dead page.
 */
test("apiFetch converts an unanswered request into a bounded failure", async () => {
  const realFetch = globalThis.fetch;
  const realSetTimeout = globalThis.setTimeout;

  globalThis.fetch = ((_url: string, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      const signal = init?.signal;
      if (!signal) return;
      if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })) as typeof fetch;

  // Collapse the 60 s bound so the abort fires immediately.
  globalThis.setTimeout = ((fn: () => void) => {
    fn();
    return 0 as unknown as ReturnType<typeof setTimeout>;
  }) as unknown as typeof setTimeout;

  try {
    await assert.rejects(
      () => apiFetch("/devices"),
      (error: unknown) => error instanceof ApiError && error.status === 0 && /timed out/.test(error.message),
    );
  } finally {
    globalThis.fetch = realFetch;
    globalThis.setTimeout = realSetTimeout;
  }
});
