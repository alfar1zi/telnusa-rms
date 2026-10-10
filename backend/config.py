"""Runtime configuration from environment only. No secrets in source."""
from __future__ import annotations

import os


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() not in ("0", "false", "no", "off")

class Config:
    HOST: str = _get("NMS_HOST", "127.0.0.1")
    PORT: int = _get_int("NMS_PORT", 8080)
    DB_PATH: str = _get("NMS_DB_PATH", "nms.db")
    COOKIE_SECURE: bool = _get_bool("NMS_COOKIE_SECURE", False)
    SESSION_TTL_SECONDS: int = _get_int("NMS_SESSION_TTL", 12 * 3600)
    TRUST_PROXY: bool = _get_bool("NMS_TRUST_PROXY", False)
    DEBUG_ERRORS: bool = _get_bool("NMS_DEBUG_ERRORS", False)
    BOOTSTRAP_ADMIN_EMAIL: str = _get("NMS_BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    BOOTSTRAP_ADMIN_PASSWORD: str = _get("NMS_BOOTSTRAP_ADMIN_PASSWORD", "")

    LOGIN_RATE_LIMIT: str = _get("NMS_LOGIN_RATE_LIMIT", "10/60")
    AUTH_PEPPER: str = _get("NMS_AUTH_PEPPER", "")
    ZABBIX_URL: str = _get("ZABBIX_URL")
    ZABBIX_TOKEN: str = _get("ZABBIX_TOKEN")
    ZABBIX_VERIFY_TLS: bool = _get_bool("ZABBIX_VERIFY_TLS", True)
    ZABBIX_TIMEOUT: int = _get_int("ZABBIX_TIMEOUT", 15)
    ZABBIX_TEMPLATE: str = _get("ZABBIX_TEMPLATE", "")
    ZABBIX_HOST_GROUP: str = _get("ZABBIX_HOST_GROUP", "")
    ZABBIX_AUTO_CREATE_ITEMS: bool = _get_bool("ZABBIX_AUTO_CREATE_ITEMS", False)
    ZABBIX_SENDER_HOST: str = _get("ZABBIX_SENDER_HOST")
    ZABBIX_SENDER_PORT: int = _get_int("ZABBIX_SENDER_PORT", 10051)
    ZABBIX_SENDER_TLS: bool = _get_bool("ZABBIX_SENDER_TLS", False)
    ZABBIX_SENDER_VERIFY_TLS: bool = _get_bool("ZABBIX_SENDER_VERIFY_TLS", True)
    ZABBIX_CA_FILE: str = _get("ZABBIX_CA_FILE", "")
    ZABBIX_QUEUE_MAX: int = _get_int("ZABBIX_QUEUE_MAX", 1000)
    # Raw samples pulled per item when building local hourly/daily rollups. Only
    # the recent part of a window needs raw precision (older buckets are filled
    # from Zabbix trends), so this stays small: 2000 points is about 11 hours at
    # the device's 20 s cadence and keeps the build off the critical path.
    ZABBIX_ROLLUP_RAW_LIMIT: int = _get_int("ZABBIX_ROLLUP_RAW_LIMIT", 2000)
    # Device telemetry cadence (RutOS Data Sender period). Used to size a raw
    # history request for the window the operator actually asked for.
    SAMPLE_INTERVAL_MS: int = _get_int("TELEMETRY_SAMPLE_INTERVAL_MS", 20_000)
    ZABBIX_RETRY_MAX: int = _get_int("ZABBIX_RETRY_MAX", 5)
    ZABBIX_RETRY_BASE_SECONDS: int = _get_int("ZABBIX_RETRY_BASE_SECONDS", 5)
    ZABBIX_DLQ_RETENTION_DAYS: int = _get_int("ZABBIX_DLQ_RETENTION_DAYS", 90)
    MQTT_ENABLED: bool = _get_bool("MQTT_ENABLED", False)
    MQTT_HOST: str = _get("MQTT_HOST", "")
    MQTT_PORT: int = _get_int("MQTT_PORT", 8883)
    MQTT_USERNAME: str = _get("MQTT_USERNAME", "")
    MQTT_PASSWORD: str = _get("MQTT_PASSWORD", "")
    MQTT_TLS: bool = _get_bool("MQTT_TLS", True)
    MQTT_TLS_INSECURE_DEV: bool = _get_bool("MQTT_TLS_INSECURE_DEV", False)
    MQTT_CA_FILE: str = _get("MQTT_CA_FILE", "")
    MQTT_CLIENT_ID: str = _get("MQTT_CLIENT_ID", "")
    MQTT_TOPIC: str = _get("MQTT_TOPIC", "entermqtt/mitratel/+/telemetry")
    MQTT_RECONNECT_MIN_SECONDS: int = _get_int("MQTT_RECONNECT_MIN_SECONDS", 1)
    MQTT_RECONNECT_MAX_SECONDS: int = _get_int("MQTT_RECONNECT_MAX_SECONDS", 60)

    FRESHNESS_LIVE_SECONDS: int = _get_int("FRESHNESS_LIVE_SECONDS", 300)
    FRESHNESS_STALE_SECONDS: int = _get_int("FRESHNESS_STALE_SECONDS", 900)
    CLOCK_SKEW_THRESHOLD_SECONDS: int = _get_int("CLOCK_SKEW_THRESHOLD_SECONDS", 900)
    FUTURE_TOLERANCE_SECONDS: int = _get_int("FUTURE_TOLERANCE_SECONDS", 300)
    PROCESSED_MESSAGES_RETENTION_SECONDS: int = _get_int("PROCESSED_MESSAGES_RETENTION_SECONDS", 7 * 86400)
    # How long the control-plane audit trail is kept. 180 days is a policy default,
    # overridable; the table had no delete path at all before this.
    AUDIT_RETENTION_DAYS: int = _get_int("NMS_AUDIT_RETENTION_DAYS", 180)
    EMAIL_PROVIDER: str = _get("EMAIL_PROVIDER", "disabled").lower()
    GMAIL_SENDER_EMAIL: str = _get("GMAIL_SENDER_EMAIL")

    RUT_SSH_HOST: str = _get("RUT_SSH_HOST", "192.168.1.144")
    RUT_SSH_PORT: int = _get_int("RUT_SSH_PORT", 22)
    # REMOTE-03: no default identity. Remote access must name an explicit, non-root,
    # authorized account; an unset value fails closed rather than assuming root.
    RUT_SSH_USER: str = _get("NMS_RUT_SSH_USER", _get("RUT_SSH_USER", "")).strip()
    RUT_SSH_KEY_PATH: str = _get("NMS_RUT_SSH_KEY_PATH", _get("RUT_SSH_KEY_PATH", ""))
    RUT_KNOWN_HOSTS_PATH: str = _get("NMS_RUT_KNOWN_HOSTS_PATH", _get("RUT_KNOWN_HOSTS_PATH", ""))
    RUT_WEBUI_TARGET_HOST: str = _get("RUT_WEBUI_TARGET_HOST", "192.168.10.1")
    RUT_WEBUI_TARGET_PORT: int = _get_int("RUT_WEBUI_TARGET_PORT", 443)
    RUT_TUNNEL_IDLE_TIMEOUT_SECONDS: int = _get_int("RUT_TUNNEL_IDLE_TIMEOUT_SECONDS", 300)
    RUT_TUNNEL_MAX_FAILURES: int = _get_int("RUT_TUNNEL_MAX_FAILURES", 5)
    RUT_TUNNEL_STARTUP_WAIT_SECONDS: float = _get_float("RUT_TUNNEL_STARTUP_WAIT_SECONDS", 3.0)
    # REMOTE-02: explicit bounds for per-device tunnel resources.
    RUT_TUNNEL_MAX_CONCURRENT: int = _get_int("RUT_TUNNEL_MAX_CONCURRENT", 32)
    RUT_TUNNEL_PORT_RANGE_START: int = _get_int("RUT_TUNNEL_PORT_RANGE_START", 18000)
    RUT_TUNNEL_PORT_RANGE_END: int = _get_int("RUT_TUNNEL_PORT_RANGE_END", 19000)
    RUT_TUNNEL_LOCK_CACHE_MAX: int = _get_int("RUT_TUNNEL_LOCK_CACHE_MAX", 1024)
    # Bounds for the RUT WebUI proxy: an upload bigger than this is refused (413)
    # before a byte is read, and a response bigger than this is relayed untouched
    # instead of being buffered for rewriting. Firmware uploads fit; a hostile or
    # runaway body cannot exhaust the heap.
    RUT_WEBUI_MAX_UPLOAD_BYTES: int = _get_int("RUT_WEBUI_MAX_UPLOAD_BYTES", 64 * 1024 * 1024)
    RUT_WEBUI_MAX_REWRITE_BYTES: int = _get_int("RUT_WEBUI_MAX_REWRITE_BYTES", 8 * 1024 * 1024)
    # How many trusted reverse proxies sit in front of this service (Tailscale Funnel
    # -> Apache = 2). The client address is taken that many entries from the right of
    # X-Forwarded-For, so a forged leftmost entry cannot choose its own identity.
    TRUSTED_PROXY_HOPS: int = _get_int("NMS_TRUSTED_PROXY_HOPS", 1)
    # SSE-01: explicit bounds for the live stream.
    SSE_RETRY_MS: int = _get_int("SSE_RETRY_MS", 3000)
    SSE_QUEUE_MAX: int = _get_int("SSE_QUEUE_MAX", 100)
    SSE_MAX_TOTAL_CONNECTIONS: int = _get_int("SSE_MAX_TOTAL_CONNECTIONS", 200)
    SSE_MAX_CONNECTIONS_PER_DEVICE: int = _get_int("SSE_MAX_CONNECTIONS_PER_DEVICE", 50)
    SSE_MAX_CONNECTIONS_PER_USER: int = _get_int("SSE_MAX_CONNECTIONS_PER_USER", 10)

    # Guacamole terminal (signed-JSON authentication, guacamole-auth-json)
    GUACAMOLE_URL: str = _get("GUACAMOLE_URL", "")
    GUACAMOLE_JSON_SECRET_KEY: str = _get("GUACAMOLE_JSON_SECRET_KEY", "")

    # HTTP Telemetry Ingestion
    NMS_TELEMETRY_INGEST_ENABLED: bool = _get_bool("NMS_TELEMETRY_INGEST_ENABLED", False)
    NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES: int = _get_int("NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES", 64 * 1024)
    # HTTP Telemetry Ingestion auth (machine-to-machine shared secret)
    NMS_TELEMETRY_INGEST_TOKEN: str = _get("NMS_TELEMETRY_INGEST_TOKEN", "")


config = Config()

def mqtt_configured() -> bool:
    return bool(config.MQTT_ENABLED and config.MQTT_HOST and config.MQTT_CLIENT_ID and config.MQTT_TOPIC)


def zabbix_configured() -> bool:
    return bool(config.ZABBIX_URL and config.ZABBIX_TOKEN)


# email_configured() was deleted: it reported a placeholder Gmail provider as
# configured, and the honest state now comes from the provider itself
# (notifications.EmailProvider.state) via startup_state().


def startup_state() -> dict:
    if config.EMAIL_PROVIDER == "disabled":
        email_state = "DISABLED"
    elif config.EMAIL_PROVIDER == "gmail":
        # The Gmail provider is a placeholder that raises on send; calling it
        # CONFIGURED would claim a working integration that does not exist.
        email_state = "NOT IMPLEMENTED"
    else:
        email_state = "NOT CONFIGURED"
    return {
        "mqtt": "CONFIGURED" if mqtt_configured() else "NOT CONFIGURED",
        "zabbix": "CONFIGURED" if zabbix_configured() else "NOT CONFIGURED",
        "email": email_state,
    }
