import assert from "node:assert/strict";
import test from "node:test";
import { FORBIDDEN_SANDBOX_TOKENS, REMOTE_FRAME_SANDBOX } from "./remoteFrame.ts";

test("remote frame sandbox carries the capabilities the device UI needs", () => {
  const tokens = REMOTE_FRAME_SANDBOX.split(" ");
  for (const required of ["allow-same-origin", "allow-scripts", "allow-forms"]) {
    assert.ok(tokens.includes(required), `${required} is required by both surfaces`);
  }
  // Audited against the served RUT bundles: the backup screen saves an archive with a
  // link download, and the WebUI opens its own CLI window. Without these the flows
  // fail silently inside the frame.
  assert.ok(tokens.includes("allow-downloads"), "the backup config download needs allow-downloads");
  assert.ok(tokens.includes("allow-popups"), "the WebUI CLI window needs allow-popups");
  assert.ok(tokens.includes("allow-modals"), "the password-protected backup export needs a prompt");
});

test("remote frame sandbox never gains an escaping or navigation token", () => {
  const tokens = REMOTE_FRAME_SANDBOX.split(" ");
  for (const forbidden of FORBIDDEN_SANDBOX_TOKENS) {
    assert.ok(!tokens.includes(forbidden), `${forbidden} must never be granted to device content`);
  }
  // A popup must inherit the sandbox: escaping would turn it into an unsandboxed
  // window on the portal origin.
  assert.ok(!tokens.includes("allow-popups-to-escape-sandbox"));
  assert.ok(!tokens.includes("allow-top-navigation"));
});
