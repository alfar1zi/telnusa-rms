import assert from "node:assert/strict";
import test from "node:test";

// the focus guard must fire exactly once per granted terminal URL.
// Fresh load and reconnect each mint a new URL, so a new URL re-arms focus;
// re-renders with the same URL must not steal focus back.
function makeFocusGuard() {
  let focusedUrl: string | null = null;
  let calls = 0;
  const focusTerminal = (terminalUrl: string | null, focus: () => void) => {
    if (focusedUrl === terminalUrl) return false;
    focus();
    calls += 1;
    focusedUrl = terminalUrl;
    return true;
  };
  return { focusTerminal, calls: () => calls };
}

test("first load focuses once, repeat load events do not refocus", () => {
  const g = makeFocusGuard();
  let n = 0;
  assert.equal(g.focusTerminal("https://nms/guacamole/#/client/A", () => { n += 1; }), true);
  assert.equal(g.focusTerminal("https://nms/guacamole/#/client/A", () => { n += 1; }), false);
  assert.equal(n, 1);
  assert.equal(g.calls(), 1);
});

test("reconnect with a new URL re-arms focus exactly once", () => {
  const g = makeFocusGuard();
  let n = 0;
  g.focusTerminal("https://nms/guacamole/#/client/A", () => { n += 1; });
  assert.equal(g.focusTerminal("https://nms/guacamole/#/client/B", () => { n += 1; }), true);
  assert.equal(g.focusTerminal("https://nms/guacamole/#/client/B", () => { n += 1; }), false);
  assert.equal(n, 2);
});
