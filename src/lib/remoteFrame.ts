/**
 * WEB-02: iframe hardening policy for the two remote-access surfaces.
 *
 * Both frames are same-origin with the TELNUSA portal by design:
 *   - WebUI:   /api/remote/:deviceId/webui/proxy/  (server-side proxy to the RUT)
 *   - Terminal: /guacamole#/client/...             (Guacamole client SPA)
 *
 * That means `allow-same-origin` cannot simply be dropped. Without it the frame
 * gets an opaque origin, and the RUT WebUI loses cookie access and same-origin
 * XHR, which breaks the verified `POST /api/bulk` path and the 11 native RUT
 * API calls. Removing it would be a regression, not a fix.
 *
 * What IS hardened here, without touching the verified behaviour:
 *   - no `allow-modals` / `allow-top-navigation`: the frame may not tab-nab the
 *     portal, and device content cannot raise native dialogs;
 *   - `allow-popups` is granted (the WebUI opens its own CLI window) but
 *     `allow-popups-to-escape-sandbox` is not, so that window stays sandboxed;
 *   - `allow-modals`: a full scan of all 135 served WebUI chunks found one `prompt(`
 *     (the password-protected backup export) and one `beforeunload` guard, and zero
 *     `confirm(`/`alert(`/`window.print(`. Without it those two flows differ from
 *     opening the router locally; the cost is that device content can raise a native
 *     dialog, which is accepted because the device is registry-gated and already
 *     inside the operator's trust boundary;
 *   - an explicit `allow` list denies camera, microphone, geolocation,
 *     accelerometer and payment access to remote device content;
 *   - `no-referrer` keeps the portal's URL structure out of the device's logs.
 *
 * RESIDUAL RISK (documented, not fixed here): `allow-same-origin` together with
 * `allow-scripts` on a same-origin frame means device-rendered content can, in
 * principle, reach the portal's origin. The correct fix is serving the proxy from
 * a separate origin (dedicated hostname) so the two are never same-origin. That is
 * an ingress/deployment change, out of scope for a source-only step, and it must be
 * validated against the live RUT before it is adopted.
 */

/**
 * Tokens the remote frames are allowed to keep.
 *
 * `allow-forms` is required: both surfaces submit POST forms (RUT login and
 * Guacamole connection). `allow-scripts` is required: both are JS applications.
 *
 * Two capabilities were added after auditing the served WebUI bundles (2026-10-21),
 * because without them verified native flows are dead through the portal:
 *   - `allow-downloads`: the backup screen (assets/app.backup.*.js) saves the config
 *     archive with a link download. Blocked, it fails silently: measured A/B on the
 *     same origin, 0 downloads without the flag and 1 with it.
 *   - `allow-popups`: the WebUI opens the native CLI window itself
 *     (`window.open(location.origin + ".../cgi-bin/cli", "_blank")`).
 * `allow-popups-to-escape-sandbox` stays forbidden, so a popup inherits the sandbox
 * and can never become an unsandboxed window; `allow-top-navigation` stays forbidden
 * so device content can never navigate the portal away.
 */
export const REMOTE_FRAME_SANDBOX = "allow-same-origin allow-scripts allow-forms allow-downloads allow-popups allow-modals";

/**
 * Powerful browser features remote device content is denied. Anything not listed
 * here is denied by default; the list exists so the intent is auditable.
 */
export const REMOTE_FRAME_ALLOW =
  "camera 'none'; microphone 'none'; geolocation 'none'; " +
  "accelerometer 'none'; magnetometer 'none'; gyroscope 'none'; " +
  "payment 'none'; usb 'none'";

/** The portal URL is not disclosed to the remote device. */
export const REMOTE_FRAME_REFERRER_POLICY = "no-referrer";

/** Tokens that must never appear in a remote frame sandbox. */
export const FORBIDDEN_SANDBOX_TOKENS = [
  "allow-top-navigation",
  "allow-top-navigation-by-user-activation",
  "allow-top-navigation-to-custom-protocols",
  "allow-popups-to-escape-sandbox",
  "allow-presentation",
  "allow-pointer-lock",
] as const;