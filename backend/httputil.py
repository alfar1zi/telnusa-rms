"""HTTP plumbing. stdlib only (http.server + urllib).

json_body(): parses JSON objects with a size cap.
send_json(): JSON response with hardened defaults:
  no-store, nosniff, deny framing, strict referrer. Caller adds the
  cookie header; this module never logs secrets.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from .config import config

MAX_BODY = 1 << 20  # 1 MiB


class ApiProblem(Exception):
    def __init__(self, status: int, message: str,
                 headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


def json_body(handler: BaseHTTPRequestHandler, max_body: int | None = None) -> dict:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        raise ApiProblem(400, "Invalid Content-Length.")
    if length <= 0:
        return {}
    limit = max_body if max_body is not None else MAX_BODY
    if length > limit:
        raise ApiProblem(413, "Request body too large.")
    raw = handler.rfile.read(length)
    ctype = (handler.headers.get("Content-Type") or "").split(";")[0]
    if ctype and ctype != "application/json":
        raise ApiProblem(415, "Content-Type must be application/json.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiProblem(400, "Malformed JSON body.")
    if not isinstance(data, dict):
        raise ApiProblem(400, "JSON body must be an object.")
    return data


def send_json(handler: BaseHTTPRequestHandler, status: int,
              payload: dict | list,
              extra_headers: dict | None = None) -> None:
    body = json.dumps(payload).encode()
    if getattr(handler, "_response_started", False):
        # Something is already on the wire (a stream or an earlier response):
        # a second response would corrupt it. Raised errors after streaming
        # begins are dropped here on purpose.
        return
    # The whole send path is guarded, not just the body write: the header flush
    # (end_headers -> wfile.write) raises the same way on a dead socket, and a
    # raise there reached the caller's error arm, which then tried a second
    # response on the same socket (production tracebacks, 2026-10-21).
    handler._response_started = True
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if extra_headers:
            for k, v in extra_headers.items():
                handler.send_header(k, v)
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
        # The client is gone: cancelled fetch, navigation away, proxy timeout.
        # Nobody is left to tell, so never raise and never retry on this socket.
        handler.close_connection = True


def send_bytes(handler: BaseHTTPRequestHandler, status: int,
               body: bytes, content_type: str, filename: str) -> None:
    """Binary download with the same dead-client and double-response guards."""
    if getattr(handler, "_response_started", False):
        return
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-") or "download"
    handler._response_started = True
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
        handler.close_connection = True


def client_ip(handler: BaseHTTPRequestHandler, trust_proxy: bool, hops: int = 1) -> str:
    """Determine client IP safely.
    If trust_proxy is True and X-Forwarded-For is present, use the entry `hops`
    places from the right -- every proxy appends the address it saw, so the
    rightmost entries are the ones a client cannot forge. `hops` is how many
    trusted proxies sit in front (Tailscale Funnel -> Apache = 2; default 1).
    Otherwise use the socket peer address.
    """
    if trust_proxy:
        raw = handler.headers.get("X-Forwarded-For") or ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            index = max(0, len(parts) - max(1, int(hops)))
            return parts[index]
    return handler.client_address[0]


def parse_cookies(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    raw = handler.headers.get("Cookie") or ""
    out = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def session_cookie(value: str, secure: bool, max_age: int | None = None) -> str:
    ttl = max_age if max_age is not None else config.SESSION_TTL_SECONDS
    parts = [
        f"nms_session={value}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={ttl}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie(secure: bool) -> str:
    parts = [
        "nms_session=deleted",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
