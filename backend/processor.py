"""Shared telemetry ingestion path for MQTT collector and controlled tests."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from .alarm_engine import AlarmEngine
from .config import config
from .sse import broker
from .store import IdentityConflictError, Store, clock_skew_seconds, compute_connectivity, compute_freshness, compute_status, is_clock_skewed
from .telemetry import ValidationError, evaluate_sensor_completeness, latest_shape, merge_sensor_state, parse_json_payload, validate_payload
from .zabbix_client import ZABBIX_KEYS, ZabbixError, zabbix, zabbix_values

logger = logging.getLogger("telnusa.processor")
# Delivery errors that no retry will heal: server-side config mismatches requiring
# an operator fix (template/group/item wiring). Fast-track to dead letter with
# evidence instead of burning API calls through the full retry schedule.
PERMANENT_DELIVERY_ERRORS = frozenset({
    "TEMPLATE_NOT_FOUND",
    "TEMPLATE_MISMATCH",
    "HOST_GROUP_NOT_FOUND",
    "HOST_GROUP_MISMATCH",
    "ITEM_NOT_CONFIGURED",
    "ITEM_TYPE_MISMATCH",
})

def now_ms() -> int:
    return int(time.time() * 1000)


def freshness(source_ts: int | None, at_ms: int | None = None) -> str:
    return compute_freshness(source_ts, at_ms)

def _merged_view(vt, mppt, pzem, solar, health, raw_values):
    """ValidatedTelemetry-shaped view carrying canonical merged sensor state."""
    return replace(vt, mppt=mppt, pzem=pzem, solar=solar, health=health, raw_values=raw_values)
@dataclass
class RuntimeMetrics:
    lock: threading.Lock = field(default_factory=threading.Lock)
    mqtt: dict = field(default_factory=lambda: {
        "messages_received": 0,
        "messages_valid": 0,
        "messages_invalid": 0,
        "messages_unmapped": 0,
        "reconnect_count": 0,
        "last_message_received": None,
        "current_connection_state": "NOT CONFIGURED",
        "last_error": None,
        "last_connected_at": None,
        "subscribed_topics": [],
    })
    # Primary ingest transport (router -> HTTP POST /api/telemetry/ingest).
    # Counters are per-transport on purpose: a rejected MQTT session must not
    # be able to hide a healthy HTTP path, nor the reverse.
    http: dict = field(default_factory=lambda: {
        "messages_received": 0,
        "messages_valid": 0,
        "messages_invalid": 0,
        "messages_unmapped": 0,
        "last_message_received": None,
        "last_error": None,
    })
    zabbix: dict = field(default_factory=lambda: {
        "values_submitted": 0,
        "processed": 0,
        "failed": 0,
        "queued": 0,
        "dropped": 0,
        "last_success": None,
        "last_error": None,
    })

    def inc(self, component: str, key: str, by: int = 1) -> None:
        with self.lock:
            self.__dict__[component][key] = self.__dict__[component].get(key, 0) + by

    def set(self, component: str, **values) -> None:
        with self.lock:
            self.__dict__[component].update(values)

    def snapshot(self) -> dict:
        with self.lock:
            return {"mqtt": dict(self.mqtt), "http": dict(self.http), "zabbix": dict(self.zabbix)}


metrics = RuntimeMetrics()


def process_payload(store: Store, payload: dict, *, actor: str, topic: str | None = None, received_ms: int | None = None) -> tuple[int, dict]:
    # Counters are per transport. `actor` is the trusted local caller label
    # ("http" for the telemetry ingest endpoint, "mqtt" for the collector,
    # anything else is an in-process test/system call and is counted as mqtt).
    transport = "http" if actor == "http" else "mqtt"
    metrics.inc(transport, "messages_received")
    received = received_ms if received_ms is not None else now_ms()
    metrics.set(transport, last_message_received=received)
    try:
        vt = validate_payload(
            payload,
            now_ms=received,
            processed_ms=now_ms(),
            stale_after_ms=config.FRESHNESS_STALE_SECONDS * 1000,
            future_slop_ms=config.FUTURE_TOLERANCE_SECONDS * 1000,
            topic=topic,
        )
    except ValidationError as exc:
        metrics.inc(transport, "messages_invalid")
        metrics.set(transport, last_error=str(exc))
        store.audit(actor, "mqtt.payload.rejected", str(exc))
        return 400, {"error": "validation_failed", "message": str(exc)}

    is_new = store.mark_message(vt.message_id, vt.device_id, vt.source_ts, vt.received_ts)
    if not is_new:
        store.audit(actor, "mqtt.payload.duplicate", vt.device_id)
        return 202, {"deviceId": vt.device_id, "duplicate": True, "unmapped": [u.field for u in vt.unmapped]}

    metrics.inc(transport, "messages_valid")
    if vt.unmapped:
        metrics.inc(transport, "messages_unmapped", len(vt.unmapped))
    for unknown in vt.unmapped:
        store.record_unmapped(unknown.field, unknown.observed_type, vt.device_id)

    # RC-2 canonical merge: absent/null preserve last valid value; only the
    # newest accepted state reaches Zabbix. Older-ts skips row+SSE+Zabbix;
    # equal-ts merges deterministically (first-writer-wins per field).
    prev = store.get_device(vt.device_id)
    if prev is not None and prev.get("site_id") != vt.site_id:
        metrics.inc(transport, "messages_invalid")
        store.audit(actor, "mqtt.payload.conflict", vt.device_id)
        return 409, {"error": "identity_conflict", "message": f"Device {vt.device_id} (serial {vt.serial}) is registered to site {prev.get('site_id')}, conflict with {vt.site_id}"}
    prev_last_seen = prev.get("last_seen") if prev else None
    prev_payload = prev.get("last_payload") if prev else None
    if prev_last_seen is not None and vt.source_ts < int(prev_last_seen):
        store.audit(actor, "mqtt.payload.stale_superseded", vt.device_id)
        return 202, {"deviceId": vt.device_id, "stale_superseded": True, "unmapped": [u.field for u in vt.unmapped]}
    equal_ts = prev_last_seen is not None and vt.source_ts == int(prev_last_seen)
    merged_mppt, merged_pzem, merged_solar, merged_health, merged_raw = merge_sensor_state(prev_payload, vt, equal_ts=equal_ts)
    if equal_ts and isinstance(prev_payload, dict):
        prev_mppt = prev_payload.get("mppt") or {}
        prev_pzem = prev_payload.get("pzem") or {}
        prev_solar = prev_payload.get("solar") or {}
        prev_health = prev_payload.get("health") or {}
        if merged_mppt == prev_mppt and merged_pzem == prev_pzem and merged_solar == prev_solar and merged_health == prev_health:
            store.audit(actor, "mqtt.payload.no_change", vt.device_id)
            return 202, {"deviceId": vt.device_id, "no_change": True, "unmapped": [u.field for u in vt.unmapped]}
    age = freshness(vt.source_ts, vt.received_ts)
    vt_for_shape = vt if merged_raw == vt.raw_values else _merged_view(vt, merged_mppt, merged_pzem, merged_solar, merged_health, merged_raw)
    latest = latest_shape(vt_for_shape, age)
    mppt_state, pzem_state, sensor3_state, acquisition_state = evaluate_sensor_completeness(merged_mppt, merged_pzem, merged_solar)
    skewed = is_clock_skewed(vt.source_ts, vt.received_ts, vt.received_ts)
    status = compute_status(age, skewed, mppt_state, pzem_state, sensor3_state)
    row = {
        "device_id": vt.device_id,
        "site_id": vt.site_id,
        "serial": vt.serial,
        "site_name": None,
        "status": status,
        "last_seen": vt.source_ts,
        "latest_received_at": vt.received_ts,
        "latest_processed_at": vt.processed_ts,
        "connectivity": compute_connectivity(age),
        "data_age": age,
        "mppt_state": mppt_state,
        "pzem_state": pzem_state,
        "sensor3_state": sensor3_state,
        "acquisition_state": acquisition_state,
        "last_payload": json.dumps(latest, separators=(",", ":")),
        "received_at": vt.received_ts,
        "integration_status": "accepted",
    }
    try:
        latest_updated = store.upsert_device(row)
    except IdentityConflictError as exc:
        metrics.inc(transport, "messages_invalid")
        store.audit(actor, "mqtt.payload.conflict", str(exc))
        return 409, {"error": "identity_conflict", "message": str(exc)}
    zabbix_outcome: str | None = None
    if latest_updated:
        zabbix_outcome = queue_zabbix(store, vt_for_shape)
    else:
        metrics.inc("zabbix", "skipped_stale")
        store.audit(actor, "mqtt.payload.zabbix_skipped_stale", vt.device_id)
    if latest_updated:
        if transport == "http":
            store.set_integration_state(
                "http", "CONNECTED", configured=True, reachable=True,
                last_success=vt.received_ts, last_error=None,
                metrics=metrics.snapshot()["http"],
            )
        broker.publish(vt.device_id, {"type": "telemetry", "payload": latest})
        broker.publish(vt.device_id, {"type": "status", "payload": {
            "deviceId": vt.device_id,
            "status": row["status"],
            "connectivity": row["connectivity"],
            "dataAge": age,
            "lastSeen": vt.source_ts,
            "mpptState": mppt_state,
            "pzemState": pzem_state,
            "sensor3State": sensor3_state,
            "acquisitionState": acquisition_state,
            "clockSkew": skewed,
            "clockSkewSeconds": (vt.received_ts - vt.source_ts) // 1000,
        }})
        try:
            outcomes = AlarmEngine(store).evaluate_device(vt.device_id, payload=latest, now_ms=vt.received_ts)
        except Exception as exc:
            # Ingestion continues either way, but a swallowed alarm failure is
            # invisible: the payload looks accepted while no alarm is ever raised.
            logger.warning("alarm evaluation failed for %s: %r", vt.device_id, exc)
            try:
                store.audit(actor, "alarm.evaluate.failed", f"{vt.device_id}: {type(exc).__name__}")
            except Exception:
                pass
        else:
            # The live stream is the only place a raised alarm becomes a sound in
            # the operator's browser, so a new raise is published here. Only the
            # first occurrence is sent: a continuing alarm re-evaluated every 20 s
            # must not beep the NOC on every payload.
            for outcome in outcomes or []:
                if outcome.get("action") != "raised_or_updated":
                    continue
                alarm = outcome.get("alarm") or {}
                try:
                    occurrences = int(alarm.get("consecutive_occurrences") or 0)
                except (TypeError, ValueError):
                    occurrences = 0
                severity = str(alarm.get("severity") or "").lower()
                if occurrences > 1 or severity not in ("high", "disaster"):
                    continue
                broker.publish(vt.device_id, {"type": "alarm", "payload": {
                    "deviceId": vt.device_id,
                    "alarmId": str(alarm.get("id") or ""),
                    "severity": severity,
                    "problem": str(alarm.get("title") or ""),
                    "raisedAt": vt.received_ts,
                }})
    store.audit(actor, "mqtt.payload.accepted", vt.device_id)
    res: dict = {"deviceId": vt.device_id, "unmapped": [u.field for u in vt.unmapped]}
    if zabbix_outcome not in (None, "queued"):
        res["zabbix"] = zabbix_outcome
    return 202, res


def process_raw_mqtt(store: Store, raw: bytes, *, topic: str, received_ms: int | None = None) -> tuple[int, dict]:
    try:
        payload = parse_json_payload(raw)
    except ValidationError as exc:
        metrics.inc("mqtt", "messages_received")
        metrics.inc("mqtt", "messages_invalid")
        store.audit("mqtt", "mqtt.payload.rejected", str(exc))
        return 400, {"error": "validation_failed", "message": str(exc)}
    return process_payload(store, payload, actor="mqtt", topic=topic, received_ms=received_ms)


def queue_zabbix(store: Store, vt) -> str:
    """Enqueue validated values. Returns the queue outcome for the ingest response."""
    values = zabbix_values(vt)
    if not values:
        return "no_values"
    before = store.count_zabbix_queue()
    ok = store.enqueue_zabbix(vt.message_id, vt.device_id, vt.device_id, values, config.ZABBIX_QUEUE_MAX)
    if ok:
        metrics.inc("zabbix", "queued", len(values))
        if not zabbix.configured:
            store.set_integration_state("zabbix", "NOT CONFIGURED", configured=False, metrics=metrics.snapshot()["zabbix"])
        return "evicted_oldest" if store.count_zabbix_queue() <= before else "queued"
    metrics.inc("zabbix", "dropped", len(values))
    metrics.set("zabbix", last_error="ZABBIX_QUEUE_OVERFLOW")
    store.audit("system", "zabbix.queue.overflow", vt.device_id)
    store.set_integration_state("zabbix", "DEGRADED", configured=zabbix.configured, reachable=None, last_error="ZABBIX_QUEUE_OVERFLOW", metrics=metrics.snapshot()["zabbix"])
    return "overflow_dropped"

def integration_status(store: Store) -> dict:
    rows = {r["component"]: Store.row_dict(r) for r in store.list_integration_state()}
    snap = metrics.snapshot()
    device_counts = store.count_devices_by_age()
    mqtt_row = dict(rows.get("mqtt", {"component": "mqtt", "state": snap["mqtt"]["current_connection_state"], "configured": int(config.MQTT_ENABLED)}))
    mqtt_row["metrics"] = snap["mqtt"]
    # Primary transport. `state` reflects the last accepted direct HTTP sample,
    # not the MQTT session, so the two never mask each other.
    http_row = dict(rows.get("http", {
        "component": "http",
        "state": "NOT CONFIGURED" if not config.NMS_TELEMETRY_INGEST_ENABLED else "NO DATA",
        "configured": int(config.NMS_TELEMETRY_INGEST_ENABLED),
    }))
    http_row["metrics"] = snap["http"]
    zabbix_row = dict(rows.get("zabbix", {"component": "zabbix", "state": "NOT CONFIGURED" if not zabbix.configured else "CONFIGURED", "configured": int(zabbix.configured)}))
    zabbix_row["metrics"] = snap["zabbix"]
    return {
        "mqtt": mqtt_row,
        "http": http_row,
        "zabbix": zabbix_row,
        "sse": broker.stats(),
        "devices": device_counts,
    }


class ZabbixDeliveryWorker:
    def __init__(self, store: Store, interval: float = 5.0):
        self.store = store
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="zabbix-delivery", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.flush_once()

    def flush_once(self) -> None:
        if not zabbix.configured:
            self.store.set_integration_state("zabbix", "NOT CONFIGURED", configured=False, metrics=metrics.snapshot()["zabbix"])
            return
        rows = self.store.pop_zabbix_due(100, config.ZABBIX_RETRY_MAX, config.ZABBIX_RETRY_BASE_SECONDS)
        if not rows:
            purged = self.store.purge_dead_letter_older_than(config.ZABBIX_DLQ_RETENTION_DAYS)
            if purged:
                metrics.inc("zabbix", "dead_letter_purged", purged)
            # Retention also runs on idle ticks, at most every five minutes: neither
            # table had any delete path at all (processed_messages held 86,528 rows
            # on production, sessions were only cleared on restart).
            now_mono = time.monotonic()
            if now_mono - getattr(self, "_last_purge", 0.0) > 300:
                self._last_purge = now_mono
                retired = self.store.purge_old_processed_messages()
                if retired:
                    metrics.inc("zabbix", "processed_messages_purged", retired)
                audits = self.store.purge_audit_older_than(config.AUDIT_RETENTION_DAYS)
                if audits:
                    metrics.inc("zabbix", "audit_purged", audits)
                self.store.purge_expired_sessions()
            health = zabbix.health_check()
            metrics.set("zabbix", api_version=health.version, api_latency_ms=health.latency_ms)
            self.store.set_integration_state(
                "zabbix", health.state, configured=True,
                reachable=health.state in ("AUTHENTICATED", "INCOMPATIBLE"),
                authenticated=health.state == "AUTHENTICATED",
                last_success=now_ms() if health.state == "AUTHENTICATED" else None,
                last_error=json.dumps(health.error, separators=(",", ":")) if health.error else None,
                metrics=metrics.snapshot()["zabbix"],
            )
            return

        any_success = False
        last_err_category = None
        last_err_public = None

        for row in rows:
            row_id = row["id"]
            device_id = row["device_id"]
            try:
                payload = json.loads(row["payload"])
            except Exception as e:
                self.store.move_to_dead_letter([row_id], f"JSON_CORRUPT: {str(e)[:200]}")
                continue

            fields = {v["key"] for v in payload}
            try:
                hostid = zabbix.ensure_device_host({"device_id": device_id, "name": device_id})
                self.store.set_device_zabbix_hostid(device_id, hostid)
                raw_fields = [raw for raw, key in ZABBIX_KEYS.items() if key in fields]
                zabbix.reconcile_items(hostid, raw_fields)
                result = zabbix.sender_submit(payload)
            except ZabbixError as exc:
                metrics.inc("zabbix", "failed", len(payload))
                metrics.set("zabbix", last_error=exc.category)
                last_err_category = exc.category
                last_err_public = exc.public()
                if exc.category in PERMANENT_DELIVERY_ERRORS:
                    dead = self.store.move_to_dead_letter([row_id], f"PERMANENT:{exc.category}")
                    if dead:
                        metrics.inc("zabbix", "dead_lettered", dead)
                elif int(row["attempts"]) + 1 >= config.ZABBIX_RETRY_MAX:
                    dead = self.store.move_to_dead_letter([row_id], exc.category)
                    if dead:
                        metrics.inc("zabbix", "dead_lettered", dead)
                else:
                    self.store.fail_zabbix_queue([row_id], exc.category)
                continue

            if result.ok:
                self.store.delete_zabbix_queue([row_id])
                metrics.inc("zabbix", "values_submitted", len(payload))
                metrics.inc("zabbix", "processed", result.processed)
                metrics.set("zabbix", last_success=now_ms(), last_error=None)
                any_success = True
            else:
                metrics.inc("zabbix", "values_submitted", len(payload))
                metrics.inc("zabbix", "processed", result.processed)
                metrics.inc("zabbix", "failed", result.failed)
                metrics.set("zabbix", last_error="SENDER_PARTIAL_OR_FAILED")
                last_err_category = "SENDER_PARTIAL_OR_FAILED"
                last_err_public = {"category": "SENDER_PARTIAL_OR_FAILED", "message": f"Processed {result.processed}/{result.total}, failed {result.failed}"}
                if int(row["attempts"]) + 1 >= config.ZABBIX_RETRY_MAX:
                    dead = self.store.move_to_dead_letter([row_id], "SENDER_PARTIAL_OR_FAILED")
                    if dead:
                        metrics.inc("zabbix", "dead_lettered", dead)
                else:
                    self.store.fail_zabbix_queue([row_id], "SENDER_PARTIAL_OR_FAILED")
        if any_success and not last_err_category:
            self.store.set_integration_state("zabbix", "CONNECTED", configured=True, reachable=True, authenticated=True, last_success=now_ms(), last_error=None, metrics=metrics.snapshot()["zabbix"])
        elif last_err_category:
            self.store.set_integration_state("zabbix", "DEGRADED", configured=True, reachable=False, last_error=json.dumps(last_err_public or {}, separators=(",", ":")), metrics=metrics.snapshot()["zabbix"])


class StatusAgingWorker:
    """Server-side freshness sweep (RC-3).

    Device rows only change on ingest; without this, a silent device keeps its
    last-ingest ONLINE status forever and the UI never learns it went stale or
    offline. Every interval the sweep recomputes freshness at now, persists the
    transitioned state, and publishes a `status` frame so SSE clients age out
    even with zero new telemetry. Publishes on transition only; quiet otherwise.
    """

    def __init__(self, store: Store, interval: float = 30.0):
        self.store = store
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="status-aging", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep_once()
            except Exception:
                continue

    def sweep_once(self, now_ms: int | None = None) -> int:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        transitioned = 0
        for raw in self.store.list_devices_raw():
            device_id = raw.get("device_id")
            if not device_id:
                continue
            age = compute_freshness(raw.get("last_seen"), now)
            status = compute_status(age, is_clock_skewed(raw.get("last_seen"), raw.get("latest_received_at"), now))
            if raw.get("status") == status and raw.get("data_age") == age:
                continue
            self.store.update_device_state(device_id, status, age, now)
            broker.publish(device_id, {"type": "status", "payload": {
                "deviceId": device_id,
                "status": status,
                "connectivity": raw.get("connectivity") or "unknown",
                "dataAge": age,
                "lastSeen": raw.get("last_seen"),
                "clockSkew": status == "degraded",
                "clockSkewSeconds": clock_skew_seconds(raw.get("last_seen"), raw.get("latest_received_at")),
            }})
            self.store.audit("system", "device.status.transition", f"{device_id} {raw.get('status')}->{status}")
            transitioned += 1
        return transitioned
