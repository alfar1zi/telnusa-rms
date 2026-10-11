from __future__ import annotations

import gzip
import http.client
import json
import logging
import hmac
import re
import queue
import secrets
import ssl
import threading
import time
import traceback
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("telnusa.app")
from .auth_util import (RateLimiter, dummy_password_hash, hash_password, new_token, safe_role,
                        valid_email, validate_password_strength, verify_password,
                        verify_password_allowing_legacy)
from .config import config, startup_state
from .httputil import (ApiProblem, clear_session_cookie, client_ip, json_body,
                       parse_cookies, send_bytes, send_json, session_cookie)
from .guacamole import client_url, configured as guacamole_configured, issue_terminal_grant
from .mqtt_collector import start_collector
from .notifications import get_email_provider
from .processor import (StatusAgingWorker, ZabbixDeliveryWorker, integration_status,
                        process_payload)
from .remote import resolve_device_target
from .rollup import build_telemetry_history_response
from .sse import SubscriptionRejected, broker
from .store import Store
from .tunnel import TunnelMaintenanceWorker, TunnelState, tunnel_manager
from .xlsx_report import build_report_workbook, jakarta_label
from .zabbix_client import METRIC_NOT_INTEGRATED, ZabbixError, zabbix

store = Store(config.DB_PATH)
tunnel_manager.store = store
login_limiter = RateLimiter(config.LOGIN_RATE_LIMIT)
api_limiter = RateLimiter("120/60")

ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}

# Export registry for the Reports page. Labels and number formats are data, not
# copy: the CSV header line is English for the same reason, and the precision per
# metric matches what the interface shows for it.
REPORT_METRICS: dict[str, tuple[str, str, str, str]] = {
    "pvInputPower":  ("PV input power",   "mppt",  "W",    "0.00"),
    "outputPower":   ("Output power",     "mppt",  "W",    "0.00"),
    "pvVoltage":     ("PV voltage",       "mppt",  "V",    "0.00"),
    "pvCurrent":     ("PV current",       "mppt",  "A",    "0.00"),
    "outputVoltage": ("Output voltage",   "mppt",  "V",    "0.00"),
    "outputCurrent": ("Output current",   "mppt",  "A",    "0.00"),
    "phaseAVoltage": ("Phase A voltage",  "pzem",  "V",    "0.0"),
    "phaseBVoltage": ("Phase B voltage",  "pzem",  "V",    "0.0"),
    "phaseCVoltage": ("Phase C voltage",  "pzem",  "V",    "0.0"),
    "phaseACurrent": ("Phase A current",  "pzem",  "A",    "0.00"),
    "phaseBCurrent": ("Phase B current",  "pzem",  "A",    "0.00"),
    "phaseCCurrent": ("Phase C current",  "pzem",  "A",    "0.00"),
    "energy":        ("Energy counter",   "pzem",  "kWh",  "0.000"),
    "radiation":     ("Solar irradiance", "solar", "W/m2", "0"),
}


def public_user(row) -> dict:
    d = row if isinstance(row, dict) else store.row_dict(row)
    return {
        "id": str(d["id"]),
        "email": d["email"],
        "name": d.get("display_name", ""),
        "role": d.get("role", "viewer"),
        "provider": d.get("provider", "local"),
        "mustChangePassword": bool(d.get("must_change_password", 0)),
        "disabled": bool(d.get("disabled", 0)),
        "createdAt": d.get("created_at", 0),
        "updatedAt": d.get("updated_at", 0),
    }


def bootstrap_admin(target_store: Store | None = None) -> None:
    s = target_store or store
    email = config.BOOTSTRAP_ADMIN_EMAIL.strip().lower()
    password = config.BOOTSTRAP_ADMIN_PASSWORD
    if not email or not password:
        return
    if not valid_email(email):
        raise RuntimeError("NMS_BOOTSTRAP_ADMIN_EMAIL is invalid")
    existing = s.get_user_by_email(email)
    if existing:
        if existing["role"] != "admin":
            s.set_user_role(existing["id"], "admin")
            s.audit(email, "role.bootstrap_admin", "promoted existing user")
        return
    # Only the account creation path is strength-checked: a weak break-glass password
    # must fail loudly at startup rather than be seeded as a hashed admin secret.
    valid, msg = validate_password_strength(password)
    if not valid:
        raise RuntimeError(f"NMS_BOOTSTRAP_ADMIN_PASSWORD rejected: {msg}")
    uid = s.create_user(
        email=email,
        display_name="Administrator",
        password_hash=hash_password(password),
        role="admin",
        provider="local",
        must_change_password=1,
        disabled=0,
    )
    s.audit(email, "auth.bootstrap_admin.created", f"user_id={uid}")

def now_ms() -> int:
    return int(time.time() * 1000)

def filter_proxy_cookies(raw_cookie: str | None) -> str:
    if not raw_cookie:
        return ""
    parts = raw_cookie.split(";")
    keep = []
    for part in parts:
        trimmed = part.strip()
        if not trimmed:
            continue
        cookie_name = trimmed.split("=", 1)[0].strip()
        if cookie_name.lower() == "nms_session":
            continue
        keep.append(trimmed)
    return "; ".join(keep)

# Root-absolute resource paths emitted by the RUT WebUI that escape the proxy prefix.
# Deliberately an allowlist: the same bundles also carry router paths ("/status/...",
# "/system/...") and Linux filesystem paths ("/tmp/modbus_db", "/usr/local/share/...")
# that must resolve unchanged, so rewriting every "/..." literal would corrupt
# pushState targets and displayed paths.
# ponytail: enumerated from RUT906 firmware 07.24.3. A firmware upgrade can add new
# resource roots; re-derive this list from a browser network trace when that happens.
RUT_WEBUI_RESOURCE_PREFIXES = (
    "/assets/", "/cgi-bin/", "/fonts/", "/icons/",
    "/tlt_networks_logo.svg", "/favicon-light.svg", "/favicon-dark.svg",
)
_RUT_RES_ALT = "|".join(re.escape(p) for p in RUT_WEBUI_RESOURCE_PREFIXES)
# Quote-anchored so only complete string literals are touched; the captured path is
# re-emitted verbatim, keeping quote/backtick balance.
RUT_WEBUI_JS_PATH_RE = re.compile(r'(["\'`])(' + _RUT_RES_ALT + ')')
# Bare url(/fonts/...) and quoted url('/icons/...') both occur in the real stylesheet.
RUT_WEBUI_CSS_URL_RE = re.compile(r'url\(([\'"]?)(' + _RUT_RES_ALT + ')')
# Vite bakes its preload base as a literal "/" join, which turns every lazy chunk into
# a root-absolute URL that <base href> cannot rescue.
RUT_WEBUI_VITE_BASE_RE = re.compile(r'return"/"\+([A-Za-z_$][\w$]*)\}')

# Navigation built as origin + root-absolute path. <base href> cannot help: an
# absolute URL resolves against the origin, not the document. The router's login
# response is exactly this shape:
#   window.location = window.location.origin + '/status/overview'
# which, unrewritten, lands the operator on the PORTAL root after a successful login.
# Only assignments are rewritten, never bare comparisons: `location.pathname` in this
# SPA is compared against unprefixed literals, and rewriting those would break routing
# in a different way. Closing that fully needs the frame on its own origin.
RUT_WEBUI_NAV_ASSIGN_RE = re.compile(
    r"(location\.(?:href|assign|replace)\s*=\s*|location\.(?:assign|replace)\()"
    r"(['\"])(/(?!api/remote/).*?)\2"
)
RUT_WEBUI_ORIGIN_NAV_RE = re.compile(r"(location\.origin\s*\+\s*)(['\"])(/(?!api/remote/).*?)\2")

# Headers that describe the DEVICE's own security policy and must not be replayed as
# if they were the portal's: HSTS with includeSubDomains/preload would apply the
# router's policy to the portal host, and Expect-CT is retired. The device's CSP is
# forwarded as-is: its script hashes describe the router's original inline scripts,
# and no violation has ever been observed on the pages the proxy rewrites (checked in
# a real browser). ponytail: if a future firmware page reports a CSP hash violation,
# strip content-security-policy on rewritten bodies only.
DEVICE_POLICY_HEADERS = {"strict-transport-security", "expect-ct"}

# Streaming relay for the RUT event feed. A live feed is idle far more often than it is
# chatty, so the 15s per-request socket timeout would sever a healthy stream; the idle
# ceiling is raised instead of lifted outright so a dead upstream still frees the
# thread. ponytail: raise if the RUT is ever seen to pause longer than this mid-stream.
RUT_WEBUI_STREAM_CHUNK_BYTES = 8192
RUT_WEBUI_STREAM_IDLE_TIMEOUT_SECONDS = 300

# The RUT SPA batches its data requests into a single POST here and the RUT dispatches
# each sub-request server-side, against its own route table.
RUT_WEBUI_BULK_PATH = "/api/bulk"


def _header_value(headers, name):
    for k, v in headers:
        if k.lower() == name:
            return v
    return ""


def _is_rewritten_content_type(content_type):
    ct = (content_type or "").strip().lower()
    # An absent Content-Type counts as rewritten: a 304 that cannot be classified must
    # not be forwarded, because passing one through wrongly is the failure this guards.
    return not ct or "text/html" in ct or "javascript" in ct or "text/css" in ct


