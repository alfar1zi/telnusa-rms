"""SQLite persistence. No telemetry history duplication; Zabbix remains history."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time

from .config import config

logger = logging.getLogger("telnusa.store")


class IdentityConflictError(ValueError):
    pass


def compute_freshness(source_ts: int | None, at_ms: int | None = None) -> str:
    if source_ts is None:
        return "no_data"
    at = at_ms if at_ms is not None else int(time.time() * 1000)
    age = max(0, (at - source_ts) // 1000)
    if age <= config.FRESHNESS_LIVE_SECONDS:
        return "fresh"
    if age <= config.FRESHNESS_STALE_SECONDS:
        return "stale"
    return "offline"


def compute_status(
    data_age: str,
    skewed: bool = False,
    mppt_state: str = "no_data",
    pzem_state: str = "no_data",
    sensor3_state: str = "not_integrated",
) -> str:
    """Deterministic precedence model for primary operator status:
    1. clock_skew: if device clock is skewed far behind receipt
    2. offline: if data_age == 'offline'
    3. no_data: if data_age == 'no_data'
    4. stale: if data_age == 'stale'
    5. sensor_fault: if any sensor reports hardware fault
    6. partial: if any contracted sensor is partial, or sensors disagree
       (one reporting data while another reports none)
    7. online: if data_age == 'fresh' and sensors are all_ok
    8. unknown: fallback

    Status stays a function of ingestion freshness plus per-sensor state; an
    individual stale/absent sensor degrades `partial`, it never marks the whole
    device offline. `sensor3_state` defaults to the neutral 'not_integrated' so
    callers that predate the sensor's integration are not penalised; it is the
    live device row (with_dynamic_state/process_payload) that supplies the real
    value.
    """
    if skewed:
        return "clock_skew"
    if data_age == "offline":
        return "offline"
    if data_age == "no_data":
        return "no_data"
    if data_age == "stale":
        return "stale"
    states = (mppt_state, pzem_state, sensor3_state)
    if any(s == "sensor_fault" for s in states):
        return "sensor_fault"
    if any(s == "partial" for s in states):
        return "partial"
    if any(s == "all_ok" for s in states) and any(s == "no_data" for s in states):
        return "partial"
    if data_age == "fresh":
        return "online"
    return "unknown"


# Display thresholds for the router's own resource readings. They are the
# conservative defaults for a 128 MB / 16 MB-flash RUT-class device and are the
# single place to tune them; nothing here raises an alarm, it only labels the
# health badge with the numbers the router reported.
HEALTH_RAM_WARN_PCT = 85.0
HEALTH_RAM_CRITICAL_PCT = 95.0
HEALTH_FLASH_WARN_PCT = 85.0
HEALTH_FLASH_CRITICAL_PCT = 95.0
HEALTH_TEMP_WARN_C = 65.0
HEALTH_TEMP_CRITICAL_C = 75.0
HEALTH_LOAD_WARN_PER_CORE = 1.5


def compute_health(health: dict | None) -> str:
    """Label the device's system health from the values the router measured.

    States: healthy, warning, critical, no_data. `not_integrated` is gone: the
    router reports its own resource health now, so an empty reading means "no
    data yet", which is a different fact.
    """
    values = {k: v for k, v in (health or {}).items() if v is not None}
    if not values:
        return "no_data"
    ram = values.get("ramPercent")
    flash = values.get("flashPercent")
    temp = values.get("temperatureC")
    load1 = values.get("load1")
    cores = values.get("cores")
    load_per_core = (load1 / cores) if isinstance(load1, (int, float)) and isinstance(cores, (int, float)) and cores else None
    if (isinstance(ram, (int, float)) and ram >= HEALTH_RAM_CRITICAL_PCT) or \
       (isinstance(flash, (int, float)) and flash >= HEALTH_FLASH_CRITICAL_PCT) or \
       (isinstance(temp, (int, float)) and temp >= HEALTH_TEMP_CRITICAL_C):
        return "critical"
    if (isinstance(ram, (int, float)) and ram >= HEALTH_RAM_WARN_PCT) or \
       (isinstance(flash, (int, float)) and flash >= HEALTH_FLASH_WARN_PCT) or \
       (isinstance(temp, (int, float)) and temp >= HEALTH_TEMP_WARN_C) or \
       (load_per_core is not None and load_per_core >= HEALTH_LOAD_WARN_PER_CORE):
        return "warning"
    return "healthy"


def compute_connectivity(data_age: str) -> str:
    """Connectivity of the primary telemetry transport (direct HTTP ingest).

    Derived from ingest freshness rather than from any single optional
    transport: a rejected MQTT session must not paint a device that is
    delivering fresh samples as disconnected, and vice versa.
    """
    if data_age == "fresh":
        return "connected"
    if data_age == "stale":
        return "degraded"
    if data_age == "offline":
        return "disconnected"
    return "no_data"

def clock_skew_seconds(last_seen: int | None, latest_received_at: int | None) -> int | None:
    """received - source in seconds. Positive = device clock behind the server."""
    if last_seen is None or latest_received_at is None:
        return None
    return (int(latest_received_at) - int(last_seen)) // 1000


def is_clock_skewed(last_seen: int | None, latest_received_at: int | None, now_ms: int | None = None) -> bool:
    """True only when transport is alive but the source clock is far behind.

    Both halves are required: recent receipt without recency would pin a
    long-dead device as degraded forever instead of letting it go offline.
    """
    if last_seen is None or latest_received_at is None:
        return False
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if now - int(latest_received_at) > config.FRESHNESS_LIVE_SECONDS * 1000:
        return False
    return int(latest_received_at) - int(last_seen) > config.CLOCK_SKEW_THRESHOLD_SECONDS * 1000


def with_dynamic_state(row: dict | sqlite3.Row, at_ms: int | None = None) -> dict:
    d = Store.row_dict(row)
    age = compute_freshness(d.get("last_seen"), at_ms)
    now = at_ms if at_ms is not None else int(time.time() * 1000)
    skewed = is_clock_skewed(d.get("last_seen"), d.get("latest_received_at"), now)
    mppt_state = d.get("mppt_state", "no_data")
    pzem_state = d.get("pzem_state", "no_data")
    # Sensor 3 (solar irradiance) is integrated system-wide as of 2026-10-20;
    # the historical 'not_integrated' placeholder now only means "this device
    # has not reported the field", which is exactly no_data.
    sensor3_state = d.get("sensor3_state", "no_data") or "no_data"
    if sensor3_state == "not_integrated":
        sensor3_state = "no_data"
    states = (mppt_state, pzem_state, sensor3_state)
    if any(s == "sensor_fault" for s in states):
        acq_state = "sensor_fault"
    elif all(s == "all_ok" for s in states):
        acq_state = "all_ok"
    elif all(s == "no_data" for s in states):
        acq_state = "no_data"
    else:
        acq_state = "partial"
    # System health comes from the router's own readings in the latest payload,
    # not from a stored stub.
    payload = d.get("last_payload")
    payload_health = payload.get("health") if isinstance(payload, dict) else None
    d["health"] = compute_health(payload_health)
    d["mppt_state"] = mppt_state
    d["pzem_state"] = pzem_state
    d["sensor3_state"] = sensor3_state
    d["acquisition_state"] = acq_state
    d["data_age"] = age
    d["connectivity"] = compute_connectivity(age)
    d["status"] = compute_status(age, skewed, mppt_state, pzem_state, sensor3_state)
    d["clock_skew"] = skewed
    d["clock_skew_seconds"] = clock_skew_seconds(d.get("last_seen"), d.get("latest_received_at"))
    return d

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT,
  role TEXT NOT NULL,
  provider TEXT NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  csrf TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL,
  serial TEXT NOT NULL UNIQUE,
  site_name TEXT,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_seen INTEGER,
  latest_received_at INTEGER,
  latest_processed_at INTEGER,
  network TEXT,
  health TEXT NOT NULL DEFAULT 'unknown',
  connectivity TEXT NOT NULL DEFAULT 'unknown',
  data_age TEXT NOT NULL DEFAULT 'no_data',
  mppt_state TEXT NOT NULL DEFAULT 'no_data',
  pzem_state TEXT NOT NULL DEFAULT 'no_data',
  sensor3_state TEXT NOT NULL DEFAULT 'not_integrated',
  acquisition_state TEXT NOT NULL DEFAULT 'no_data',
  last_payload TEXT,
  received_at INTEGER,
  zabbix_hostid TEXT,
  integration_status TEXT NOT NULL DEFAULT 'no_data',
  first_seen INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(site_id, serial)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  actor TEXT,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS unmapped_fields (
  field TEXT NOT NULL,
  observed_type TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  count INTEGER NOT NULL DEFAULT 1,
  example_device TEXT,
  PRIMARY KEY(field, observed_type)
);
CREATE TABLE IF NOT EXISTS processed_messages (
  message_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  source_ts INTEGER NOT NULL,
  received_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_state (
  component TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  configured INTEGER NOT NULL DEFAULT 0,
  reachable INTEGER,
  authenticated INTEGER,
  last_success INTEGER,
  last_error TEXT,
  updated_at INTEGER NOT NULL,
  metrics TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS zabbix_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  host TEXT NOT NULL,
  payload TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zabbix_queue_device ON zabbix_queue(device_id);
CREATE TABLE IF NOT EXISTS zabbix_dead_letter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  host TEXT NOT NULL,
  payload TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  dead_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_recipients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry_rollups_hourly (
  device_id TEXT NOT NULL,
  bucket_start INTEGER NOT NULL,
  bucket_end INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  expected_count INTEGER NOT NULL DEFAULT 180,
  coverage_ratio REAL NOT NULL,
  energy_start_kwh REAL,
  energy_end_kwh REAL,
  energy_delta_kwh REAL NOT NULL DEFAULT 0.0,
  energy_quality TEXT NOT NULL DEFAULT 'VALID',
  pv_v_avg REAL,
  pv_v_min REAL,
  pv_v_max REAL,
  pv_a_avg REAL,
  pv_a_min REAL,
  pv_a_max REAL,
  pv_power_avg REAL,
  pv_power_min REAL,
  pv_power_max REAL,
  out_v_avg REAL,
  out_v_min REAL,
  out_v_max REAL,
  out_a_avg REAL,
  out_a_min REAL,
  out_a_max REAL,
  out_power_avg REAL,
  out_power_min REAL,
  out_power_max REAL,
  pzem_va_avg REAL,
  pzem_va_min REAL,
  pzem_va_max REAL,
  pzem_vb_avg REAL,
  pzem_vb_min REAL,
  pzem_vb_max REAL,
  pzem_vc_avg REAL,
  pzem_vc_min REAL,
  pzem_vc_max REAL,
  pzem_ia_avg REAL,
  pzem_ia_min REAL,
  pzem_ia_max REAL,
  pzem_ib_avg REAL,
  pzem_ib_min REAL,
  pzem_ib_max REAL,
  pzem_ic_avg REAL,
  pzem_ic_min REAL,
  pzem_ic_max REAL,
  solar_rad_avg REAL,
  solar_rad_min REAL,
  solar_rad_max REAL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(device_id, bucket_start)
);
CREATE TABLE IF NOT EXISTS telemetry_rollups_daily (
  device_id TEXT NOT NULL,
  bucket_start INTEGER NOT NULL,
  bucket_end INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  expected_count INTEGER NOT NULL DEFAULT 4320,
  coverage_ratio REAL NOT NULL,
  energy_start_kwh REAL,
  energy_end_kwh REAL,
  energy_delta_kwh REAL NOT NULL DEFAULT 0.0,
  energy_quality TEXT NOT NULL DEFAULT 'VALID',
  pv_v_avg REAL,
  pv_v_min REAL,
  pv_v_max REAL,
  pv_a_avg REAL,
  pv_a_min REAL,
  pv_a_max REAL,
  pv_power_avg REAL,
  pv_power_min REAL,
  pv_power_max REAL,
  out_v_avg REAL,
  out_v_min REAL,
  out_v_max REAL,
  out_a_avg REAL,
  out_a_min REAL,
  out_a_max REAL,
  out_power_avg REAL,
  out_power_min REAL,
  out_power_max REAL,
  pzem_va_avg REAL,
  pzem_va_min REAL,
  pzem_va_max REAL,
  pzem_vb_avg REAL,
  pzem_vb_min REAL,
  pzem_vb_max REAL,
  pzem_vc_avg REAL,
  pzem_vc_min REAL,
  pzem_vc_max REAL,
  pzem_ia_avg REAL,
  pzem_ia_min REAL,
  pzem_ia_max REAL,
  pzem_ib_avg REAL,
  pzem_ib_min REAL,
  pzem_ib_max REAL,
  pzem_ic_avg REAL,
  pzem_ic_min REAL,
  pzem_ic_max REAL,
  solar_rad_avg REAL,
  solar_rad_min REAL,
  solar_rad_max REAL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(device_id, bucket_start)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial);
CREATE INDEX IF NOT EXISTS idx_processed_messages_received ON processed_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_rollups_hourly_device_ts ON telemetry_rollups_hourly(device_id, bucket_start);
CREATE INDEX IF NOT EXISTS idx_rollups_daily_device_ts ON telemetry_rollups_daily(device_id, bucket_start);
CREATE TABLE IF NOT EXISTS alarm_rules (
  rule_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  metric TEXT NOT NULL,
  device_pattern TEXT NOT NULL DEFAULT '*',
  severity TEXT NOT NULL,
  operator TEXT NOT NULL,
  threshold TEXT,
  evaluation_interval INTEGER NOT NULL DEFAULT 60,
  consecutive_count INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alarms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  site_id TEXT NOT NULL,
  rule_id TEXT NOT NULL REFERENCES alarm_rules(rule_id),
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'OPEN',
  observed_value TEXT,
  evidence TEXT,
  first_occurrence INTEGER NOT NULL,
  last_occurrence INTEGER NOT NULL,
  consecutive_occurrences INTEGER NOT NULL DEFAULT 1,
  acknowledged_at INTEGER,
  acknowledged_by TEXT,
  ack_note TEXT,
  resolved_at INTEGER,
  resolved_by TEXT,
  resolution_reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alarm_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alarm_id INTEGER NOT NULL REFERENCES alarms(id) ON DELETE CASCADE,
  device_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT,
  timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alarms_device_state ON alarms(device_id, state);
CREATE INDEX IF NOT EXISTS idx_alarms_rule_state ON alarms(rule_id, state);
CREATE INDEX IF NOT EXISTS idx_alarms_state ON alarms(state);
CREATE INDEX IF NOT EXISTS idx_alarm_transitions_alarm_id ON alarm_transitions(alarm_id);
"""

