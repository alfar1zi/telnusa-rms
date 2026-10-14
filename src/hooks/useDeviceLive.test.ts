import assert from "node:assert/strict";
import test from "node:test";
import { parseSseFrame } from "../lib/sseFrame.ts";

// SSE-01: the previous implementation cast `JSON.parse(e.data) as SseEvent`, so any
// well-formed JSON reached the UI as if it were a real device frame. Every case
// below is something that must be rejected rather than rendered.
test("SSE-01: a well-formed telemetry frame is accepted", () => {
  const frame = parseSseFrame(JSON.stringify({
    type: "telemetry",
    payload: { deviceId: "RUT-A", timestamp: 1, mppt: {}, pzem: {} },
  }));
  assert.equal(frame?.type, "telemetry");
});

test("SSE-01: non-JSON and empty bodies are rejected", () => {
  assert.equal(parseSseFrame("<!DOCTYPE html><html>captive portal</html>"), null);
  assert.equal(parseSseFrame(""), null);
  assert.equal(parseSseFrame(undefined), null);
  assert.equal(parseSseFrame(null), null);
  assert.equal(parseSseFrame(42), null);
});

test("SSE-01: an unknown event type is rejected, not silently rendered", () => {
  assert.equal(parseSseFrame(JSON.stringify({
    type: "totally_made_up", payload: { deviceId: "RUT-A" },
  })), null);
});

test("SSE-01: a missing or non-string type is rejected", () => {
  assert.equal(parseSseFrame(JSON.stringify({ payload: { deviceId: "RUT-A" } })), null);
  assert.equal(parseSseFrame(JSON.stringify({ type: 7, payload: { deviceId: "RUT-A" } })), null);
});

test("SSE-01: a payload without deviceId cannot be attributed and is dropped", () => {
  assert.equal(parseSseFrame(JSON.stringify({ type: "telemetry", payload: {} })), null);
  assert.equal(parseSseFrame(JSON.stringify({ type: "status", payload: { deviceId: 9 } })), null);
});

test("SSE-01: non-object payloads and frames are rejected", () => {
  assert.equal(parseSseFrame(JSON.stringify({ type: "telemetry", payload: null })), null);
  assert.equal(parseSseFrame(JSON.stringify({ type: "telemetry", payload: [1, 2] })), null);
  assert.equal(parseSseFrame(JSON.stringify({ type: "telemetry" })), null);
  assert.equal(parseSseFrame(JSON.stringify([{ type: "telemetry" }])), null);
  assert.equal(parseSseFrame(JSON.stringify("telemetry")), null);
  assert.equal(parseSseFrame(JSON.stringify(null)), null);
});

// A resync marker is the server saying "your buffer was truncated". It has no
// deviceId by design, so the attribution check must not reject it.
test("SSE-01: a resync marker is accepted without a deviceId", () => {
  const frame = parseSseFrame(JSON.stringify({
    type: "resync", payload: { reason: "buffer_overflow" },
  }));
  assert.equal(frame?.type, "resync");
});