def restore_bulk_endpoint_paths(body: bytes, proxy_prefix: str) -> bytes:
    """Return a /api/bulk body with the proxy prefix removed from every sub-request
    endpoint, or the original bytes when there is nothing to restore.

    The RUT SPA builds every API URL from one string literal, and the bundle rewrite
    prefixes it so browser fetch() calls traverse the proxy. That literal is reused
    as the `endpoint` of a bulk sub-request -- but the RUT resolves those server-side
    against its own route table, which starts at /api/. A prefixed endpoint matches
    no route and returns code 122 "Endpoint not implemented" under a 207 envelope.

    Only `endpoint` is touched. Every other value in the payload is left alone, and a
    body with no matching prefix is returned as the same object so the caller can
    detect "unchanged" by identity.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body
    if not isinstance(payload, dict):
        return body
    entries = payload.get("data")
    if not isinstance(entries, list):
        return body
    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        endpoint = entry.get("endpoint")
        # An endpoint the SPA built natively needs no restore, and a non-string
        # endpoint is not ours to interpret.
        if not isinstance(endpoint, str) or not endpoint.startswith(proxy_prefix):
            continue
        # Slice, not regex: everything after the prefix -- including any query
        # string -- is already the RUT-native endpoint and must survive verbatim.
        native = endpoint[len(proxy_prefix):]
        if not native.startswith("/"):
            continue
        entry["endpoint"] = native
        changed = True
    if not changed:
        return body
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "TelnusaNMS/0.1"
    # A client that opens a connection and stalls must not pin a thread forever.
    timeout = 30
    # Without this, BaseHTTPRequestHandler.version_string() appends the running
    # interpreter version and the banner leaks through Apache onto every
    # response, including the proxied RUT906 WebUI.
    sys_version = ""

    def log_message(self, fmt, *args):
        # Avoid logging headers/cookies/body. Apache access log covers path/status.
        return

    def _problem(self, status: int, message: str,
                 headers: dict | None = None) -> None:
        send_json(self, status, {"error": message}, headers)

    def _remote_key(self) -> str:
        return client_ip(self, config.TRUST_PROXY, config.TRUSTED_PROXY_HOPS)

    def _session(self):
        sid = parse_cookies(self).get("nms_session")
        if not sid:
            return None, None
        sess = store.get_session(sid)
        if not sess:
            return None, None
        if sess["expires_at"] < now_ms():
            store.delete_session(sid)
            return None, None
        user = store.get_user_by_id(sess["user_id"])
        if not user or user["disabled"]:
            store.delete_session(sid)
            return None, None
        return sess, user

    def _require_user(self, allow_must_change: bool = False):
        sess, user = self._session()
        if not user:
            raise ApiProblem(401, "Authentication required.")
        if user["disabled"]:
            raise ApiProblem(403, "Account is disabled.")
        if user["must_change_password"] and not allow_must_change:
            raise ApiProblem(403, "Password change required.", {"X-Must-Change-Password": "true"})
        return sess, user

    def _require_role(self, role: str, allow_must_change: bool = False):
        sess, user = self._require_user(allow_must_change=allow_must_change)
        if ROLE_ORDER.get(user["role"], -1) < ROLE_ORDER.get(role, 99):
            raise ApiProblem(403, "Access denied.")
        return sess, user

    def _csrf(self, sess) -> None:
        token = self.headers.get("X-CSRF-Token")
        if not token or not secrets.compare_digest(token.encode("utf-8"), str(sess["csrf"]).encode("utf-8")):
            raise ApiProblem(403, "CSRF token required.")

    def _rate_api(self) -> None:
        key = f"api:{self._remote_key()}"
        if not api_limiter.allow(key):
            raise ApiProblem(429, "Too many requests.",
                             {"Retry-After": str(api_limiter.retry_after(key))})

    def _int_query(self, query, name: str, default: int, minimum: int = 1, maximum: int = 1000) -> int:
        """A malformed limit is a client error (400), never a 500."""
        vals = query.get(name)
        if not vals:
            return default
        try:
            return max(minimum, min(int(vals[0]), maximum))
        except (TypeError, ValueError):
            raise ApiProblem(400, f"Invalid query parameter: {name}.")

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self._handle_get()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_HEAD(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self._handle_get()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_OPTIONS(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self.send_response(204)
            self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD")
            self.end_headers()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_DELETE(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self._handle_delete()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_PATCH(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self._handle_patch()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_PUT(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self.do_PATCH()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            proxy_info = parse_remote_proxy_path(parsed.path)
            if proxy_info:
                dev_id, subpath = proxy_info
                return self._proxy_webui(dev_id, subpath, parsed.query)
            self._handle_post()
        except ApiProblem as exc:
            self._problem(exc.status, exc.message, exc.headers)
        except Exception:
            if config.DEBUG_ERRORS:
                traceback.print_exc()
            self._problem(500, "Internal server error.")
    def _handle_patch(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        self._rate_api()
        uid, action = route_user_action(path)
        if uid and action is None:
            # PATCH /api/users/:id
            return self._users_update(uid)
        raise ApiProblem(404, "Not found.")

    def _handle_delete(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        self._rate_api()
        uid, action = route_user_action(path)
        if uid and action is None:
            # DELETE /api/users/:id -> disable user
            return self._users_disable(uid)
        raise ApiProblem(404, "Not found.")

    def _handle_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/auth/me":
            return self._auth_me()
        if path == "/api/auth/csrf":
            return self._auth_csrf()
        if path == "/api/auth/providers":
            return send_json(self, 200, {
                "providers": {
                    "local": {"enabled": True},
                }
            })
        # SSE-01: the live route is rate limited like every other API route. A
        # long-lived stream consumes a connection, so an unbounded fan-out through
        # this endpoint is exactly what the per-device/total caps exist to prevent.
        if path.startswith("/api/live/"):
            self._rate_api()
            return self._live(path.rsplit("/", 1)[-1])

        self._rate_api()
        if path == "/api/reports/export.xlsx":
            return self._reports_export_xlsx(query)
        if path == "/api/users":
            return self._users_list()
        if path == "/api/fleet/summary":
            self._require_user()
            return send_json(self, 200, store.fleet_summary())
        if path == "/api/devices":
            self._require_user()
            return send_json(self, 200,
                             [device_public(r) for r in store.list_devices()])
        if path == "/api/alarms/rules":
            self._require_user()
            return send_json(self, 200, store.list_alarm_rules())
        if path == "/api/alarms/history":
            self._require_user()
            limit = self._int_query(query, "limit", 100)
            dev_id = query.get("device_id", [None])[0] or query.get("deviceId", [None])[0]
            alarms = store.list_alarms(device_id=dev_id, state="RESOLVED", limit=limit)
            return send_json(self, 200, [alarm_public(a) for a in alarms])
        if path == "/api/alarms":
            self._require_user()
            try:
                from .alarm_engine import AlarmEngine
                AlarmEngine(store).evaluate_all()
            except Exception:
                try:
                    from alarm_engine import AlarmEngine
                    AlarmEngine(store).evaluate_all()
                except Exception as exc:
                    logger.warning("alarm evaluation failed: %r", exc)
            state_filter = query.get("state", [None])[0]
            dev_filter = query.get("device_id", [None])[0] or query.get("deviceId", [None])[0]
            sev_filter = query.get("severity", [None])[0]
            rule_filter = query.get("rule_id", [None])[0] or query.get("ruleId", [None])[0]
            limit = self._int_query(query, "limit", 200)
            alarms = store.list_alarms(
                device_id=dev_filter,
                state=state_filter,
                severity=sev_filter,
                rule_id=rule_filter,
                limit=limit,
            )
            return send_json(self, 200, [alarm_public(a) for a in alarms])
        alarm_id_param, subaction = route_alarm_action(path)
        if alarm_id_param is not None and subaction is None:
            self._require_user()
            alarm = store.get_alarm(alarm_id_param)
            if not alarm:
                raise ApiProblem(404, f"Alarm {alarm_id_param} not found.")
            return send_json(self, 200, alarm_public(alarm))
        if path == "/api/events":
            self._require_user()
            if not zabbix.configured:
                raise ApiProblem(503, "Zabbix event integration not configured.")
            try:
                registered = [r["device_id"] for r in store.list_devices()]
                events = zabbix.scoped_events(None, registered_hosts=registered, limit=100, device_lookup=store.get_device)
            except ZabbixError:
                raise ApiProblem(503, "Zabbix event integration unavailable.")
            return send_json(self, 200, events)
        if path == "/api/integration/unmapped":
            self._require_role("operator")
            return send_json(self, 200, [dict(r) for r in store.list_unmapped()])
        if path == "/api/integration/status":
            self._require_role("operator")
            return send_json(self, 200, integration_status(store))
        if path == "/api/integration/metrics":
            self._require_role("operator")
            return send_json(self, 200, integration_status(store))
        if path == "/api/notifications/config":
            self._require_role("operator")
            provider = get_email_provider()
            return send_json(self, 200, {
                "provider": provider.name,
                "configured": provider.configured(),
                "state": provider.state,
                "senderEmail": config.GMAIL_SENDER_EMAIL or None,
            })
        if path == "/api/notifications/recipients":
            self._require_role("operator")
            return send_json(self, 200,
                             [dict(r) for r in store.list_recipients()])
        if path.startswith("/api/remote/"):
            sess, user = self._require_role("operator")
            # Trusted registry only: fixed /api/remote/:deviceId/(webui|terminal). No ?host=/?url= proxy. No SSRF.
            parts = path.split("/")
            if len(parts) == 5 and parts[4] in ("webui", "terminal"):
                device_id = urllib.parse.unquote(parts[3])
                access_type = parts[4]
                action = f"remote.{access_type}.open"

                target = resolve_device_target(device_id, store)
                if not target:
                    store.audit(user["email"], action, f"device_id={device_id} result=device_not_found")
                    raise ApiProblem(404, "Device not found.")
                if access_type == "webui":
                    info = tunnel_manager.ensure(device_id)
                    if info.state == TunnelState.READY:
                        store.audit(user["email"], action, f"device_id={device_id} port={info.local_port} result=ready")
                        return send_json(self, 200, {
                            "deviceId": device_id,
                            "status": "ready",
                            "url": f"/api/remote/{urllib.parse.quote(device_id)}/webui/proxy/",
                            "localPort": info.local_port,
                            "startedAt": info.started_at,
                        })
                    elif info.state == TunnelState.BLOCKED_CONFIG:
                        store.audit(user["email"], action, f"device_id={device_id} result=blocked_config error={info.error}")
                        return send_json(self, 501, {
                            "error": "REMOTE_NOT_CONFIGURED",
                            "detail": f"BLOCKED: MISSING EXTERNAL SERVICE. {info.error or 'SSH/ZeroTier remote access not configured.'}"
                        })
                    else:
                        store.audit(user["email"], action, f"device_id={device_id} result=failed error={info.error}")
                        return send_json(self, 503, {
                            "error": "REMOTE_TUNNEL_FAILED",
                            "detail": f"Remote tunnel failure: {info.error or 'Failed to establish tunnel.'}"
                        })

                # Terminal: Guacamole via signed-JSON auth (no stored credential,
                # no browser-supplied target; host/port/key all server-side).
                grant = issue_terminal_grant(user["email"], target)
                if grant.status != "ready":
                    store.audit(user["email"], action,
                                f"device_id={device_id} type={access_type} "
                                f"result=blocked error={grant.error}")
                    http_status = 501 if grant.error == "REMOTE_NOT_CONFIGURED" else 503
                    return send_json(self, http_status, {
                        "error": grant.error,
                        "detail": grant.detail,
                    })
                store.audit(user["email"], action,
                            f"device_id={device_id} type={access_type} result=ready")
                return send_json(self, 200, {
                    "deviceId": device_id,
                    "status": "ready",
                    "url": client_url(grant),
                    "connectionId": grant.connection_id,
                })
        dev_id = route_device(path)
        if dev_id:
            return self._device_get(path, dev_id, query)
        raise ApiProblem(404, "Not found.")

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/auth/login":
            return self._auth_login()
        if path == "/api/auth/logout":
            return self._auth_logout()
        if path == "/api/auth/change-password":
            return self._auth_change_password()

        self._rate_api()
        if path == "/api/users":
            return self._users_create()
        uid, action = route_user_action(path)
        if uid:
            if action == "reset-password":
                return self._users_reset_password(uid)
            if action == "revoke-sessions":
                return self._users_revoke_sessions(uid)

        if path == "/api/alarms/rules":
            sess, user = self._require_role("operator")
            self._csrf(sess)
            body = json_body(self)
            if not body.get("rule_id") or not body.get("name") or not body.get("metric"):
                raise ApiProblem(400, "rule_id, name, and metric are required.")
            store.upsert_alarm_rule(body)
            store.audit(user["email"], "alarm.rule.upsert", body["rule_id"])
            return send_json(self, 200, {"ok": True})
        alarm_id_param, alarm_action = route_alarm_action(path)
        if alarm_id_param is not None:
            if alarm_action == "ack":
                sess, user = self._require_role("operator")
                self._csrf(sess)
                body = json_body(self) if self.headers.get("content-length") else {}
                try:
                    alarm = store.acknowledge_alarm(alarm_id_param, actor=user["email"], note=body.get("note"))
                except ValueError as err:
                    raise ApiProblem(404, str(err))
                store.audit(user["email"], "alarm.acknowledge", str(alarm_id_param))
                return send_json(self, 200, alarm_public(alarm))
            elif alarm_action == "resolve":
                sess, user = self._require_role("operator")
                self._csrf(sess)
                body = json_body(self) if self.headers.get("content-length") else {}
                try:
                    alarm = store.manual_resolve_alarm(alarm_id_param, actor=user["email"], reason=body.get("reason"))
                except ValueError as err:
                    raise ApiProblem(404, str(err))
                store.audit(user["email"], "alarm.resolve", str(alarm_id_param))
                return send_json(self, 200, alarm_public(alarm))
        if path == "/api/integration/mqtt":
            sess, user = self._require_role("operator")
            self._csrf(sess)
            return self._mqtt_ingest(user["email"])
        if path == "/api/notifications/recipients":
            sess, user = self._require_role("operator")
            self._csrf(sess)
            body = json_body(self)
            email = body.get("email")
            if not valid_email(email):
                raise ApiProblem(400, "Valid email is required.")
            store.add_recipient(email, user["email"])
            store.audit(user["email"], "notification.recipient.add",
                        email.lower())
            return send_json(self, 201, {"ok": True})
        if path == "/api/telemetry/ingest":
            return self._http_ingest()
        raise ApiProblem(404, "Not found.")

    # -- auth -----------------------------------------------------------
    def _auth_me(self):
        sess, user = self._session()
        if not user:
            raise ApiProblem(401, "Authentication required.")
        return send_json(self, 200, public_user(user))

    def _auth_csrf(self):
        sess, _ = self._require_user(allow_must_change=True)
        return send_json(self, 200, {"csrfToken": sess["csrf"]})

    def _auth_login(self):
        remote_ip = self._remote_key()
        ip_key = f"login:ip:{remote_ip}"
        if not login_limiter.allow(ip_key):
            raise ApiProblem(429, "Too many attempts.",
                             {"Retry-After": str(login_limiter.retry_after(ip_key))})
        body = json_body(self)
        email = body.get("email")
        password = body.get("password")
        if not valid_email(email) or not isinstance(password, str):
            store.audit("anonymous", "auth.login.failure", "invalid payload")
            raise ApiProblem(401, "Invalid credentials.")

        normalized_email = str(email).strip().lower()
        account_key = f"login:account:{normalized_email}"
        if not login_limiter.allow(account_key):
            raise ApiProblem(429, "Too many attempts for this account.",
                             {"Retry-After": str(login_limiter.retry_after(account_key))})

        user = store.get_user_by_email(email)
        # Always pay exactly one verification, even for an unknown account or a
        # non-local provider: skipping it makes the response time an oracle for
        # which email addresses exist.
        stored_hash = user["password_hash"] if user and user["provider"] == "local" else dummy_password_hash()
        password_ok, needs_pepper_upgrade = verify_password_allowing_legacy(password, stored_hash)
        if not user or not password_ok:
            store.audit(normalized_email, "auth.login.failure", remote_ip)
            raise ApiProblem(401, "Invalid credentials.")
        if needs_pepper_upgrade:
            # The stored row predates the pepper. Now that the plaintext has been
            # proven, rewrite it in the hardened form; the account migrates on
            # first login instead of needing a reset for everybody.
            store.set_user_password(
                user["id"], hash_password(password),
                must_change_password=int(user["must_change_password"] or 0),
            )
        if user["disabled"]:
            # Same public answer as a wrong password: a 403 here would confirm the
            # password was correct and hand the attacker a verified credential.
            store.audit(normalized_email, "auth.login.disabled", remote_ip)
            raise ApiProblem(401, "Invalid credentials.")
        sid = new_token()
        csrf = new_token(24)
        store.create_session(sid, user["id"],
                             now_ms() + config.SESSION_TTL_SECONDS * 1000,
                             csrf)
        store.audit(user["email"], "auth.login.success", remote_ip)
        return send_json(self, 200, public_user(user), {
            "Set-Cookie": session_cookie(sid, config.COOKIE_SECURE),
            "X-CSRF-Token": csrf,
        })

    def _auth_logout(self):
        sess, user = self._session()
        if sess:
            store.delete_session(sess["id"])
            store.audit(user["email"] if user else "anonymous",
                        "auth.logout", self._remote_key())
        return send_json(self, 200, {"ok": True}, {
            "Set-Cookie": clear_session_cookie(config.COOKIE_SECURE),
        })

    def _auth_change_password(self):
        sess, user = self._require_user(allow_must_change=True)
        self._csrf(sess)
        body = json_body(self)
        current_password = body.get("currentPassword")
        new_password = body.get("newPassword")
        if not isinstance(current_password, str) or not isinstance(new_password, str):
            raise ApiProblem(400, "Current and new passwords are required.")
        # A row written before the pepper still has to verify here: the new hash is
        # written below anyway, so accepting it upgrades rather than locks out.
        current_ok, _ = verify_password_allowing_legacy(current_password, user["password_hash"])
        if not current_ok:
            store.audit(user["email"], "auth.password.change.failure", "incorrect current password")
            raise ApiProblem(400, "Current password is incorrect.")
        valid, msg = validate_password_strength(new_password)
        if not valid:
            raise ApiProblem(400, msg)
        if new_password == current_password:
            raise ApiProblem(400, "New password must be different from current password.")
        pw_hash = hash_password(new_password)
        store.set_user_password(user["id"], pw_hash, must_change_password=0)
        # Every other token for this user dies with the password they were issued
        # against; the session running this request stays, so the operator is not
        # logged out of the window they just used to remediate.
        store.delete_user_sessions_except(user["id"], sess["id"])
        store.audit(user["email"], "auth.password.change.success", self._remote_key())
        updated = store.get_user_by_id(user["id"])
        return send_json(self, 200, {"ok": True, "user": public_user(updated)})

    # -- users (admin only) ---------------------------------------------
    def _reports_export_xlsx(self, query) -> None:
        """Tidy .xlsx for the selected metrics and window, with a native chart each.

        Values come from the same history builder the Reports page calls, so the
        file cannot disagree with the screen; only the presentation differs.
        """
        sess, user = self._require_role("operator")
        device_id = (query.get("deviceId") or query.get("device_id") or [""])[0]
        if not device_id:
            raise ApiProblem(400, "Query parameter 'deviceId' is required.")
        row = store.get_device(device_id)
        if not row:
            raise ApiProblem(404, "Device not found.")
        if not zabbix.configured:
            raise ApiProblem(503, "Zabbix history integration not configured.")

        requested = [k.strip() for k in ((query.get("metrics") or [""])[0]).split(",") if k.strip()]
        unknown = [k for k in requested if k not in REPORT_METRICS]
        if unknown:
            raise ApiProblem(400, f"Unknown metric(s): {', '.join(sorted(unknown))}.")
        keys = requested or []
        if not keys:
            raise ApiProblem(400, "At least one metric is required.")

        time_from = self._int_query(query, "from", None, minimum=0, maximum=10**17) if "from" in query else None
        time_till = self._int_query(query, "to", None, minimum=0, maximum=10**17) if "to" in query else None
        if time_from is not None and time_till is not None and time_from > time_till:
            raise ApiProblem(400, "Query parameter 'from' must be <= 'to'.")

        try:
            history = build_telemetry_history_response(
                store, zabbix, device_id,
                time_from_ms=time_from,
                time_till_ms=time_till,
                z_from=time_from // 1000 if time_from is not None else None,
                z_till=time_till // 1000 if time_till is not None else None,
                limit=1000,
            )
        except ZabbixError as exc:
            if exc.category == "HOST_NOT_FOUND":
                raise ApiProblem(404, "Device has no history in Zabbix.")
            raise ApiProblem(503, "Zabbix history integration unavailable.")

        by_ts: dict[int, dict] = {}
        for key in keys:
            _, section, _, _ = REPORT_METRICS[key]
            points = (history.get(section) or {}).get(key) or []
            for point in points:
                try:
                    by_ts.setdefault(int(point["ts"]), {})[key] = point.get("value")
                except (KeyError, TypeError, ValueError):
                    continue
        rows = [{"ts": ts, **values} for ts, values in sorted(by_ts.items())]

        window = history.get("window") or {}
        from_ms = window.get("fromMs")
        till_ms = window.get("toMs")
        window_label = "requested window"
        if from_ms is not None and till_ms is not None:
            window_label = f"{jakarta_label(from_ms)} to {jakarta_label(till_ms)} (Asia/Jakarta)"
        payload = build_report_workbook(
            device_id=device_id,
            site_label=row.get("site_name") or row.get("site_id") or device_id,
            window_label=window_label,
            generated_ms=now_ms(),
            metrics=[(key, REPORT_METRICS[key][0], REPORT_METRICS[key][2], REPORT_METRICS[key][3]) for key in keys],
            rows=rows,
        )
        stamp = jakarta_label(now_ms()).replace(":", "").replace(" ", "-")
        filename = f"telnusa-report-{device_id}-{stamp}.xlsx"
        store.audit(user["email"], "report.export.xlsx",
                    f"device_id={device_id} metrics={','.join(keys)} rows={len(rows)}")
        if not rows:
            logger.warning("xlsx export for %s has no rows in the requested window", device_id)
        return send_bytes(
            self, 200, payload,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename,
        )

    def _users_list(self):
        self._require_role("admin")
        return send_json(self, 200, {"users": [public_user(u) for u in store.list_users()]})

    def _users_create(self):
        sess, admin_user = self._require_role("admin")
        self._csrf(sess)
        body = json_body(self)
        email = body.get("email")
        name = str(body.get("name") or "").strip()
        password = body.get("password")
        role = safe_role(body.get("role")) or "viewer"
        if not valid_email(email):
            raise ApiProblem(400, "Valid email is required.")
        if not name:
            name = email.split("@")[0]
        valid, msg = validate_password_strength(password)
        if not valid:
            raise ApiProblem(400, msg)
        if store.get_user_by_email(email):
            raise ApiProblem(409, "User with this email already exists.")
        uid = store.create_user(
            email=email,
            display_name=name,
            password_hash=hash_password(password),
            role=role,
            provider="local",
            must_change_password=1,
            disabled=0,
        )
        store.audit(admin_user["email"], "auth.user.create", f"created_user_id={uid}, email={email}, role={role}")
        return send_json(self, 201, public_user(store.get_user_by_id(uid)))

    def _users_update(self, uid: str):
        sess, admin_user = self._require_role("admin")
        self._csrf(sess)
        target = store.get_user_by_id(uid)
        if not target:
            raise ApiProblem(404, "User not found.")
        body = json_body(self)
        display_name = body.get("name") if "name" in body else None
        role = body.get("role") if "role" in body else None
        disabled = body.get("disabled") if "disabled" in body else None
        if disabled is not None and not isinstance(disabled, bool):
            # Any non-empty string used to be truthy, so {"disabled": "false"}
            # disabled the account. Accept booleans only.
            raise ApiProblem(400, "Field 'disabled' must be true or false.")
        must_change = body.get("mustChangePassword") if "mustChangePassword" in body else None

        if role is not None:
            sanitized_role = safe_role(role)
            if not sanitized_role:
                raise ApiProblem(400, "Invalid role. Must be 'admin', 'operator', or 'viewer'.")
            if target["role"] == "admin" and sanitized_role != "admin":
                if store.count_active_admins() <= 1:
                    raise ApiProblem(400, "Cannot demote the last active administrator.")
            role = sanitized_role

        if disabled is not None:
            disabled_int = 1 if disabled else 0
            if target["role"] == "admin" and disabled_int == 1 and not target["disabled"]:
                if store.count_active_admins() <= 1:
                    raise ApiProblem(400, "Cannot disable the last active administrator.")
            if disabled_int == 1:
                store.delete_user_sessions(target["id"])
            disabled = disabled_int

        if must_change is not None:
            must_change = 1 if must_change else 0

        store.update_user_profile(
            uid=target["id"],
            display_name=display_name,
            role=role,
            disabled=disabled,
            must_change_password=must_change,
        )
        store.audit(admin_user["email"], "auth.user.update", f"user_id={uid}")
        return send_json(self, 200, public_user(store.get_user_by_id(uid)))

    def _users_disable(self, uid: str):
        sess, admin_user = self._require_role("admin")
        self._csrf(sess)
        target = store.get_user_by_id(uid)
        if not target:
            raise ApiProblem(404, "User not found.")
        if target["role"] == "admin" and not target["disabled"]:
            if store.count_active_admins() <= 1:
                raise ApiProblem(400, "Cannot disable the last active administrator.")
        store.update_user_profile(target["id"], disabled=1)
        store.delete_user_sessions(target["id"])
        store.audit(admin_user["email"], "auth.user.disable", f"user_id={uid}")
        return send_json(self, 200, {"ok": True})

    def _users_reset_password(self, uid: str):
        sess, admin_user = self._require_role("admin")
        self._csrf(sess)
        target = store.get_user_by_id(uid)
        if not target:
            raise ApiProblem(404, "User not found.")
        body = json_body(self)
        new_password = body.get("newPassword")
        valid, msg = validate_password_strength(new_password)
        if not valid:
            raise ApiProblem(400, msg)
        store.set_user_password(target["id"], hash_password(new_password), must_change_password=1)
        store.delete_user_sessions(target["id"])
        store.audit(admin_user["email"], "auth.user.reset_password", f"user_id={uid}")
        return send_json(self, 200, {"ok": True})

    def _users_revoke_sessions(self, uid: str):
        sess, admin_user = self._require_role("admin")
        self._csrf(sess)
        target = store.get_user_by_id(uid)
        if not target:
            raise ApiProblem(404, "User not found.")
        store.delete_user_sessions(target["id"])
        store.audit(admin_user["email"], "auth.user.revoke_sessions", f"user_id={uid}")
        return send_json(self, 200, {"ok": True})

    # -- live -----------------------------------------------------------
    def _live(self, device_id: str):
        _sess, user = self._require_user()
        user_key = f"u:{user['id']}"
        try:
            sub = broker.subscribe(device_id, user_key=user_key)
        except SubscriptionRejected as rej:
            # An explicit, bounded refusal, distinct from an upstream outage.
            raise ApiProblem(503, "Live stream capacity reached.", {
                "scope": rej.limit.scope,
                "limit": rej.limit.limit,
                "retryable": True,
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # Tell the browser how long to wait before reconnecting. Bounded so a dead
        # stream cannot become a reconnect storm.
        self.send_header("Retry-Ms", str(config.SSE_RETRY_MS))
        self.end_headers()
        self._response_started = True

        # Resume point: the last id this client actually received, else the broker's
        # current head so a fresh client does not appear to be behind.
        raw_last = self.headers.get("Last-Event-ID") or self.headers.get("last-event-id")
        try:
            resume_from = int(raw_last) if raw_last not in (None, "") else broker.last_event_id()
        except (TypeError, ValueError):
            resume_from = broker.last_event_id()

        try:
            self.wfile.write(f": connected resume={resume_from}\n\n".encode())
            self.wfile.flush()
            # Replay is impossible: subscriber buffers hold only frames published
            # after subscribe, so a client resuming from an older id is missing
            # history it will never receive. Say so immediately: a resync marker
            # tells it to refetch the authoritative snapshot instead of trusting
            # a partial timeline. Fresh clients (resume == broker head) skip this.
            if resume_from < broker.last_event_id():
                gap_marker = json.dumps(
                    {"_seq": resume_from + 1, "type": "resync", "payload": {"reason": "resume_gap"}},
                    separators=(",", ":"),
                )
                try:
                    sub.queue.put_nowait(gap_marker)
                except queue.Full:
                    pass
            while True:
                try:
                    data = sub.queue.get(timeout=25)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue

                try:
                    frame_id = json.loads(data).get("_seq", sub.last_event_id)
                except ValueError:
                    frame_id = sub.last_event_id
                try:
                    frame_id = int(frame_id)
                except (TypeError, ValueError):
                    frame_id = sub.last_event_id
                self.wfile.write(f"id: {frame_id}\n".encode())
                self.wfile.write(f"data: {data}\n\n".encode())
                sub.last_event_id = frame_id
                sub.overflowed = False
                self.wfile.flush()
        except (OSError, TimeoutError):
            # Client disconnect (or a stalled socket) is a normal end of stream,
            # not a backend failure.
            return
        finally:
            broker.unsubscribe(device_id, sub)

    # -- device/api -----------------------------------------------------
    def _device_get(self, path: str, device_id: str, query=None):
        self._require_user()
        row = store.get_device(device_id)
        if not row:
            raise ApiProblem(404, "Device not found.")
        if path.endswith("/telemetry/latest"):
            payload = Store.row_dict(row).get("last_payload")
            if not payload:
                return send_json(self, 200, {
                    "deviceId": device_id,
                    "timestamp": row["last_seen"],
                    "dataAge": "no_data",
                    "mppt": None,
                    "pzem": None,
                    "solar": None,
                    "derived": None,
                })
            return send_json(self, 200, payload)
        if path.endswith("/telemetry"):
            # Real Zabbix history only. Unconfigured -> explicit 503, never mock/fake series.
            if not zabbix.configured:
                raise ApiProblem(503, "Zabbix history integration not configured.")
            q = query or {}
            def _int_param(name: str, default: int | None) -> int | None:
                try:
                    vals = q.get(name)
                    if not vals:
                        return default
                    return int(vals[0])
                except (TypeError, ValueError):
                    raise ApiProblem(400, f"Invalid query parameter: {name}.")
            def _str_param(name: str, default: str | None) -> str | None:
                vals = q.get(name)
                return vals[0] if vals else default

            time_from = _int_param("from", None)
            if time_from is None:
                time_from = _int_param("time_from", None)
            time_till = _int_param("to", None)
            if time_till is None:
                time_till = _int_param("time_till", None)
            requested_range = _str_param("range", None)
            requested_resolution = _str_param("resolution", None)
            raw_limit = _int_param("limit", None)
            limit = max(1, min(raw_limit, 1000)) if raw_limit is not None else 1000
            # Plot density follows the caller's panel width (Grafana's rule); the
            # server picks the interval, so this is a target, not a hard count.
            max_data_points = _int_param("maxDataPoints", None)

            if time_from is not None and time_till is not None and time_from > time_till:
                raise ApiProblem(400, "Query parameter 'from' must be <= 'to'.")

            # Convert to seconds / ms
            z_from = (time_from // 1000 if time_from > 10_000_000_000 else time_from) if time_from is not None else None
            z_till = (time_till // 1000 if time_till > 10_000_000_000 else time_till) if time_till is not None else None
            tf_ms = (time_from if time_from > 10_000_000_000 else time_from * 1000) if time_from is not None else None
            tt_ms = (time_till if time_till > 10_000_000_000 else time_till * 1000) if time_till is not None else None

            try:
                history_data = build_telemetry_history_response(
                    store,
                    zabbix,
                    device_id,
                    time_from_ms=tf_ms,
                    time_till_ms=tt_ms,
                    z_from=z_from,
                    z_till=z_till,
                    requested_range=requested_range,
                    requested_resolution=requested_resolution,
                    limit=limit,
                    max_data_points=max_data_points,
                )
                return send_json(self, 200, history_data)
            except ZabbixError as exc:
                if exc.category == "HOST_NOT_FOUND":
                    metric_state = {
                        f"{group}.{name}": METRIC_NOT_INTEGRATED
                        for group, names in (("mppt", ("pvVoltage", "pvCurrent", "outputVoltage", "outputCurrent")),
                                             ("pzem", ("phaseAVoltage", "phaseBVoltage", "phaseCVoltage", "phaseACurrent", "phaseBCurrent", "phaseCCurrent", "energy")),
                                             ("solar", ("radiation",)))
                        for name in names
                    }
                    return send_json(self, 200, {
                        "deviceId": device_id,
                        "resolution": requested_resolution or "raw",
                        "mppt": {"pvVoltage": [], "pvCurrent": [], "outputVoltage": [], "outputCurrent": [], "pvInputPower": [], "outputPower": []},
                        "pzem": {"phaseAVoltage": [], "phaseBVoltage": [], "phaseCVoltage": [], "phaseACurrent": [], "phaseBCurrent": [], "phaseCCurrent": [], "energy": []},
                        "energySummary": {
                            "accumulatedKwh": None,
                            "periodDeltaKwh": 0.0,
                            "todayKwh": 0.0,
                            "quality": "MISSING_DATA",
                            "coverageRatio": 0.0,
                        },
                        "solar": {"radiation": []},
                        "metricState": metric_state,
                        "window": {
                            "fromMs": z_from * 1000 if z_from is not None else None,
                            "toMs": z_till * 1000 if z_till is not None else None,
                            "range": requested_range,
                            "limit": min(limit, 1000),
                            "truncated": limit > 1000,
                            "maxPoints": 1000,
                            "dataPoints": 0,
                            "coverageRatio": 0.0,
                            "quality": "MISSING_DATA",
                        },
                    })
                raise ApiProblem(503, "Zabbix history integration unavailable.")
        if path.endswith("/health/history"):
            if not zabbix.configured:
                return send_json(self, 200, health_history_unavailable(device_id, "not_configured"))
            q = query or {}
            def _int(name: str) -> int | None:
                vals = q.get(name)
                if not vals:
                    return None
                try:
                    return int(vals[0])
                except (TypeError, ValueError):
                    raise ApiProblem(400, f"Invalid query parameter: {name}.")
            t_from, t_till = _int("from"), _int("to")
            try:
                return send_json(self, 200, zabbix.health_history(
                    device_id,
                    time_from=(t_from // 1000 if t_from and t_from > 10_000_000_000 else t_from),
                    time_till=(t_till // 1000 if t_till and t_till > 10_000_000_000 else t_till),
                    limit=min(_int("limit") or 288, 1000),
                ))
            except ZabbixError as exc:
                if exc.category == "HOST_NOT_FOUND":
                    return send_json(self, 200, health_history_unavailable(device_id, "no_history_yet"))
                raise ApiProblem(503, "Zabbix health history unavailable.")
        if path.endswith("/health"):
            return send_json(self, 200, health_snapshot(store, device_id))
        if path.endswith("/alarms"):
            if not zabbix.configured:
                raise ApiProblem(503, "Zabbix alarm integration not configured.")
            try:
                return send_json(self, 200, zabbix.scoped_alarms(device_id, limit=100, device_lookup=store.get_device))
            except ZabbixError:
                raise ApiProblem(503, "Zabbix alarm integration unavailable.")
        if path.endswith("/events"):
            if not zabbix.configured:
                raise ApiProblem(503, "Zabbix event integration not configured.")
            try:
                return send_json(self, 200, zabbix.scoped_events(device_id, limit=100, device_lookup=store.get_device))
            except ZabbixError:
                raise ApiProblem(503, "Zabbix event integration unavailable.")
        if path.endswith("/status"):
            return send_json(self, 200, {
                "status": row["status"],
                "lastSeen": row["last_seen"],
                "clockSkew": row.get("clock_skew", False),
                "clockSkewSeconds": row.get("clock_skew_seconds"),
            })
        return send_json(self, 200, device_public(row))

    def _mqtt_ingest(self, actor: str):
        status, payload = process_payload(store, json_body(self), actor=actor)
        return send_json(self, status, payload)

    def _http_ingest(self):
        if not config.NMS_TELEMETRY_INGEST_ENABLED:
            raise ApiProblem(501, "HTTP telemetry ingestion not enabled.")

        # Authentication: shared token from config (machine-to-machine)
        expected_token = config.NMS_TELEMETRY_INGEST_TOKEN
        if not expected_token:
            # No token configured, so reject for security
            raise ApiProblem(501, "HTTP telemetry ingestion not configured (missing token).")

        provided = self.headers.get("Authorization") or self.headers.get("X-Telemetry-Token")
        if not provided:
            raise ApiProblem(401, "Authentication required.")
        if provided.startswith("Bearer "):
            provided = provided[7:]
        # Constant-time comparison
        if not hmac.compare_digest(str(provided).encode("utf-8"), str(expected_token).encode("utf-8")):
            raise ApiProblem(401, "Invalid token.")

        # Body size enforcement: json_body validates Content-Length and reads exact bytes.
        # Pass the telemetry-specific limit; generic 1 MiB limit still applies as fallback.
        max_body = config.NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES

        # Delegate to shared pipeline
        status, payload = process_payload(store, json_body(self, max_body=max_body), actor="http")
        return send_json(self, status, payload)

    def _proxy_webui(self, device_id: str, subpath: str, query: str = "") -> None:
        sess, user = self._require_role("operator")
        action = "remote.webui.proxy"

        target = resolve_device_target(device_id, store)
        if not target:
            store.audit(user["email"], action, f"device_id={device_id} result=device_not_found")
            raise ApiProblem(404, "Device not found.")

        info = tunnel_manager.ensure(device_id)
        tunnel_manager.touch(device_id)
        if info.state == TunnelState.BLOCKED_CONFIG:
            store.audit(user["email"], action, f"device_id={device_id} result=blocked_config error={info.error}")
            raise ApiProblem(501, f"BLOCKED: MISSING EXTERNAL SERVICE. {info.error or 'SSH/ZeroTier remote access not configured.'}")
        if info.state != TunnelState.READY or not info.local_port:
            store.audit(user["email"], action, f"device_id={device_id} result=failed error={info.error}")
            raise ApiProblem(503, f"Remote tunnel failure: {info.error or 'Failed to establish tunnel.'}")

        upstream_url = subpath + (f"?{query}" if query else "")
        if not upstream_url.startswith("/"):
            upstream_url = "/" + upstream_url

        # Needed before the request goes out: the bulk body must be corrected against
        # this prefix, and the RUT cannot know it -- the prefix exists only between the
        # browser and this proxy.
        proxy_prefix = f"/api/remote/{urllib.parse.quote(device_id)}/webui/proxy"

        req_body = None
        content_len = self.headers.get("Content-Length")
        if content_len:
            try:
                declared = int(content_len)
            except (TypeError, ValueError):
                declared = 0
            if declared > config.RUT_WEBUI_MAX_UPLOAD_BYTES:
                # Reading an attacker-declared length into memory is how a proxy
                # becomes an OOM: the body is capped, explicitly, before a byte moves.
                raise ApiProblem(413, f"Upload exceeds the {config.RUT_WEBUI_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB proxy limit.")
            try:
                req_body = self.rfile.read(declared) if declared > 0 else None
            except (ValueError, OSError):
                req_body = None

        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailers", "transfer-encoding",
            "upgrade", "host"
        }

        req_headers = {}
        for k, v in self.headers.items():
            k_lower = k.lower()
            if k_lower not in hop_by_hop:
                if k_lower == "cookie":
                    filtered = filter_proxy_cookies(v)
                    if filtered:
                        req_headers[k] = filtered
                else:
                    req_headers[k] = v

        req_headers["Host"] = f"127.0.0.1:{info.local_port}"
        req_headers["Accept-Encoding"] = "identity"

        # The bundle rewrite that makes browser fetch() work also bakes this prefix into
        # the endpoint values of a bulk sub-request, and the RUT dispatches those
        # server-side. Restore them before forwarding, and keep Content-Length in step
        # with the rewritten body. Only /api/bulk is affected; every other path is
        # forwarded byte-for-byte.
        if req_body and urllib.parse.urlparse(upstream_url).path == RUT_WEBUI_BULK_PATH:
            restored = restore_bulk_endpoint_paths(req_body, proxy_prefix)
            if restored is not req_body:
                req_body = restored
                req_headers["Content-Length"] = str(len(req_body))
        is_https = (target.webui_target_port == 443)
        ssl_ctx = None
        if is_https:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        def _open_conn():
            if is_https:
                return http.client.HTTPSConnection("127.0.0.1", port=info.local_port,
                                                   context=ssl_ctx, timeout=15.0)
            return http.client.HTTPConnection("127.0.0.1", port=info.local_port, timeout=15.0)

        conn = _open_conn()

        # The browser revalidates with whatever validator the previous response carried.
        # That validator was validated against the upstream body, so on a resource TELNUSA
        # rewrites a 304 would leave the browser on its cached pre-rewrite representation.
        conditional_retry = False
        sent_conditional = any(k.lower() in ("if-none-match", "if-modified-since")
                               for k in req_headers)

        # Seeded so the finally below stays valid when conn.request() itself fails.
        is_event_stream = False
        delegated_relay = False
        try:
            conn.request(self.command, upstream_url, body=req_body, headers=req_headers)
            resp = conn.getresponse()
            resp_status = resp.status
            resp_headers = resp.getheaders()
            if (resp_status == 304 and sent_conditional and not conditional_retry
                    and self.command == "GET"
                    and _is_rewritten_content_type(_header_value(resp_headers, "content-type"))):
                # Ask again without the conditional headers so the full body can be
                # fetched and rewritten, rather than confirming a representation the
                # client must not be allowed to reuse.
                conditional_retry = True
                req_headers = {k: v for k, v in req_headers.items()
                               if k.lower() not in ("if-none-match", "if-modified-since")}
                conn.close()
                conn = _open_conn()
                conn.request(self.command, upstream_url, body=req_body, headers=req_headers)
                resp = conn.getresponse()
                resp_status = resp.status
                resp_headers = resp.getheaders()
            # A live event feed never ends on its own: the RUT holds
            # /cgi-bin/subscribe.lua open indefinitely, so resp.read() would block
            # until the socket timed out and the browser would never see a response
            # head at all. Classify it from the headers already in hand and leave the
            # body unread so the relay below can hand it over incrementally.
            is_event_stream = any(
                k.lower() == "content-type" and "text/event-stream" in v.lower()
                for k, v in resp_headers)
            if not is_event_stream:
                declared_len = None
                raw_len = _header_value(resp_headers, "content-length")
                try:
                    declared_len = int(raw_len) if raw_len else None
                except (TypeError, ValueError):
                    declared_len = None
                if declared_len is not None and declared_len > config.RUT_WEBUI_MAX_REWRITE_BYTES:
                    # Too large to buffer for rewriting: relay the bytes untouched.
                    # Rewriting only applies to HTML/JS/CSS documents, which are never
                    # this big; buffering them would be an avoidable heap spike.
                    delegated_relay = True
                    logger.warning(
                        "relaying a %.1f MiB RUT body unrewritten for %s",
                        declared_len / (1024 * 1024), device_id,
                    )
                    return self._relay_event_stream(resp, resp_status, resp_headers,
                                                    hop_by_hop, _rewrite_location, _rewrite_cookie)
                resp_body = resp.read()
        except Exception as exc:
            store.audit(user["email"], action, f"device_id={device_id} result=upstream_error error={exc}")
            raise ApiProblem(503, f"Upstream proxy connection error: {exc}")
        finally:
            if not is_event_stream and not delegated_relay:
                conn.close()


        def _rewrite_location(loc_val: str) -> str:
            parsed = urllib.parse.urlparse(loc_val)
            if parsed.scheme and parsed.netloc:
                loc_p = parsed.path + (f"?{parsed.query}" if parsed.query else "") + (f"#{parsed.fragment}" if parsed.fragment else "")
                if not loc_p.startswith("/"):
                    loc_p = "/" + loc_p
                return proxy_prefix + loc_p
            elif loc_val.startswith("/"):
                return proxy_prefix + loc_val
            else:
                return proxy_prefix + "/" + loc_val

        def _rewrite_cookie(cookie_val: str) -> str:
            val = re.sub(r'(?i)\bdomain=[^;]+;?\s*', '', cookie_val)
            if re.search(r'(?i)\bpath=[^;]+', val):
                val = re.sub(r'(?i)\bpath=[^;]+', f'Path={proxy_prefix}', val)
            else:
                val = val.rstrip('; ') + f'; Path={proxy_prefix}'
            return val

        content_type = ""
        for k, v in resp_headers:
            if k.lower() == "content-type":
                content_type = v
                break

        content_type_lower = content_type.lower()
        is_html = "text/html" in content_type_lower
        # Root-absolute /api/... URLs inside RUT JS bundles bypass the <base href>
        # injected into HTML: <base> only affects document-relative resolution, not
        # runtime string literals already evaluated by an ES module. Without this
        # rewrite they escape the proxy prefix and hit TELNUSA's own API root.
        is_js = "javascript" in content_type_lower
        # The RUT stylesheet references its webfonts and icons with bare url(/fonts/...)
        # and url(/icons/...) references. The markup branch below never sees them
        # because this response is CSS, not HTML.
        is_css = "text/css" in content_type_lower


        content_encoding = ""
        for k, v in resp_headers:
            if k.lower() == "content-encoding":
                content_encoding = v.strip().lower()
                break


        if is_event_stream:
            # The relay pumps the body, so the per-request socket timeout would
            # sever a healthy but quiet feed. Bounded, so a dead upstream still
            # frees the thread; the RUT client reconnects by itself afterwards.
            if conn.sock is not None:
                conn.sock.settimeout(RUT_WEBUI_STREAM_IDLE_TIMEOUT_SECONDS)
            self._relay_event_stream(resp, resp_status, resp_headers, hop_by_hop,
                                     _rewrite_location, _rewrite_cookie)
            conn.close()
            return
        rewritten_encoding = False
        # Set only when the bytes actually handed downstream differ from the upstream
        # bytes. The upstream ETag/Last-Modified describe the upstream representation,
        # so they must not be republished for a body TELNUSA rewrote.
        transformed = False
        if (is_html or is_js or is_css) and resp_body:
            try:
                body_bytes = resp_body
                if content_encoding == "gzip":
                    body_bytes = gzip.decompress(resp_body)
                elif content_encoding == "deflate":
                    body_bytes = zlib.decompress(resp_body)
                elif content_encoding not in ("", "identity"):
                    body_bytes = b""
                if body_bytes:
                    # JS is rewritten only on a clean UTF-8 decode; a failure leaves
                    # the upstream bytes untouched rather than corrupting the bundle.
                    body_str = body_bytes.decode("utf-8", errors="strict" if is_js else "replace")
                    if is_html:
                        if "<head>" in body_str and "<base" not in body_str.lower():
                            body_str = body_str.replace("<head>", f'<head><base href="{proxy_prefix}/">', 1)
                        elif "<HEAD>" in body_str and "<base" not in body_str.lower():
                            body_str = body_str.replace("<HEAD>", f'<HEAD><base href="{proxy_prefix}/">', 1)
                        body_str = re.sub(r'(href|src|action)=([\'"])/(?!api/remote/)', rf'\1=\2{proxy_prefix}/', body_str)
                        body_str = re.sub(r'url\(([\'"]?)/(?!api/remote/)', rf'url(\1{proxy_prefix}/', body_str)
                        # The login response is an inline script of exactly this shape:
                        #   window.location = window.location.origin + '/status/overview'
                        # Rewriting it here is what keeps a successful login inside the
                        # proxy instead of jumping to the portal root.
                        body_str = RUT_WEBUI_ORIGIN_NAV_RE.sub(
                            lambda m: f"{m.group(1)}'{proxy_prefix}{m.group(3)}'", body_str
                        )
                        body_str = RUT_WEBUI_NAV_ASSIGN_RE.sub(
                            lambda m: f"{m.group(1)}'{proxy_prefix}{m.group(3)}'", body_str
                        )
                    elif is_css:
                        body_str = RUT_WEBUI_CSS_URL_RE.sub(rf'url(\1{proxy_prefix}\2', body_str)
                    else:
                        body_str = re.sub(r'(["\'`])/api/(?!remote/)', rf'\1{proxy_prefix}/api/', body_str)
                        body_str = RUT_WEBUI_JS_PATH_RE.sub(rf'\1{proxy_prefix}\2', body_str)
                        body_str = RUT_WEBUI_VITE_BASE_RE.sub(rf'return"{proxy_prefix}/"+\1}}', body_str)
                        # Navigations that would otherwise escape to the portal origin.
                        body_str = RUT_WEBUI_ORIGIN_NAV_RE.sub(
                            lambda m: f"{m.group(1)}'{proxy_prefix}{m.group(3)}'",
                            body_str,
                        )
                        body_str = RUT_WEBUI_NAV_ASSIGN_RE.sub(
                            lambda m: f"{m.group(1)}'{proxy_prefix}{m.group(3)}'",
                            body_str,
                        )
                    resp_body = body_str.encode("utf-8")
                    rewritten_encoding = content_encoding in ("gzip", "deflate")
                    transformed = True
            except Exception as exc:
                # Fail open (serve the unrewritten body) but never silently: an
                # unrewritten WebUI loads assets from the wrong origin and looks
                # like a broken router rather than a rewrite failure.
                logger.warning("RUT WebUI body rewrite failed: %r", exc)

        self.send_response(resp_status)
        for k, v in resp_headers:
            k_lower = k.lower()
            if k_lower in hop_by_hop or k_lower == "content-length" or k_lower in DEVICE_POLICY_HEADERS:
                continue
            if transformed and k_lower in ("etag", "last-modified"):
                continue
            if rewritten_encoding and k_lower == "content-encoding":
                continue
            if k_lower == "location":
                self.send_header(k, _rewrite_location(v))
            elif k_lower == "set-cookie":
                self.send_header(k, _rewrite_cookie(v))
            else:
                self.send_header(k, v)

        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self._response_started = True
        if self.command != "HEAD":
            self.wfile.write(resp_body)

    def _relay_event_stream(self, resp, status, headers, hop_by_hop,
                            rewrite_location, rewrite_cookie):
        """Relay a long-lived upstream event stream to the client.

        The body is forwarded untouched, so Content-Encoding is passed through as-is
        instead of being recomputed the way the rewritten branch does. The length is
        unknown up front, so no Content-Length is sent: the client reads until the
        response ends, which is the point of a stream.
        """
        self.close_connection = True
        self.send_response(status)
        for k, v in headers:
            k_lower = k.lower()
            if k_lower in hop_by_hop or k_lower == "content-length" or k_lower in DEVICE_POLICY_HEADERS:
                continue
            if k_lower == "location":
                self.send_header(k, rewrite_location(v))
            elif k_lower == "set-cookie":
                self.send_header(k, rewrite_cookie(v))
            else:
                self.send_header(k, v)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self._response_started = True
        if self.command == "HEAD":
            return
        try:
            while True:
                # read1, not read: read(n) blocks until n bytes have accumulated, which
                # would hold every small event back until 8 KiB had piled up.
                chunk = resp.read1(RUT_WEBUI_STREAM_CHUNK_BYTES)
                if not chunk:
                    return
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            # Client hung up, or the upstream went quiet past the idle timeout. The
            # head is already on the wire, so an error response is no longer possible:
            # ending the stream is the only correct move, and re-raising would make
            # the handler append a second response to a response already in flight.
            return


def parse_remote_proxy_path(path: str) -> tuple[str, str] | None:
    """Parse /api/remote/:deviceId/webui/proxy(/.*)? returning (device_id, subpath) or None."""
    if not path.startswith("/api/remote/"):
        return None
    parts = path.split("/")
    if len(parts) >= 6 and parts[1] == "api" and parts[2] == "remote" and parts[4] == "webui" and parts[5] == "proxy":
        device_id = urllib.parse.unquote(parts[3])
        prefix_len = len(f"/api/remote/{parts[3]}/webui/proxy")
        subpath = path[prefix_len:]
        if not subpath:
            subpath = "/"
        return device_id, subpath
    return None

def route_device(path: str) -> str | None:
    if not path.startswith("/api/devices/"):
        return None
    parts = path.split("/")
    return urllib.parse.unquote(parts[3]) if len(parts) >= 4 else None


def route_user_action(path: str) -> tuple[str | None, str | None]:
    if not path.startswith("/api/users/"):
        return None, None
    parts = path.split("/")
    if len(parts) == 4:
        return urllib.parse.unquote(parts[3]), None
    if len(parts) == 5:
        return urllib.parse.unquote(parts[3]), parts[4]
    return None, None
def route_alarm_action(path: str) -> tuple[int | None, str | None]:
    if not path.startswith("/api/alarms/"):
        return None, None
    parts = path.split("/")
    if len(parts) == 4:
        try:
            return int(parts[3]), None
        except ValueError:
            return None, None
    if len(parts) == 5:
        try:
            return int(parts[3]), parts[4]
        except ValueError:
            return None, None
    return None, None


def alarm_public(row: dict) -> dict:
    state = row.get("state", "OPEN")
    status = "active" if state in ("OPEN", "ACKNOWLEDGED") else "resolved"
    res = {
        "id": str(row["id"]),
        "deviceId": row["device_id"],
        "siteId": row["site_id"],
        "siteName": row.get("site_name"),
        "problem": row["title"],
        "severity": row["severity"].lower(),
        "status": status,
        "state": state,
        "startTime": row["first_occurrence"],
        "lastTime": row["last_occurrence"],
        "endTime": row.get("resolved_at"),
        "observedValue": row.get("observed_value"),
        "evidence": row.get("evidence"),
        "consecutiveOccurrences": row.get("consecutive_occurrences", 1),
        "acknowledgedAt": row.get("acknowledged_at"),
        "acknowledgedBy": row.get("acknowledged_by"),
        "ackNote": row.get("ack_note"),
        "resolvedAt": row.get("resolved_at"),
        "resolvedBy": row.get("resolved_by"),
        "resolutionReason": row.get("resolution_reason"),
        "ruleId": row.get("rule_id"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
    if "transitions" in row:
        res["transitions"] = row["transitions"]
    return res


def device_network_label(row) -> str | None:
    """Mobile network label for the device row. Read from the router's own modem
    status (connection type, else registration state); never defaulted to a
    guess like "Cellular"."""
    payload = row.get("last_payload")
    health = payload.get("health") if isinstance(payload, dict) else None
    if not isinstance(health, dict):
        return row.get("network") or None
    for value in (health.get("connectionType"), health.get("networkState")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return row.get("network") or None


def device_public(row) -> dict:
    payload = row.get("last_payload")
    if isinstance(payload, str):
        try:
            import json as _json
            payload = _json.loads(payload)
        except ValueError:
            payload = None
    mppt_state = row.get("mppt_state", "no_data")
    pzem_state = row.get("pzem_state", "no_data")
    sensor3_state = row.get("sensor3_state", "no_data") or "no_data"
    if sensor3_state == "not_integrated":
        sensor3_state = "no_data"
    states = (mppt_state, pzem_state, sensor3_state)
    if any(s == "sensor_fault" for s in states):
        acquisition_state = "sensor_fault"
    elif all(s == "all_ok" for s in states):
        acquisition_state = "all_ok"
    elif all(s == "no_data" for s in states):
        acquisition_state = "no_data"
    else:
        acquisition_state = "partial"

    return {
        "id": row["device_id"],
        "name": row["device_id"],
        "model": "Teltonika RUT906",
        "serial": row["serial"],
        "siteId": row["site_id"],
        "siteName": row["site_name"],
        "status": row["status"],
        "lastSeen": row["last_seen"],
        "network": device_network_label(row),
        "health": row["health"],
        "connectivity": row["connectivity"],
        "dataAge": row["data_age"],
        "mpptState": mppt_state,
        "pzemState": pzem_state,
        "sensor3State": sensor3_state,
        "acquisitionState": acquisition_state,
        "clockSkew": row.get("clock_skew", False),
        "clockSkewSeconds": row.get("clock_skew_seconds"),
        "lastPayload": payload if isinstance(payload, dict) else None,
    }

# the router-system resource block (CPU/RAM/flash/temperature)
# still has no collected source, so it stays explicitly `not_integrated` with
# null readings: "no source" must not be rendered as "source exists but reported
# nothing". Everything the NMS actually knows is exposed instead as named
# dimensions (`dimensions`) so transport, sensors, freshness and integrations are
# never collapsed into one misleading number.
HEALTH_NOT_INTEGRATED = "not_integrated"
HEALTH_AVAILABLE = "available"

def health_from_payload(dev: dict) -> dict:
    """Router health as reported by the device itself, never derived from
    nothing: every value below was measured on the router. `cpuPercent` is the
    one derivation, and it is labelled as load-based."""
    payload = dev.get("last_payload")
    raw_health = payload.get("health") if isinstance(payload, dict) else None
    health = {k: v for k, v in raw_health.items() if v is not None} if isinstance(raw_health, dict) else {}
    if not health:
        return {"state": HEALTH_NOT_INTEGRATED, "health": {}}
    load1 = health.get("load1")
    cores = health.get("cores")
    cpu_percent = None
    if isinstance(load1, (int, float)) and isinstance(cores, (int, float)) and cores > 0:
        cpu_percent = round(min(100.0, float(load1) / float(cores) * 100.0), 1)
    return {"state": HEALTH_AVAILABLE, "health": health, "cpuPercent": cpu_percent}

def health_snapshot(store, device_id: str) -> dict:
    dev = store.get_device(device_id) or {}
    rows = {r["component"]: Store.row_dict(r) for r in store.list_integration_state()}
    mqtt_row = rows.get("mqtt") or {}
    http_row = rows.get("http") or {}
    zabbix_row = rows.get("zabbix") or {}
    now = now_ms()
    last_seen = dev.get("last_seen")
    age_seconds = None if last_seen is None else max(0, (now - int(last_seen)) // 1000)
    target = resolve_device_target(device_id, store)
    tunnel = tunnel_manager.status(device_id).to_dict() if target else None
    data_age = dev.get("data_age", "no_data")
    reachability = {
        "fresh": "reachable",
        "stale": "stale",
        "offline": "unreachable",
    }.get(data_age, "unknown")
    router_health = health_from_payload(dev)
    health = router_health["health"]
    return {
        # "available" once the router reported its own resource metrics; the
        # fields below stay null only while no reading has arrived.
        "state": router_health["state"],
        "deviceId": device_id,
        "cpuPercent": router_health.get("cpuPercent"),
        "ramPercent": health.get("ramPercent"),
        "flashPercent": health.get("flashPercent"),
        "temperatureC": health.get("temperatureC"),
        "uptimeSeconds": health.get("uptimeSeconds"),
        "rssi": health.get("rssi"),
        "networkState": health.get("networkState"),
        "mqttConnected": (mqtt_row.get("state") == "CONNECTED") if mqtt_row else None,
        "lastTelemetryAge": age_seconds,
        "dimensions": {
            # 1. Can the NMS manage the device (telemetry/administration path)?
            "managementReachability": {"state": reachability, "dataAge": data_age, "lastSeen": last_seen},
            # 2. Sensor acquisition, per sensor, never merged with connectivity.
            "sensorAcquisition": {
                "state": dev.get("acquisition_state", "no_data"),
                "mppt": dev.get("mppt_state", "no_data"),
                "pzem": dev.get("pzem_state", "no_data"),
                "sensor3": dev.get("sensor3_state", "no_data"),
            },
            # 3. Primary telemetry transport (router -> HTTP ingest).
            "httpTransport": {
                "state": http_row.get("state", "NOT CONFIGURED"),
                "configured": bool(http_row.get("configured", 0)),
                "reachable": http_row.get("reachable"),
                "lastSuccess": http_row.get("last_success"),
                "lastError": http_row.get("last_error"),
            },
            # 4. Secondary transport. Its own state, independent of HTTP.
            "mqttTransport": {
                "state": mqtt_row.get("state", "NOT CONFIGURED"),
                "configured": bool(mqtt_row.get("configured", 0)),
                "lastError": mqtt_row.get("last_error"),
            },
            # 5. NMS ingest acceptance (queue + worker), distinct from transport.
            "ingest": {
                "latestReceivedAt": dev.get("latest_received_at"),
                "latestProcessedAt": dev.get("latest_processed_at"),
                "integrationStatus": dev.get("integration_status", "no_data"),
            },
            # 6. Freshness as its own dimension.
            "freshness": {"dataAge": data_age, "ageSeconds": age_seconds},
            # 7. History/problem integration.
            "zabbix": {
                "state": zabbix_row.get("state", "NOT CONFIGURED"),
                "configured": bool(zabbix_row.get("configured", 0)),
                "hostId": dev.get("zabbix_hostid"),
                "lastSuccess": zabbix_row.get("last_success"),
            },
            # 10. Router resource health, measured on the device itself.
            "routerHealth": {
                "state": router_health["state"],
                "load1": health.get("load1"),
                "load5": health.get("load5"),
                "load15": health.get("load15"),
                "cores": health.get("cores"),
                "rsrpDbm": health.get("rsrp"),
                "operator": health.get("operator"),
                "connectionType": health.get("connectionType"),
            },
            # 8. Native WebUI path (trusted server-side mapping + tunnel state).
            "webui": {
                "configured": bool(target),
                "tunnelState": (tunnel or {}).get("state"),
            },
            # 9. Terminal path (same trusted mapping; Guacamole is the gateway).
            "terminal": {
                "configured": bool(target) and guacamole_configured(),
                "guacamole": guacamole_configured(),
            },
        },
    }


def health_history_unavailable(device_id: str, reason: str) -> dict:
    """Empty series with an explicit reason: the source state is named instead of
    being inferred from empty arrays (reason: not_configured | no_history_yet)."""
    return {
        "state": "not_integrated" if reason == "not_configured" else "no_data",
        "deviceId": device_id,
        "reason": reason,
        "cpu": [],
        "ram": [],
        "temperature": [],
        "rssi": [],
    }



def main() -> None:
    bootstrap_admin()
    # Configuration that weakens security silently is worse than a missing feature:
    # name it once at startup, in the journal, instead of leaving it to be inferred.
    if not config.AUTH_PEPPER:
        logger.warning(
            "NMS_AUTH_PEPPER is empty: password hashes are unpeppered. Setting it is now safe - "
            "existing hashes keep verifying and are rewritten peppered on next login."
        )
    if config.TRUST_PROXY and config.HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "NMS_TRUST_PROXY=1 while NMS_HOST=%s (not loopback): X-Forwarded-For can be "
            "forged by anyone who can reach the port directly.", config.HOST
        )
    if not config.TRUST_PROXY and config.HOST in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "NMS_TRUST_PROXY=0 behind a loopback reverse proxy: every client shares one "
            "rate-limit bucket and the audit trail records 127.0.0.1."
        )
    store.purge_expired_sessions()
    for component, state in startup_state().items():
        store.set_integration_state(component.lower(), state, configured=state == "CONFIGURED")
    if zabbix.configured:
        health = zabbix.health_check()
        store.set_integration_state(
            "zabbix", health.state, configured=True,
            reachable=health.state in ("AUTHENTICATED", "INCOMPATIBLE"),
            authenticated=health.state == "AUTHENTICATED",
            last_success=now_ms() if health.state == "AUTHENTICATED" else None,
            last_error=json.dumps(health.error, separators=(",", ":")) if health.error else None,
            metrics={"api_version": health.version, "api_latency_ms": health.latency_ms},
        )
    collector = start_collector(store)
    zabbix_worker = ZabbixDeliveryWorker(store)
    zabbix_worker.start()
    aging_worker = StatusAgingWorker(store)
    aging_worker.start()
    tunnel_worker = TunnelMaintenanceWorker(tunnel_manager, interval=10.0)
    tunnel_worker.start()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"TELNUSA NMS backend listening on {config.HOST}:{config.PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        collector.stop()
        zabbix_worker.stop()
        aging_worker.stop()
        tunnel_worker.stop()
        tunnel_manager.cleanup_all()

if __name__ == "__main__":
    main()