DEVICE_MIGRATIONS = {
    "latest_received_at": "INTEGER",
    "latest_processed_at": "INTEGER",
    "integration_status": "TEXT NOT NULL DEFAULT 'no_data'",
    "first_seen": "INTEGER NOT NULL DEFAULT 0",
    "mppt_state": "TEXT NOT NULL DEFAULT 'no_data'",
    "pzem_state": "TEXT NOT NULL DEFAULT 'no_data'",
    "sensor3_state": "TEXT NOT NULL DEFAULT 'not_integrated'",
    "acquisition_state": "TEXT NOT NULL DEFAULT 'no_data'",
}
UNMAPPED_MIGRATIONS = {"observed_type": "TEXT NOT NULL DEFAULT 'unknown'"}
# Solar irradiance rollup columns (added 2026-10-20 with sensor 3 integration).
ROLLUP_MIGRATIONS = {
    "solar_rad_avg": "REAL",
    "solar_rad_min": "REAL",
    "solar_rad_max": "REAL",
}
USER_MIGRATIONS = {
    "must_change_password": "INTEGER NOT NULL DEFAULT 0",
    "disabled": "INTEGER NOT NULL DEFAULT 0",
    "updated_at": "INTEGER NOT NULL DEFAULT 0",
}
# Connection-level SQLite pragmas. Applied on every connection this store opens.
#
# - foreign_keys: OFF is the SQLite default, so FK declarations in SCHEMA are inert
#   unless enabled per connection.
# - WAL: readers no longer block the writer, which matters because the MQTT collector,
#   Zabbix delivery worker, and HTTP request threads all share this store.
# - busy_timeout: makes a contended write wait for the lock instead of raising
#   "database is locked" immediately.
DB_BUSY_TIMEOUT_MS = 5000


