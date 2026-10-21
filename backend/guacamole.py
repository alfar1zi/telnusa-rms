"""Guacamole terminal access via signed-JSON authentication (guacamole-auth-json).

The portal signs a short-lived assertion that carries the SSH connection
parameters taken from the trusted device registry. Guacamole therefore never
holds a stored credential for the RUT906, and the private key travels
portal -> Guacamole only: the browser receives nothing but the opaque
Guacamole session token produced by that exchange.

Protocol (per guacamole-auth-json/doc/README.md):
    data = base64( AES-128-CBC( HMAC-SHA256(key, json) || json, iv=0 ) )
    POST /api/tokens  with form field ``data``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import config
from .remote import DeviceRemoteTarget

# Guacamole 128-bit AES-CBC with an all-zero IV, per the extension's reference
# implementation (openssl enc -aes-128-cbc -iv 0 -nosalt -a).
_NULL_IV = b"\x00" * 16
_BLOCK = 16

# The signed assertion is only used for the immediate server-side exchange, so
# it gets a deliberately short life.
_ASSERTION_TTL_MS = 5 * 60 * 1000

_JSON_AUTH_NOT_CONFIGURED = "REMOTE_NOT_CONFIGURED"
_JSON_AUTH_UNREACHABLE = "REMOTE_TERMINAL_UNAVAILABLE"


@dataclass(frozen=True)
class TerminalGrant:
    """What the browser needs to attach a Guacamole terminal. Never a secret key."""

    status: str
    base_url: str = ""
    token: str = ""
    connection_id: str = ""
    error: str = ""
    detail: str = ""


def configured() -> bool:
    return bool(config.GUACAMOLE_URL and config.GUACAMOLE_JSON_SECRET_KEY)


def _pad(data: bytes) -> bytes:
    """PKCS#7, matching openssl's default padding for ``enc -aes-128-cbc``."""
    pad = _BLOCK - (len(data) % _BLOCK)
    return data + bytes([pad]) * pad


def _sign_and_encrypt(payload: dict, secret_hex: str) -> str:
    key = bytes.fromhex(secret_hex.strip())
    if len(key) != 16:
        raise ValueError("GUACAMOLE_JSON_SECRET_KEY must be 32 hex digits (128-bit)")

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key, body, hashlib.sha256).digest()

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.CBC(_NULL_IV)).encryptor()
    encrypted = encryptor.update(_pad(signature + body)) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def _ssh_parameters(target: DeviceRemoteTarget) -> dict[str, str]:
    """Build the connection from server-side config only.

    hostname/port/user come from the trusted registry via ``DeviceRemoteTarget``
    and ``config``; the private key is read from the server-side key file.
    Never from a request payload.
    """
    key_path = config.RUT_SSH_KEY_PATH
    if not key_path:
        raise ValueError("RUT SSH key path is not configured")
    with open(key_path, "r", encoding="utf-8") as fh:
        private_key = fh.read()

    params = {
        "hostname": target.ssh_host,
        "port": str(target.ssh_port),
        "username": config.RUT_SSH_USER,
        "private-key": private_key,
        "security": "any",
        "font-name": "monospace",
        "font-size": "12",
        "color-scheme": "gray-black",
    }
    return params


def _post_token(base_url: str, data_b64: str, timeout: float) -> str:
    body = urllib.parse.urlencode({"data": data_b64}).encode("ascii")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/tokens",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = payload.get("authToken") or payload.get("token")
    if not token:
        raise ValueError("Guacamole returned no auth token")
    return str(token)


_CONNECTION_NAME = "RUT906 SSH"
_DATA_SOURCE = "json"
_PUBLIC_BASE = "/guacamole"  # must match the Apache proxy path in the vhost


def _connection_id() -> str:
    """Guacamole's identifier for an inline data-source connection.

    The client route is ``/#/client/<id>`` where the id is
    ``base64("<name>\0c\0<datasource>")`` -- the exact form Guacamole itself
    puts in the redirect after it authenticates an inline JSON connection.
    """
    raw = f"{_CONNECTION_NAME}\0c\0{_DATA_SOURCE}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def client_url(grant: TerminalGrant) -> str:
    """Browser-facing URL for the Guacamole client.

    Same-origin path: only the opaque Guacamole session token crosses to the
    browser, so the RUT906 private key stays server-side inside the assertion.
    """
    if grant.status != "ready":
        return ""
    return f"{_PUBLIC_BASE}/#/client/{grant.connection_id}?token={grant.token}"


def issue_terminal_grant(
    user_email: str,
    target: DeviceRemoteTarget,
    timeout: float = 10.0,
) -> TerminalGrant:
    """Exchange a signed assertion for an opaque Guacamole token.

    Returns a ``TerminalGrant``; on failure the grant carries an ``error`` code
    the caller maps to an HTTP status. No exception escapes except a missing
    key file, which the caller surfaces as a configuration problem.
    """
    if not configured():
        return TerminalGrant(
            status="error",
            error=_JSON_AUTH_NOT_CONFIGURED,
            detail=(
                "BLOCKED: MISSING CONFIGURATION. Guacamole JSON auth is not "
                "configured (GUACAMOLE_URL / GUACAMOLE_JSON_SECRET_KEY)."
            ),
        )

    payload = {
        "username": user_email,
        "expires": int(time.time() * 1000) + _ASSERTION_TTL_MS,
        "connections": {
            _CONNECTION_NAME: {
                "protocol": "ssh",
                "parameters": _ssh_parameters(target),
            }
        },
    }

    try:
        data_b64 = _sign_and_encrypt(payload, config.GUACAMOLE_JSON_SECRET_KEY)
        token = _post_token(config.GUACAMOLE_URL, data_b64, timeout)
        connection_id = _connection_id()
    except urllib.error.HTTPError as exc:
        return TerminalGrant(
            status="error",
            error=_JSON_AUTH_UNREACHABLE,
            detail=f"BLOCKED: EXTERNAL DEPENDENCY. Guacamole rejected the assertion (HTTP {exc.code}).",
        )
    except urllib.error.URLError as exc:
        return TerminalGrant(
            status="error",
            error=_JSON_AUTH_UNREACHABLE,
            detail=f"BLOCKED: EXTERNAL DEPENDENCY. Guacamole unreachable ({exc.reason}).",
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return TerminalGrant(
            status="error",
            error=_JSON_AUTH_NOT_CONFIGURED,
            detail=f"BLOCKED: MISSING CONFIGURATION. {exc}",
        )

    return TerminalGrant(
        status="ready",
        base_url=config.GUACAMOLE_URL,
        token=token,
        connection_id=connection_id,
    )