def apply_connection_pragmas(db: sqlite3.Connection) -> None:
    """Apply foreign_keys/busy_timeout/WAL to a connection. Never raises."""
    db.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA foreign_keys=ON")
    # WAL is a persistent database property, but it cannot be used by in-memory
    # databases and can fail on a read-only or concurrently-locked file. In every
    # one of those cases SQLite stays on its existing journal mode, which is correct.
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass


class Store:
    def __init__(self, path: str):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        apply_connection_pragmas(self.db)
        self.db.row_factory = sqlite3.Row
        with self._lock, self.db:
            self.db.executescript(SCHEMA)
            self._migrate()
        try:
            from .alarm_engine import AlarmEngine
            AlarmEngine(self).seed_default_rules()
        except Exception:
            try:
                from alarm_engine import AlarmEngine
                AlarmEngine(self).seed_default_rules()
            except Exception as exc:
                # A store with no seeded rules evaluates no alarms at all; that
                # must be visible in the journal, not inferred from silence.
                logger.warning("alarm rule seeding failed: %r", exc)
    def _migrate(self) -> None:
        # One-off: rows created before ids were hashed still hold the raw cookie.
        # Idempotent - a hashed row is a 64-char hex digest and is skipped.
        with self._lock, self.db:
            for (sid,) in self.db.execute("SELECT id FROM sessions").fetchall():
                text = str(sid)
                if len(text) == 64 and all(c in "0123456789abcdef" for c in text):
                    continue
                self.db.execute("UPDATE sessions SET id = ? WHERE id = ?",
                                (hashlib.sha256(text.encode("utf-8")).hexdigest(), text))
        def cols(table: str) -> set[str]:
            return {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
        existing = cols("devices")
        for name, ddl in DEVICE_MIGRATIONS.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE devices ADD COLUMN {name} {ddl}")
        existing = cols("users")
        for name, ddl in USER_MIGRATIONS.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        for table in ("telemetry_rollups_hourly", "telemetry_rollups_daily"):
            rollup_existing = cols(table)
            if rollup_existing:
                for name, ddl in ROLLUP_MIGRATIONS.items():
                    if name not in rollup_existing:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        existing = cols("unmapped_fields")
        pk = [r[1] for r in self.db.execute("PRAGMA table_info(unmapped_fields)") if r[5]]
        if existing and pk == ["field"]:
            source_has_type = "observed_type" in existing
            self.db.execute("ALTER TABLE unmapped_fields RENAME TO unmapped_fields_old")
            self.db.execute("CREATE TABLE unmapped_fields(field TEXT NOT NULL, observed_type TEXT NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 1, example_device TEXT, PRIMARY KEY(field, observed_type))")
            if source_has_type:
                self.db.execute("INSERT OR IGNORE INTO unmapped_fields(field, observed_type, first_seen, last_seen, count, example_device) SELECT field, COALESCE(observed_type,'unknown'), first_seen, last_seen, count, example_device FROM unmapped_fields_old")
            else:
                self.db.execute("INSERT OR IGNORE INTO unmapped_fields(field, observed_type, first_seen, last_seen, count, example_device) SELECT field, 'unknown', first_seen, last_seen, count, example_device FROM unmapped_fields_old")
            self.db.execute("DROP TABLE unmapped_fields_old")
        else:
            for name, ddl in UNMAPPED_MIGRATIONS.items():
                if name not in existing:
                    self.db.execute(f"ALTER TABLE unmapped_fields ADD COLUMN {name} {ddl}")
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "zabbix_dead_letter" not in tables:
            self.db.execute("CREATE TABLE zabbix_dead_letter(id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, device_id TEXT NOT NULL, host TEXT NOT NULL, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, dead_at INTEGER NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_zabbix_queue_device ON zabbix_queue(device_id)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_processed_messages_received ON processed_messages(received_at)")
        if "alarm_rules" not in tables:
            self.db.execute("CREATE TABLE alarm_rules (rule_id TEXT PRIMARY KEY, name TEXT NOT NULL, metric TEXT NOT NULL, device_pattern TEXT NOT NULL DEFAULT '*', severity TEXT NOT NULL, operator TEXT NOT NULL, threshold TEXT, evaluation_interval INTEGER NOT NULL DEFAULT 60, consecutive_count INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1, description TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
        if "alarms" not in tables:
            self.db.execute("CREATE TABLE alarms (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, site_id TEXT NOT NULL, rule_id TEXT NOT NULL REFERENCES alarm_rules(rule_id), severity TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN', observed_value TEXT, evidence TEXT, first_occurrence INTEGER NOT NULL, last_occurrence INTEGER NOT NULL, consecutive_occurrences INTEGER NOT NULL DEFAULT 1, acknowledged_at INTEGER, acknowledged_by TEXT, ack_note TEXT, resolved_at INTEGER, resolved_by TEXT, resolution_reason TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
        if "alarm_transitions" not in tables:
            self.db.execute("CREATE TABLE alarm_transitions (id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id INTEGER NOT NULL REFERENCES alarms(id) ON DELETE CASCADE, device_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT, timestamp INTEGER NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_alarms_device_state ON alarms(device_id, state)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_alarms_rule_state ON alarms(rule_id, state)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_alarms_state ON alarms(state)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_alarm_transitions_alarm_id ON alarm_transitions(alarm_id)")

    def create_user(self, email: str, display_name: str, password_hash: str | None, role: str = "viewer", provider: str = "local", must_change_password: int = 0, disabled: int = 0) -> int:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            cur = self.db.execute(
                "INSERT INTO users(email, display_name, password_hash, role, provider, must_change_password, disabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (email.lower(), display_name, password_hash, role, provider, int(must_change_password), int(disabled), now, now),
            )
            return cur.lastrowid

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT id, email, display_name, role, provider, must_change_password, disabled, created_at, updated_at FROM users ORDER BY id ASC").fetchall()
            return [self.row_dict(r) for r in rows]

    def get_user_by_email(self, email: str):
        with self._lock:
            return self.db.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()

    def get_user_by_id(self, uid: int | str):
        with self._lock:
            return self.db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    def set_user_role(self, uid: int | str, role: str) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now, uid))

    def set_user_password(self, uid: int | str, password_hash: str, must_change_password: int = 0) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute("UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?", (password_hash, int(must_change_password), now, uid))

    def update_user_profile(self, uid: int | str, display_name: str | None = None, role: str | None = None, disabled: int | None = None, must_change_password: int | None = None) -> None:
        now = int(time.time() * 1000)
        updates = ["updated_at = ?"]
        params: list[object] = [now]
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if disabled is not None:
            updates.append("disabled = ?")
            params.append(int(disabled))
        if must_change_password is not None:
            updates.append("must_change_password = ?")
            params.append(int(must_change_password))
        params.append(uid)
        with self._lock, self.db:
            self.db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)

    def count_active_admins(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0]

    def user_count(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    @staticmethod
    def _session_key(sid) -> str:
        """The sessions table never stores the cookie value.

        The row used to *be* the token: any copy of the database (support archive,
        filesystem snapshot, a future permission slip) was a jar of live sessions,
        CSRF included. Lookups hash the cookie the same way.
        """
        return hashlib.sha256(str(sid).encode("utf-8")).hexdigest()

    def create_session(self, sid, user_id, expires_at, csrf) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO sessions(id, user_id, created_at, expires_at, csrf) VALUES (?,?,?,?,?)",
                (self._session_key(sid), user_id, now, expires_at, csrf),
            )

    def get_session(self, sid):
        with self._lock:
            return self.db.execute("SELECT * FROM sessions WHERE id = ?", (self._session_key(sid),)).fetchone()

    def delete_session(self, sid) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE id = ?", (self._session_key(sid),))

    def delete_user_sessions(self, uid) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

    def delete_user_sessions_except(self, uid, keep_sid) -> None:
        """Revoke every session of a user except the one making the request."""
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?",
                            (uid, self._session_key(keep_sid)))

    def purge_expired_sessions(self) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))

    def mark_message(self, message_id: str, device_id: str, source_ts: int, received_at: int) -> bool:
        with self._lock, self.db:
            try:
                self.db.execute(
                    "INSERT INTO processed_messages(message_id, device_id, source_ts, received_at) VALUES (?,?,?,?)",
                    (message_id, device_id, source_ts, received_at),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def upsert_device(self, row: dict) -> bool:
        now = int(time.time() * 1000)
        row = dict(row)
        row.setdefault("created_at", now)
        row.setdefault("first_seen", now)
        row["updated_at"] = now
        with self._lock, self.db:
            existing = self.db.execute(
                "SELECT * FROM devices WHERE device_id = ? OR serial = ?",
                (row["device_id"], row["serial"]),
            ).fetchone()
            if existing:
                if existing["site_id"] != row["site_id"]:
                    raise IdentityConflictError(
                        f"Device {row['device_id']} (serial {row['serial']}) is registered to site {existing['site_id']}, conflict with {row['site_id']}"
                    )
                if existing["last_seen"] is not None and row.get("last_seen") is not None:
                    if int(row["last_seen"]) < int(existing["last_seen"]):
                        self.db.execute(
                            "UPDATE devices SET updated_at=?, received_at=?, latest_received_at=? WHERE device_id=?",
                            (now, row.get("received_at"), row.get("latest_received_at"), row["device_id"]),
                        )
                        return False
                row["created_at"] = existing["created_at"]
                row["first_seen"] = existing["first_seen"] or existing["created_at"]
            cols = ",".join(row.keys())
            ph = ",".join("?" for _ in row)
            upd = ",".join(f"{c}=excluded.{c}" for c in row if c not in ("device_id", "created_at", "first_seen"))
            self.db.execute(
                f"INSERT INTO devices({cols}) VALUES ({ph}) ON CONFLICT(device_id) DO UPDATE SET {upd}",
                tuple(row.values()),
            )
            return True

    def purge_old_processed_messages(self, retention_seconds: int | None = None, now_ms: int | None = None) -> int:
        retention = retention_seconds if retention_seconds is not None else config.PROCESSED_MESSAGES_RETENTION_SECONDS
        current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff_ms = current_ms - (retention * 1000)
        with self._lock, self.db:
            cur = self.db.execute("DELETE FROM processed_messages WHERE received_at < ?", (cutoff_ms,))
            return cur.rowcount

    def set_device_zabbix_hostid(self, device_id: str, hostid: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE devices SET zabbix_hostid=?, updated_at=? WHERE device_id=?", (hostid, int(time.time() * 1000), device_id))

    def get_device(self, device_id: str, at_ms: int | None = None, now_ms: int | None = None) -> dict | None:
        effective_ms = at_ms if at_ms is not None else now_ms
        with self._lock:
            row = self.db.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            return with_dynamic_state(row, effective_ms) if row else None

    def list_devices_raw(self) -> list[dict]:
        """Stored device rows without dynamic freshness recompute (for the aging sweep)."""
        with self._lock:
            rows = self.db.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
            return [Store.row_dict(r) for r in rows]

    def update_device_state(self, device_id: str, status: str, data_age: str, now_ms: int | None = None) -> None:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute(
                "UPDATE devices SET status=?, data_age=?, connectivity=?, updated_at=? WHERE device_id=?",
                (status, data_age, compute_connectivity(data_age), now, device_id),
            )

    def list_devices(self, at_ms: int | None = None, now_ms: int | None = None) -> list[dict]:
        effective_ms = at_ms if at_ms is not None else now_ms
        with self._lock:
            rows = self.db.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
            return [with_dynamic_state(r, effective_ms) for r in rows]

    def count_devices_by_age(self, at_ms: int | None = None, now_ms: int | None = None) -> dict[str, int]:
        effective_ms = at_ms if at_ms is not None else now_ms
        with self._lock:
            rows = self.db.execute("SELECT * FROM devices").fetchall()
            total = len(rows)
            live = 0
            stale = 0
            offline = 0
            no_data = 0
            degraded = 0
            for r in rows:
                skewed = is_clock_skewed(r["last_seen"], r["latest_received_at"] if "latest_received_at" in r.keys() else None, effective_ms)
                age = compute_freshness(r["last_seen"], effective_ms)
                if skewed:
                    degraded += 1
                elif age == "fresh":
                    live += 1
                elif age == "stale":
                    stale += 1
                elif age == "offline":
                    offline += 1
                else:
                    no_data += 1
            return {
                "total_devices": total,
                "online_devices": live + stale,
                "live_devices": live,
                "fresh_devices": live,
                "stale_devices": stale,
                "offline_devices": offline,
                "no_data_devices": no_data,
                "degraded_devices": degraded,
            }
    def audit(self, actor, action, detail="") -> None:
        now = int(time.time() * 1000)
        safe_detail = str(detail)[:500]
        with self._lock, self.db:
            self.db.execute("INSERT INTO audit(ts, actor, action, detail) VALUES (?,?,?,?)", (now, actor, action, safe_detail))
    def list_audit_logs(self, limit: int = 100):
        with self._lock:
            return self.db.execute("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()

    def record_unmapped(self, field: str, observed_type: str, device: str = "") -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO unmapped_fields(field, observed_type, first_seen, last_seen, count, example_device) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(field, observed_type) DO UPDATE SET last_seen=excluded.last_seen, count=count+1, example_device=excluded.example_device",
                (field, observed_type, now, now, 1, device),
            )
    def list_unmapped(self):
        with self._lock:
            return self.db.execute("SELECT * FROM unmapped_fields ORDER BY last_seen DESC").fetchall()

    def set_integration_state(self, component: str, state: str, *, configured: bool, reachable=None, authenticated=None, last_success=None, last_error=None, metrics: dict | None = None) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            existing = self.db.execute("SELECT metrics FROM integration_state WHERE component=?", (component,)).fetchone()
            merged = {}
            if existing:
                try:
                    merged.update(json.loads(existing["metrics"] or "{}"))
                except ValueError:
                    pass
            if metrics:
                merged.update(metrics)
            self.db.execute(
                "INSERT INTO integration_state(component,state,configured,reachable,authenticated,last_success,last_error,updated_at,metrics) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(component) DO UPDATE SET state=excluded.state, configured=excluded.configured, reachable=excluded.reachable, authenticated=excluded.authenticated, last_success=COALESCE(excluded.last_success,integration_state.last_success), last_error=excluded.last_error, updated_at=excluded.updated_at, metrics=excluded.metrics",
                (component, state, int(configured), reachable, authenticated, last_success, last_error, now, json.dumps(merged, separators=(",", ":"))),
            )

    def get_integration_state(self, component: str):
        with self._lock:
            return self.db.execute("SELECT * FROM integration_state WHERE component=?", (component,)).fetchone()

    def list_integration_state(self):
        with self._lock:
            return self.db.execute("SELECT * FROM integration_state ORDER BY component").fetchall()

    def enqueue_zabbix(self, message_id: str, device_id: str, host: str, payload: list[dict], max_rows: int) -> bool:
        """Enqueue a Zabbix batch. Full queue evicts the OLDEST row (lowest id):
        fresh telemetry matters most; dropping the newest would discard exactly
        the state the device just reported. Returns True when stored."""
        now = int(time.time() * 1000)
        with self._lock, self.db:
            count = self.db.execute("SELECT COUNT(*) FROM zabbix_queue").fetchone()[0]
            if count >= max_rows:
                oldest = self.db.execute("SELECT id, device_id FROM zabbix_queue ORDER BY id LIMIT 1").fetchone()
                if oldest is None:
                    return False
                self.db.execute("DELETE FROM zabbix_queue WHERE id=?", (oldest["id"],))
                self.db.execute(
                    "INSERT INTO zabbix_dead_letter(message_id,device_id,host,payload,attempts,last_error,created_at,updated_at,dead_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"evicted:{oldest['id']}", oldest["device_id"], host, "[]", 0, "QUEUE_EVICTED_OLDEST", now, now, now),
                )
            self.db.execute(
                "INSERT INTO zabbix_queue(message_id,device_id,host,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (message_id, device_id, host, json.dumps(payload, separators=(",", ":")), now, now),
            )
            return True

    def pop_zabbix_batch(self, limit: int = 100):
        with self._lock:
            return self.db.execute("SELECT * FROM zabbix_queue ORDER BY id LIMIT ?", (limit,)).fetchall()

    def delete_zabbix_queue(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock, self.db:
            self.db.executemany("DELETE FROM zabbix_queue WHERE id=?", [(i,) for i in ids])

    def fail_zabbix_queue(self, ids: list[int], error: str) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.executemany("UPDATE zabbix_queue SET attempts=attempts+1,last_error=?,updated_at=? WHERE id=?", [(error[:300], now, i) for i in ids])

    def pop_zabbix_due(self, limit: int, retry_max: int, base_seconds: int, now_ms: int | None = None):
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            rows = self.db.execute("SELECT * FROM zabbix_queue WHERE attempts < ? ORDER BY id LIMIT ?", (retry_max, limit)).fetchall()
            eligible = []
            for r in rows:
                attempts = int(r["attempts"])
                # 0 attempts -> eligible immediately; > 0 attempts -> apply backoff from updated_at
                backoff_ms = 0 if attempts == 0 else base_seconds * (2 ** min(attempts - 1, 6)) * 1000
                if int(r["updated_at"]) + backoff_ms <= now:
                    eligible.append(r)
            return eligible
    def move_to_dead_letter(self, ids: list[int], error: str) -> int:
        if not ids:
            return 0
        now = int(time.time() * 1000)
        moved = 0
        with self._lock, self.db:
            for i in ids:
                row = self.db.execute("SELECT * FROM zabbix_queue WHERE id=?", (i,)).fetchone()
                if not row:
                    continue
                self.db.execute(
                    "INSERT INTO zabbix_dead_letter(message_id,device_id,host,payload,attempts,last_error,created_at,updated_at,dead_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (row["message_id"], row["device_id"], row["host"], row["payload"], row["attempts"] + 1, error[:300], row["created_at"], now, now),
                )
                self.db.execute("DELETE FROM zabbix_queue WHERE id=?", (i,))
                moved += 1
        return moved
    def count_zabbix_queue(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM zabbix_queue").fetchone()[0]

    def count_dead_letter(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM zabbix_dead_letter").fetchone()[0]

    def purge_dead_letter_older_than(self, retention_days: int, now_ms: int | None = None) -> int:
        """TTL janitor for dead-letter evidence. Old rows age out; recent failures stay."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now - retention_days * 86_400_000
        with self._lock, self.db:
            cur = self.db.execute("DELETE FROM zabbix_dead_letter WHERE dead_at < ?", (cutoff,))
            return cur.rowcount
    def purge_audit_older_than(self, retention_days: int, now_ms: int | None = None) -> int:
        """Bound the audit trail.

        Production held 87k rows with no delete path at all: a control-plane table
        that only grows is a slow leak, and the retention is configurable because
        how long an audit trail is kept is an operational policy, not a constant.
        """
        if retention_days <= 0:
            return 0
        cutoff = (now_ms if now_ms is not None else int(time.time() * 1000)) - retention_days * 86_400_000
        with self._lock, self.db:
            cur = self.db.execute("DELETE FROM audit WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)

    def add_recipient(self, email, created_by) -> None:
        now = int(time.time() * 1000)
        with self._lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO notification_recipients(email, created_by, created_at) VALUES (?,?,?)", (email.lower(), created_by, now))

    def remove_recipient(self, email) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM notification_recipients WHERE email = ?", (email.lower(),))

    def list_recipients(self):
        with self._lock:
            return self.db.execute("SELECT email, created_by, created_at FROM notification_recipients ORDER BY email").fetchall()

    def save_hourly_rollup(self, r: dict) -> None:
        now = int(time.time() * 1000)
        fields = [
            "device_id", "bucket_start", "bucket_end", "sample_count", "expected_count",
            "coverage_ratio", "energy_start_kwh", "energy_end_kwh", "energy_delta_kwh",
            "energy_quality", "pv_v_avg", "pv_v_min", "pv_v_max", "pv_a_avg", "pv_a_min",
            "pv_a_max", "pv_power_avg", "pv_power_min", "pv_power_max", "out_v_avg",
            "out_v_min", "out_v_max", "out_a_avg", "out_a_min", "out_a_max", "out_power_avg",
            "out_power_min", "out_power_max", "pzem_va_avg", "pzem_va_min", "pzem_va_max",
            "pzem_vb_avg", "pzem_vb_min", "pzem_vb_max", "pzem_vc_avg", "pzem_vc_min",
            "pzem_vc_max", "pzem_ia_avg", "pzem_ia_min", "pzem_ia_max", "pzem_ib_avg",
            "pzem_ib_min", "pzem_ib_max", "pzem_ic_avg", "pzem_ic_min", "pzem_ic_max",
            "solar_rad_avg", "solar_rad_min", "solar_rad_max"
        ]
        placeholders = ",".join("?" for _ in fields) + ",?,?"
        cols = ",".join(fields) + ",created_at,updated_at"
        updates = ",".join(f"{f}=excluded.{f}" for f in fields if f not in ("device_id", "bucket_start")) + ",updated_at=excluded.updated_at"
        vals = [r.get(f) for f in fields] + [now, now]
        sql = f"INSERT INTO telemetry_rollups_hourly({cols}) VALUES ({placeholders}) ON CONFLICT(device_id, bucket_start) DO UPDATE SET {updates}"
        with self._lock, self.db:
            self.db.execute(sql, vals)

    def get_hourly_rollups(self, device_id: str, time_from_ms: int, time_till_ms: int) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM telemetry_rollups_hourly WHERE device_id=? AND bucket_start >= ? AND bucket_start <= ? ORDER BY bucket_start ASC",
                (device_id, time_from_ms, time_till_ms),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def row_dict(row) -> dict:
        d = dict(row)
        if isinstance(d.get("last_payload"), str):
            try:
                d["last_payload"] = json.loads(d["last_payload"])
            except ValueError:
                d["last_payload"] = None
        if isinstance(d.get("metrics"), str):
            try:
                d["metrics"] = json.loads(d["metrics"])
            except ValueError:
                d["metrics"] = {}
        return d

    # ── ALARM RULES CRUD ──────────────────────────────────────────────────────

    def list_alarm_rules(self, enabled_only: bool = False) -> list[dict]:
        with self._lock:
            if enabled_only:
                rows = self.db.execute("SELECT * FROM alarm_rules WHERE enabled=1 ORDER BY rule_id ASC").fetchall()
            else:
                rows = self.db.execute("SELECT * FROM alarm_rules ORDER BY rule_id ASC").fetchall()
            return [dict(r) for r in rows]

    def get_alarm_rule(self, rule_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM alarm_rules WHERE rule_id=?", (rule_id,)).fetchone()
            return dict(row) if row else None

    def upsert_alarm_rule(self, rule: dict) -> None:
        now = int(time.time() * 1000)
        fields = [
            "rule_id", "name", "metric", "device_pattern", "severity", "operator",
            "threshold", "evaluation_interval", "consecutive_count", "enabled", "description"
        ]
        defaults = {
            "device_pattern": "*",
            "severity": "warning",
            "operator": "==",
            "threshold": None,
            "evaluation_interval": 60,
            "consecutive_count": 1,
            "enabled": 1,
            "description": "",
        }
        cols = ",".join(fields) + ",created_at,updated_at"
        placeholders = ",".join("?" for _ in fields) + ",?,?"
        updates = ",".join(f"{f}=excluded.{f}" for f in fields if f != "rule_id") + ",updated_at=excluded.updated_at"
        vals = [rule.get(f, defaults.get(f)) if rule.get(f) is not None else defaults.get(f) for f in fields] + [rule.get("created_at") or now, now]
        sql = f"INSERT INTO alarm_rules({cols}) VALUES ({placeholders}) ON CONFLICT(rule_id) DO UPDATE SET {updates}"
        with self._lock, self.db:
            self.db.execute(sql, vals)

    # ── ALARMS LIFECYCLE ──────────────────────────────────────────────────────

    def list_alarms(
        self,
        device_id: str | None = None,
        state: str | None = None,
        severity: str | None = None,
        rule_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conditions = []
        params: list[Any] = []

        if device_id:
            conditions.append("a.device_id = ?")
            params.append(device_id)
        if state:
            state_upper = state.strip().upper()
            if state_upper in ("ACTIVE", "OPEN_OR_ACK"):
                conditions.append("a.state IN ('OPEN', 'ACKNOWLEDGED')")
            elif state_upper in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
                conditions.append("a.state = ?")
                params.append(state_upper)
        if severity:
            conditions.append("a.severity = ?")
            params.append(severity.lower())
        if rule_id:
            conditions.append("a.rule_id = ?")
            params.append(rule_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(limit, 1000)))

        sql = f"""
            SELECT a.*, d.site_name
            FROM alarms a
            LEFT JOIN devices d ON a.device_id = d.device_id
            {where_clause}
            ORDER BY 
                CASE WHEN a.state IN ('OPEN', 'ACKNOWLEDGED') THEN 0 ELSE 1 END,
                a.last_occurrence DESC
            LIMIT ?
        """
        with self._lock:
            rows = self.db.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_alarm(self, alarm_id: int) -> dict | None:
        with self._lock:
            row = self.db.execute(
                """
                SELECT a.*, d.site_name
                FROM alarms a
                LEFT JOIN devices d ON a.device_id = d.device_id
                WHERE a.id = ?
                """,
                (alarm_id,),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            trans_rows = self.db.execute(
                "SELECT * FROM alarm_transitions WHERE alarm_id = ? ORDER BY timestamp ASC",
                (alarm_id,),
            ).fetchall()
            res["transitions"] = [dict(t) for t in trans_rows]
            return res

    def record_alarm_occurrence(
        self,
        device_id: str,
        site_id: str,
        rule_id: str,
        severity: str,
        title: str,
        observed_value: str | None = None,
        evidence: str | None = None,
        now_ms: int | None = None,
    ) -> dict:
        """Deduplicate alarm occurrence or insert new OPEN alarm."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        with self._lock, self.db:
            existing = self.db.execute(
                """
                SELECT * FROM alarms 
                WHERE device_id = ? AND rule_id = ? AND state IN ('OPEN', 'ACKNOWLEDGED')
                ORDER BY id DESC LIMIT 1
                """,
                (device_id, rule_id),
            ).fetchone()

            if existing:
                alarm_id = existing["id"]
                self.db.execute(
                    """
                    UPDATE alarms SET
                        last_occurrence = ?,
                        consecutive_occurrences = consecutive_occurrences + 1,
                        observed_value = ?,
                        evidence = ?,
                        severity = ?,
                        title = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_ms, observed_value, evidence, severity, title, now_ms, alarm_id),
                )
                row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
                return dict(row)
            else:
                cur = self.db.execute(
                    """
                    INSERT INTO alarms (
                        device_id, site_id, rule_id, severity, title, state,
                        observed_value, evidence, first_occurrence, last_occurrence,
                        consecutive_occurrences, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (device_id, site_id, rule_id, severity, title, observed_value, evidence, now_ms, now_ms, now_ms, now_ms),
                )
                alarm_id = cur.lastrowid
                self.db.execute(
                    """
                    INSERT INTO alarm_transitions (alarm_id, device_id, from_state, to_state, actor, reason, timestamp)
                    VALUES (?, ?, NULL, 'OPEN', 'system', 'Alarm triggered', ?)
                    """,
                    (alarm_id, device_id, now_ms),
                )
                row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
                return dict(row)

    def resolve_alarm_if_active(
        self,
        device_id: str,
        rule_id: str,
        reason: str = "Condition recovered",
        now_ms: int | None = None,
        actor: str = "system",
    ) -> dict | None:
        """Auto-resolve active alarm if condition is no longer violating."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        with self._lock, self.db:
            existing = self.db.execute(
                """
                SELECT * FROM alarms 
                WHERE device_id = ? AND rule_id = ? AND state IN ('OPEN', 'ACKNOWLEDGED')
                ORDER BY id DESC LIMIT 1
                """,
                (device_id, rule_id),
            ).fetchone()

            if not existing:
                return None

            alarm_id = existing["id"]
            from_state = existing["state"]
            self.db.execute(
                """
                UPDATE alarms SET
                    state = 'RESOLVED',
                    resolved_at = ?,
                    resolved_by = ?,
                    resolution_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ms, actor, reason, now_ms, alarm_id),
            )
            self.db.execute(
                """
                INSERT INTO alarm_transitions (alarm_id, device_id, from_state, to_state, actor, reason, timestamp)
                VALUES (?, ?, ?, 'RESOLVED', ?, ?, ?)
                """,
                (alarm_id, device_id, from_state, actor, reason, now_ms),
            )
            row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
            return dict(row)

    def acknowledge_alarm(
        self,
        alarm_id: int,
        actor: str,
        note: str | None = None,
        now_ms: int | None = None,
    ) -> dict:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        with self._lock, self.db:
            existing = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
            if not existing:
                raise ValueError(f"Alarm {alarm_id} not found")

            from_state = existing["state"]
            self.db.execute(
                """
                UPDATE alarms SET
                    state = 'ACKNOWLEDGED',
                    acknowledged_at = ?,
                    acknowledged_by = ?,
                    ack_note = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ms, actor, note, now_ms, alarm_id),
            )
            self.db.execute(
                """
                INSERT INTO alarm_transitions (alarm_id, device_id, from_state, to_state, actor, reason, timestamp)
                VALUES (?, ?, ?, 'ACKNOWLEDGED', ?, ?, ?)
                """,
                (alarm_id, existing["device_id"], from_state, actor, note or "Operator acknowledged", now_ms),
            )
            row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
            return dict(row)

    def manual_resolve_alarm(
        self,
        alarm_id: int,
        actor: str,
        reason: str | None = None,
        now_ms: int | None = None,
    ) -> dict:
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        with self._lock, self.db:
            existing = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
            if not existing:
                raise ValueError(f"Alarm {alarm_id} not found")

            from_state = existing["state"]
            self.db.execute(
                """
                UPDATE alarms SET
                    state = 'RESOLVED',
                    resolved_at = ?,
                    resolved_by = ?,
                    resolution_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_ms, actor, reason or "Operator resolved", now_ms, alarm_id),
            )
            self.db.execute(
                """
                INSERT INTO alarm_transitions (alarm_id, device_id, from_state, to_state, actor, reason, timestamp)
                VALUES (?, ?, ?, 'RESOLVED', ?, ?, ?)
                """,
                (alarm_id, existing["device_id"], from_state, actor, reason or "Operator resolved", now_ms),
            )
            row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
            return dict(row)

    def list_alarm_transitions(self, alarm_id: int | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if alarm_id is not None:
                rows = self.db.execute(
                    "SELECT * FROM alarm_transitions WHERE alarm_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (alarm_id, limit),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM alarm_transitions ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def fleet_summary(self, at_ms: int | None = None) -> dict[str, Any]:
        devices = self.list_devices(at_ms=at_ms)
        total = len(devices)
        online = sum(1 for d in devices if d.get("status") == "online")
        degraded = sum(1 for d in devices if d.get("status") in ("degraded", "stale", "clock_skew", "sensor_fault", "partial"))
        offline = sum(1 for d in devices if d.get("status") == "offline")
        unknown = sum(1 for d in devices if d.get("status") in ("unknown", "no_data", "not_integrated"))

        with self._lock:
            active_alarms = self.db.execute(
                "SELECT COUNT(*) FROM alarms WHERE state IN ('OPEN', 'ACKNOWLEDGED')"
            ).fetchone()[0]

        latest_telemetry_at = None
        last_seens = [d["last_seen"] for d in devices if d.get("last_seen")]
        if last_seens:
            latest_telemetry_at = max(last_seens)

        return {
            "total": total,
            "online": online,
            "degraded": degraded,
            "offline": offline,
            "unknown": unknown,
            "activeAlarms": active_alarms,
            "latestTelemetryAt": latest_telemetry_at,
        }
