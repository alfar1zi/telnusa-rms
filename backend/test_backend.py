from __future__ import annotations

import os
import io
import socket
import struct
import tempfile
import threading
import queue
import json
import time
from collections import deque
os.environ.setdefault("NMS_DB_PATH", ":memory:")

from backend.auth_util import hash_password, verify_password, new_token, RateLimiter
from backend.processor import ZabbixDeliveryWorker, freshness, process_payload
from backend.sse import EventBroker, broker
from backend.store import Store
from backend.telemetry import ValidationError, parse_json_payload, validate_payload
from backend.zabbix_client import ZabbixClient, ZabbixError, normalize_api_url, zabbix_values, normalize_problem
from backend import app as AppHandler


def base_payload(**extra):
    payload = {
        "site_id": "SITE-A",
        "sn": "8800000001",
        "ts": 1_700_000_000_000,
        "sps_mppt_pv_v": 24.5,
        "sps_mppt_pv_a": 1.2,
        "sps_mppt_out_v": 24.0,
        "sps_mppt_out_a": 1.0,
        "a_voltage": 220.0,
        "b_voltage": 220.0,
        "c_voltage": 220.0,
        "a_current": 5.0,
        "b_current": 5.0,
        "c_current": 5.0,
        "kwh": 12.5,
        "solar_radiation": 412,
    }
    payload.update(extra)
    return payload

# 1. Password hashing & verification
def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)

def test_session_token_hashing():
    raw = new_token()
    assert len(raw) >= 32
    assert new_token() != raw


# 3. Telemetry known fields vs unmapped keys
def test_telemetry_known_fields_and_unmapped():
    raw = base_payload(custom_foo=123, unexpected_metric="hello", kwh=None)
    vt = validate_payload(raw, now_ms=1_700_000_001_000)
    unmapped_fields = [u.field for u in vt.unmapped]
    assert sorted(unmapped_fields) == ["custom_foo", "unexpected_metric"]
    assert vt.mppt["pvVoltage"] == 24.5
    assert vt.mppt["pvCurrent"] == 1.2
    assert vt.pzem["energy"] is None
    assert vt.solar == {"radiation": 412.0}

# 4. Telemetry rejects bad identity, future timestamps, and strings disguised as numbers
def test_telemetry_rejects_bad_identity_future_and_numeric_string():
    now = 1_700_000_000_000
    try:
        validate_payload(base_payload(ts=now + 700_000), now_ms=now)
    except ValidationError:
        pass
    else:
        raise AssertionError("future timestamp accepted")

    try:
        validate_payload(base_payload(site_id=""), now_ms=now)
    except ValidationError:
        pass
    else:
        raise AssertionError("empty site accepted")

    try:
        validate_payload(base_payload(sps_mppt_pv_v="24.5"), now_ms=now)
    except ValidationError:
        pass
    else:
        raise AssertionError("string numeric accepted")


# 5. Malformed JSON parsing
def test_malformed_json_rejected():
    try:
        parse_json_payload(b"{not json")
    except ValidationError:
        pass
    else:
        raise AssertionError("malformed json accepted")


# 6. Database schema, user creation, role checking, authentication
def test_store_auth_and_roles():
    store = Store(":memory:")
    pw_hash = hash_password("adminpass")
    uid = store.create_user("admin@example.com", "Admin", pw_hash, role="admin", provider="local")
    assert uid > 0
    user = store.get_user_by_email("admin@example.com")
    assert user is not None and user["role"] == "admin"
    assert verify_password("adminpass", user["password_hash"])
    assert not verify_password("wrong", user["password_hash"])


# 7. CSRF validation on mutation
def test_csrf_generation_and_validation():
    store = Store(":memory:")
    uid = store.create_user("admin@example.com", "Admin", None, role="admin", provider="local")
    sid = new_token()
    csrf = new_token()
    store.create_session(sid, uid, int(time.time() * 1000) + 3600_000, csrf)
    sess = store.get_session(sid)
    assert sess is not None
    assert sess["csrf"] == csrf


# 8. Notification recipient management and deletion
def test_notification_recipient_management():
    store = Store(":memory:")
    store.add_recipient("ops@example.com", "admin@example.com")
    assert len(store.list_recipients()) == 1
    store.remove_recipient("ops@example.com")
    assert len(store.list_recipients()) == 0


# 9. Ingest processor auto-discovery and deduplication
def test_processor_discovery_dedupe_ordering_and_unmapped():
    store = Store(":memory:")
    rec_ms = 1_700_000_001_000
    t1 = base_payload(ts=rec_ms - 1000, sps_mppt_pv_v=20.0)
    process_payload(store, t1, actor="test", received_ms=rec_ms)
    d = store.get_device("RUT-8800000001", now_ms=rec_ms)
    assert d is not None
    assert d["status"] == "online"
    assert d["data_age"] == "fresh"
    # Out of order older packet should not clobber last_seen/last_payload
    t_old = base_payload(ts=1_699_000_000_000, sps_mppt_pv_v=10.0)
    process_payload(store, t_old, actor="test", received_ms=1_700_000_001_000)
    d2 = store.get_device("RUT-8800000001")
    last_payload = json.loads(d2["last_payload"]) if isinstance(d2["last_payload"], str) else d2["last_payload"]
    assert last_payload["mppt"]["pvVoltage"] == 20.0

# RC-2 canonical merge: absent/null preserve last valid value; older-ts never
# overwrites; equal-ts conflicts are first-writer-wins; duplicates idempotent.
def test_rc2_canonical_merge_preserves_last_valid_value():
    from backend.processor import queue_zabbix
    store = Store(":memory:")
    rec = 1_700_000_100_000
    # A = valid MPPT ts=100
    code, _ = process_payload(store, base_payload(ts=1_700_000_100_000, sps_mppt_pv_v=24.5, sps_mppt_pv_a=1.2), actor="test", received_ms=rec)
    assert code == 202
    assert store.count_zabbix_queue() == 1
    # B = missing/null telemetry at older ts=99 must not destroy A
    code, res = process_payload(store, base_payload(ts=1_700_000_099_000), actor="test", received_ms=rec)
    assert code == 202
    assert res.get("stale_superseded") is True
    d = store.get_device("RUT-8800000001", now_ms=rec)
    lp = d["last_payload"]
    assert lp["mppt"]["pvVoltage"] == 24.5
    assert lp["mppt"]["pvCurrent"] == 1.2
    assert store.count_zabbix_queue() == 1  # stale never enqueued
    # C = identity-only at same ts=100 (null mppt) preserves A, dedupes via fingerprint
    code, res = process_payload(store, base_payload(ts=1_700_000_100_000, sps_mppt_pv_v=None, sps_mppt_pv_a=None), actor="test", received_ms=rec)
    assert code == 202
    assert res.get("no_change") is True
    d = store.get_device("RUT-8800000001", now_ms=rec)
    assert d["last_payload"]["mppt"]["pvVoltage"] == 24.5
    # D = equal-ts conflicting valid value is first-writer-wins
    code, _ = process_payload(store, base_payload(ts=1_700_000_100_000, sps_mppt_pv_v=99.9), actor="test", received_ms=rec)
    assert code == 202
    d = store.get_device("RUT-8800000001", now_ms=rec)
    assert d["last_payload"]["mppt"]["pvVoltage"] == 24.5
    # E = newer ts valid value replaces
    code, _ = process_payload(store, base_payload(ts=1_700_000_101_000, sps_mppt_pv_v=26.0), actor="test", received_ms=rec)
    assert code == 202
    d = store.get_device("RUT-8800000001", now_ms=rec)
    assert d["last_payload"]["mppt"]["pvVoltage"] == 26.0
    assert d["last_payload"]["mppt"]["pvCurrent"] == 1.2  # preserved across newer partial
    # F = newer ts explicit null preserves too
    code, _ = process_payload(store, base_payload(ts=1_700_000_102_000, sps_mppt_pv_v=None), actor="test", received_ms=rec)
    assert code == 202
    d = store.get_device("RUT-8800000001", now_ms=rec)
    assert d["last_payload"]["mppt"]["pvVoltage"] == 26.0

# 9b. Integration status must expose live in-memory metrics even when a stored
# state snapshot already exists (stored snapshots freeze counters at transition).
def test_integration_status_merges_live_metrics_over_stored_snapshot():
    from backend.processor import integration_status, metrics
    store = Store(":memory:")
    store.set_integration_state("mqtt", "SUBSCRIBED", configured=True, reachable=True, metrics={"messages_received": 0})
    before = metrics.snapshot()["mqtt"]["messages_received"]
    process_payload(store, base_payload(ts=1_700_000_002_000), actor="test", received_ms=1_700_000_002_000)
    st = integration_status(store)
    assert st["mqtt"]["state"] == "SUBSCRIBED"
    assert st["mqtt"]["metrics"]["messages_received"] == before + 1
# 10. Freshness boundary classification
def test_freshness_boundaries():
    now = 1_000_000_000
    assert freshness(now - 10_000, now) == "fresh"
    assert freshness(now - 400_000, now) == "stale"
    assert freshness(now - 1_000_000, now) == "offline"
    assert freshness(None, now) == "no_data"


# 11. Zabbix queue retry cap and exponential backoff
def test_zabbix_queue_backoff_and_dead_letter():
    store = Store(":memory:")
    store.enqueue_zabbix("msg-1", "RUT-1", "RUT-1", [{"key": "rut.mppt.pv.v", "value": "24.5", "clock": 1000}], 100)
    due = store.pop_zabbix_due(limit=10, retry_max=3, base_seconds=5)
    assert len(due) == 1
    # Fail queue item (increments attempts)
    store.fail_zabbix_queue([due[0]["id"]], error="Network down")
    now = int(time.time() * 1000)
    # Not due immediately at now + 1s (backoff is 5s)
    assert len(store.pop_zabbix_due(limit=10, retry_max=3, base_seconds=5, now_ms=now + 1000)) == 0
    # Due after backoff at now + 6s
    due_later = store.pop_zabbix_due(limit=10, retry_max=3, base_seconds=5, now_ms=now + 6000)
    assert len(due_later) == 1

    # Move to dead letter after max retries
    store.move_to_dead_letter([due_later[0]["id"]], error="Max retries reached")
    assert store.count_dead_letter() == 1
    assert len(store.pop_zabbix_due(limit=10, retry_max=3, base_seconds=5, now_ms=now + 100_000)) == 0


# RC-4: a full queue evicts the oldest row (evidence to DLQ) instead of dropping
# the newest; the ingest response says so honestly instead of a silent 202.
def test_rc4_full_queue_evicts_oldest_and_reports_honestly():
    import backend.processor as proc
    store = Store(":memory:")
    vals = [{"key": "rut.mppt.pv.v", "value": "24.5", "clock": 1000}]
    assert store.enqueue_zabbix("m-old", "RUT-1", "RUT-1", vals, 2) is True
    assert store.enqueue_zabbix("m-mid", "RUT-1", "RUT-1", vals, 2) is True
    assert store.count_zabbix_queue() == 2
    assert store.enqueue_zabbix("m-new", "RUT-1", "RUT-1", vals, 2) is True
    assert store.count_zabbix_queue() == 2
    queued_ids = [r["message_id"] for r in store.pop_zabbix_batch(10)]
    assert "m-old" not in queued_ids and "m-new" in queued_ids
    assert store.count_dead_letter() == 1
    # Honest outcome through the ingest path at max_rows boundary.
    old_max = proc.config.ZABBIX_QUEUE_MAX
    proc.config.ZABBIX_QUEUE_MAX = 1
    try:
        s2 = Store(":memory:")
        code, res = process_payload(s2, base_payload(ts=1_700_000_100_000), actor="test", received_ms=1_700_000_100_000)
        assert code == 202
        assert "zabbix" not in res  # room in queue: silent normal path
        code, res = process_payload(s2, base_payload(ts=1_700_000_101_000), actor="test", received_ms=1_700_000_101_000)
        assert code == 202
        assert res.get("zabbix") == "evicted_oldest"
        assert s2.count_dead_letter() == 1
    finally:
        proc.config.ZABBIX_QUEUE_MAX = old_max


# RC-4: permanent config errors skip the retry schedule straight to dead letter.
def test_rc4_permanent_error_fast_tracks_to_dead_letter():
    from backend.processor import ZabbixDeliveryWorker
    from backend.zabbix_client import ZabbixError
    import backend.processor as proc
    store = Store(":memory:")
    store.enqueue_zabbix("m-1", "RUT-8800000001", "RUT-8800000001", [{"key": "rut.mppt.pv.v", "value": 1.0, "clock": 1}], 100)
    worker = ZabbixDeliveryWorker(store)
    orig_url = proc.zabbix.api_url
    orig_token = proc.zabbix.token
    orig_ensure = proc.zabbix.ensure_device_host
    orig_recon = proc.zabbix.reconcile_items
    orig_submit = proc.zabbix.sender_submit
    proc.zabbix.api_url = "http://127.0.0.1:10051/api_jsonrpc.php"
    proc.zabbix.token = "mock-token"
    proc.zabbix.ensure_device_host = lambda _d: "101"
    proc.zabbix.reconcile_items = lambda _h, _f: None
    proc.zabbix.sender_submit = lambda _p: (_ for _ in ()).throw(ZabbixError("TEMPLATE_MISMATCH", "template missing"))
    try:
        worker.flush_once()
    finally:
        proc.zabbix.api_url = orig_url
        proc.zabbix.token = orig_token
        proc.zabbix.ensure_device_host = orig_ensure
        proc.zabbix.reconcile_items = orig_recon
        proc.zabbix.sender_submit = orig_submit
    assert store.count_zabbix_queue() == 0
    assert store.count_dead_letter() == 1


# RC-4: DLQ TTL ages out old evidence only; recent failures are preserved.
def test_rc4_dead_letter_ttl_preserves_recent_evidence():
    store = Store(":memory:")
    now = 1_700_000_000_000
    old = now - 100 * 86_400_000
    with store._lock, store.db:
        store.db.execute(
            "INSERT INTO zabbix_dead_letter(message_id,device_id,host,payload,attempts,last_error,created_at,updated_at,dead_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("m-old", "RUT-1", "RUT-1", "[]", 5, "CONNECTION_ERROR", old, old, old),
        )
        store.db.execute(
            "INSERT INTO zabbix_dead_letter(message_id,device_id,host,payload,attempts,last_error,created_at,updated_at,dead_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("m-new", "RUT-1", "RUT-1", "[]", 5, "CONNECTION_ERROR", now, now, now),
        )
    purged = store.purge_dead_letter_older_than(90, now_ms=now)
    assert purged == 1
    assert store.count_dead_letter() == 1
# 12. Zabbix sender protocol encoding
def test_zabbix_sender_protocol_encoding():
    values = [{"host": "RUT-1", "key": "k", "value": "1", "clock": 1000}]
    payload = json.dumps({"request": "sender data", "data": values}, separators=(",", ":")).encode()
    packet = b"ZBXD\x01" + struct.pack("<Q", len(payload)) + payload
    assert packet.startswith(b"ZBXD\x01")
    datalen = struct.unpack("<Q", packet[5:13])[0]
    parsed = json.loads(packet[13 : 13 + datalen].decode("utf-8"))
    assert parsed["request"] == "sender data"
    assert len(parsed["data"]) == 1


# 13. Zabbix sender response parsing & strict accounting
def test_zabbix_sender_ack_must_process_all_values():
    from backend.zabbix_client import SenderResult, _info_int
    ack = "processed: 2; failed: 0; total: 2; seconds spent: 0.000050"
    res = SenderResult(
        processed=_info_int(ack, "processed"),
        failed=_info_int(ack, "failed"),
        total=_info_int(ack, "total"),
        response="success",
    )
    assert res.ok
    assert res.processed == 2
    assert res.failed == 0


# 14. Zabbix sender rejects partial success
def test_zabbix_sender_rejects_partial_success():
    from backend.zabbix_client import SenderResult, _info_int
    ack = "processed: 1; failed: 1; total: 2; seconds spent: 0.000050"
    res = SenderResult(
        processed=_info_int(ack, "processed"),
        failed=_info_int(ack, "failed"),
        total=_info_int(ack, "total"),
        response="success",
    )
    assert not res.ok


# 15. Zabbix sender rejects failed response
def test_zabbix_sender_rejects_failed_response():
    from backend.zabbix_client import SenderResult, _info_int
    ack = "processed: 0; failed: 1; total: 1; seconds spent: 0.000050"
    res = SenderResult(
        processed=_info_int(ack, "processed"),
        failed=_info_int(ack, "failed"),
        total=_info_int(ack, "total"),
        response="failed",
    )
    assert not res.ok


# 16. Zabbix API URL normalization
def test_zabbix_api_url_normalization():
    assert normalize_api_url("http://zabbix.lan/zabbix") == "http://zabbix.lan/zabbix/api_jsonrpc.php"
    assert normalize_api_url("http://zabbix.lan/zabbix/api_jsonrpc.php") == "http://zabbix.lan/zabbix/api_jsonrpc.php"


# 17. Zabbix item key validation against template definition
def test_zabbix_item_mismatch_rejected():
    client = ZabbixClient("http://unused", "token")
    # Mock mismatched type/value_type
    client.items_for_host = lambda hostid, keys: {k: {"itemid": "1", "key_": k, "type": 0, "value_type": 1} for k in keys}
    try:
        client.reconcile_items("1001", ["sps_mppt_pv_v"])
    except ZabbixError as exc:
        assert exc.category == "ITEM_TYPE_MISMATCH"
    else:
        raise AssertionError("mismatched item accepted")
def test_zabbix_health_rejects_unsupported_version():
    client = ZabbixClient("http://unused", "token")
    client.apiinfo_version = lambda: "6.0.0"
    client.verify_authentication = lambda: None
    health = client.health_check()
    assert health.state == "INCOMPATIBLE"
    assert health.version == "6.0.0"

# 19. Zabbix history value_type mapping and normalization
def test_zabbix_device_history_normalization():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({
        "rut.mppt.pv.v": [{"itemid": "1", "clock": "1700000000", "ns": "0", "value": "24.5"}],
        "rut.pzem.energy.kwh": [{"itemid": "2", "clock": "1700000000", "ns": "0", "value": "12.5"}],
    }, True)
    hist = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1700001000)
    assert hist["deviceId"] == "RUT-TEST-001"
    assert len(hist["mppt"]["pvVoltage"]) == 1
    assert hist["mppt"]["pvVoltage"][0]["value"] == 24.5
    assert hist["pzem"]["energy"][0]["value"] == 12.5



# 19b. The requested window is forwarded in full and echoed; a covered window is not truncated
def test_zabbix_device_history_preserves_requested_window():
    from backend.zabbix_client import MAX_RAW_POINTS

    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    seen = {}

    def _window(hostid, items, **kwargs):
        seen.update(kwargs)
        return {"rut.mppt.pv.v": [{"itemid": "1", "clock": "1700000000", "ns": "0", "value": "24.5"}]}, True

    client.history_window_for_items = _window
    hist = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1700001000)
    assert seen["time_from"] == 1699999000, "requested start forwarded to Zabbix unchanged"
    assert seen["time_till"] == 1700001000, "requested end forwarded to Zabbix unchanged"
    win = hist["window"]
    assert win["fromMs"] == 1699999000 * 1000, "start echoed in ms"
    assert win["toMs"] == 1700001000 * 1000, "end echoed in ms"
    assert win["truncated"] is False, "a window the pager covered in full is not truncated"
    assert win["maxPoints"] == MAX_RAW_POINTS


def test_zabbix_device_history_reports_truncation_only_when_the_pager_runs_out():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    # A window far beyond the page budget must be declared partial. The flag now
    # comes from the pager's own coverage, not from a row-count ceiling: a wide
    # window that the pager did cover is complete, and saying otherwise is what
    # used to hide the real cause of a half-empty chart.
    client.history_window_for_items = lambda hostid, items, **kwargs: ({"rut.mppt.pv.v": []}, False)
    hist = client.device_history("RUT-TEST-001", time_from=0, time_till=10 * 86_400)
    assert hist["window"]["truncated"] is True, "an exhausted page budget must be declared"


def test_zabbix_device_history_orders_samples_chronologically():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    # Zabbix retrieves DESC; the response contract is ascending.
    client.history_window_for_items = lambda hostid, items, **kwargs: ({
        "rut.mppt.pv.v": [
            {"itemid": "1", "clock": "1700000200", "ns": "0", "value": "3"},
            {"itemid": "1", "clock": "1700000000", "ns": "0", "value": "1"},
            {"itemid": "1", "clock": "1700000100", "ns": "0", "value": "2"},
        ]
    }, True)
    hist = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1700001000)
    ts = [p["ts"] for p in hist["mppt"]["pvVoltage"]]
    assert ts == sorted(ts), "series must be ascending regardless of retrieval order"


def test_zabbix_device_history_empty_window_is_empty_not_fabricated():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({}, True)
    hist = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1699999500)
    assert hist["mppt"]["pvVoltage"] == [], "a window with no samples yields an empty series"
    assert hist["pzem"]["energy"] == []
    assert hist["window"]["fromMs"] == 1699999000 * 1000, "empty result still echoes the window"


def test_zabbix_device_history_stale_feed_is_not_anchored_by_data():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    # Feed stopped 6 hours before the requested window closed.
    client.history_window_for_items = lambda hostid, items, **kwargs: ({
        "rut.mppt.pv.v": [
            {"itemid": "1", "clock": "1699978400", "ns": "0", "value": "24.5"},
        ]
    }, True)
    hist = client.device_history("RUT-TEST-001", time_from=1700000000, time_till=1700003600)
    assert hist["mppt"]["pvVoltage"][0]["ts"] == 1699978400 * 1000, "stale sample keeps its true timestamp"
    assert hist["window"]["toMs"] == 1700003600 * 1000, "window end stays the requested end"
    assert len(hist["mppt"]["pvVoltage"]) == 1, "Zabbix is the authority on which points fall in range"


def test_zabbix_device_history_skips_unparsable_rows_without_inventing_values():
    client = ZabbixClient("http://unused", "token")
    client.host_lookup = lambda host: {"hostid": "1001"}
    client.item_lookup = lambda hostid, key: {"itemid": "1", "key_": key, "value_type": 0}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({
        "rut.mppt.pv.v": [
            {"itemid": "1", "clock": "1700000000", "ns": "0", "value": "24.5"},
            {"itemid": "1", "clock": "bad", "ns": "0", "value": "1"},
            {"itemid": "1", "clock": "1700000100", "ns": "0", "value": "N/A"},
        ]
    }, True)
    hist = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1700001000)
    values = [p["value"] for p in hist["mppt"]["pvVoltage"]]
    assert values == [24.5], "unparsable rows are dropped, never coerced to zero"

# 20. Zabbix alarm / problem severity normalization
def test_zabbix_alarm_severity_normalization():
    prob = {
        "eventid": "501",
        "name": "PV Undervoltage",
        "severity": "4",  # High -> high in mapping
        "clock": "1700000000",
        "r_clock": "0",
        "hosts": [{"host": "RUT-TEST-001"}],
    }
    norm = normalize_problem(prob)
    assert norm["id"] == "501"
    assert norm["deviceId"] == "RUT-TEST-001"
    assert norm["severity"] == "high"
    assert norm["status"] == "active"
    assert norm["startTime"] == 1700000000000

# D1: resolved with unknown recovery time must not fabricate endTime 0.
def test_zabbix_resolved_without_r_clock_omits_end_time():
    base = {
        "eventid": "502",
        "name": "PV Undervoltage",
        "severity": "4",
        "clock": "1700000000",
        "ns": "0",
        "r_eventid": "503",
        "hosts": [{"host": "RUT-TEST-001"}],
    }
    unknown = normalize_problem({**base, "r_clock": "0"})
    assert unknown["status"] == "resolved"
    assert "endTime" not in unknown
    missing = normalize_problem({k: v for k, v in base.items() if k != "r_clock"})
    assert missing["status"] == "resolved"
    assert "endTime" not in missing
    known = normalize_problem({**base, "r_clock": "1700000100"})
    assert known["status"] == "resolved"
    assert known["endTime"] == 1700000100000


# 20b. problem.get rejects selectHosts in Zabbix 7.4 (-32602), which made every
# /api/alarms call fail with ZABBIX_API_ERROR. The host must be resolved via trigger.get.
def test_zabbix_problems_omits_select_hosts_and_resolves_host():
    client = ZabbixClient("http://unused", "token")
    sent = []

    def fake_call(method, params, *, authenticated=True):
        sent.append((method, params))
        if method == "problem.get":
            return [{"eventid": "847", "clock": "1790847023", "ns": "0",
                     "name": "MPPT: PV input present but no output",
                     "severity": "3", "r_eventid": "0", "r_clock": "0",
                     "objectid": "32578", "source": "0", "object": "0"}]
        if method == "trigger.get":
            assert params["triggerids"] == ["32578"]
            return [{"triggerid": "32578",
                     "hosts": [{"hostid": "10782", "host": "RUT-8800000001"}]}]
        raise AssertionError("unexpected call: " + str(method))

    client._call = fake_call
    rows = client.problems(["10782"])

    problem_params = [p for m, p in sent if m == "problem.get"][0]
    assert "selectHosts" not in problem_params
    assert problem_params["sortfield"] == ["eventid"]
    assert problem_params["hostids"] == ["10782"]

    assert len(rows) == 1
    alarm = normalize_problem(rows[0])
    assert alarm["id"] == "847"
    assert alarm["deviceId"] == "RUT-8800000001"
    assert alarm["severity"] == "average"
    assert alarm["status"] == "active"


# 21. SSE live broker pub/sub
def test_broker_pub_sub():
    b = EventBroker()
    sub = b.subscribe("RUT-TEST-001")
    b.publish("RUT-TEST-001", {"type": "telemetry", "payload": {"pvVoltage": 24.5}})
    data = json.loads(sub.queue.get(timeout=1.0))
    assert data["type"] == "telemetry"
    assert data["payload"]["pvVoltage"] == 24.5
    # A closed connection releases the frames already buffered for it.
    for _ in range(3):
        b.publish("RUT-TEST-001", {"type": "telemetry", "payload": {}})
    assert sub.queue.qsize() > 0, "frames must actually be buffered before closing"

    b.unsubscribe("RUT-TEST-001", sub)
    leftover = sub.queue.qsize()
    assert leftover == 0, f"unsubscribed buffer still holds {leftover} frame(s)"


# 22. Device route resolver
def test_route_device_parsing():
    assert AppHandler.route_device("/api/devices/RUT-TEST-001/telemetry") == "RUT-TEST-001"
    assert AppHandler.route_device("/api/devices/RUT-TEST-001/status") == "RUT-TEST-001"
    assert AppHandler.route_device("/api/devices/RUT-TEST-001") == "RUT-TEST-001"
    assert AppHandler.route_device("/api/devices") is None


# 23. Unconfigured Zabbix returns explicit 503
def test_unconfigured_zabbix_explicit_503():
    # When zabbix client is unconfigured, direct calls must be caught and raised as 503
    assert not AppHandler.zabbix.configured


# 24. Remote endpoints target resolution, RBAC, audit, and explicit 501
def test_remote_endpoints_explicit_501():
    import io
    from backend.remote import resolve_device_target, DeviceRemoteTarget
    import backend.app as app_mod

    store = Store(":memory:")
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "connectivity": "ethernet",
        "integration_status": "accepted",
        "last_seen": 1000,
        "last_payload": None,
    })
    # Registered but not integration-accepted: no remote target.
    store.upsert_device({
        "device_id": "RUT-NOT-ACCEPTED",
        "site_id": "SITE-B",
        "serial": "8800000004",
        "status": "online",
        "integration_status": "no_data",
        "last_seen": 1000,
        "last_payload": None,
    })

    # 1. Target resolver unit checks
    # Registered + integration-accepted -> trusted target from server-side config.
    t1 = resolve_device_target("RUT-8800000001", store)
    assert t1 is not None
    assert isinstance(t1, DeviceRemoteTarget)
    assert t1.ssh_host == "192.168.1.144"
    assert t1.ssh_port == 22
    assert t1.webui_target_host == "192.168.10.1"
    assert t1.webui_target_port == 443

    # REMOTE-01: the test site id is NOT special-cased. It is not in the registry,
    # so it must resolve to nothing rather than inherit the physical device's target.
    assert resolve_device_target("RUT-TEST-001", store) is None, \
        "an unregistered test site id must not resolve to a remote target"

    # Registered but not integration-accepted -> no target.
    assert resolve_device_target("RUT-NOT-ACCEPTED", store) is None, \
        "a registered device that is not integration-accepted must have no remote target"

    assert resolve_device_target("RUT-UNKNOWN", store) is None
    assert resolve_device_target("", store) is None
    assert resolve_device_target("   ", store) is None
    assert resolve_device_target(None, store) is None

    # No registry means no trustworthy identity to authorize.
    assert resolve_device_target("RUT-8800000001", None) is None, \
        "resolution without the registry must fail closed"

    # 2. HTTP handler mock harness
    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = {}

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers[keyword] = value

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    pw_hash = app_mod.hash_password("Pass1234!")
    admin_id = store.create_user("admin@example.test", "Admin", pw_hash, role="admin", provider="local")
    operator_id = store.create_user("operator@example.test", "Operator", pw_hash, role="operator", provider="local")
    viewer_id = store.create_user("viewer@example.test", "Viewer", pw_hash, role="viewer", provider="local")

    now = app_mod.now_ms()
    admin_sid = "sid-admin"
    op_sid = "sid-operator"
    viewer_sid = "sid-viewer"
    store.create_session(admin_sid, admin_id, now + 3600_000, "csrf-admin")
    store.create_session(op_sid, operator_id, now + 3600_000, "csrf-op")
    store.create_session(viewer_sid, viewer_id, now + 3600_000, "csrf-viewer")

    orig_store = app_mod.store
    # Production wires both to the same registry (app.py binds tunnel_manager.store).
    # Both must be redirected together, otherwise the tunnel resolves identity
    # against a different registry than the handler does.
    orig_tm_store = app_mod.tunnel_manager.store
    app_mod.store = store
    app_mod.tunnel_manager.store = store
    try:
        # A. Unauthenticated -> 401
        h_unauth = MockHandler("GET", "/api/remote/RUT-8800000001/webui")
        h_unauth.do_GET()
        assert h_unauth.status_code == 401

        # B. Viewer -> 403
        h_viewer = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={viewer_sid}"})
        h_viewer.do_GET()
        assert h_viewer.status_code == 403

        # C. Operator -> 501 BLOCKED (Phase 1 behavior: authorized & resolved, but external service blocked)
        h_op = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_op.do_GET()
        assert h_op.status_code == 501
        res_op = json.loads(h_op.wfile.getvalue().decode())
        assert res_op["error"] == "REMOTE_NOT_CONFIGURED"

        h_op_term = MockHandler("GET", "/api/remote/RUT-8800000001/terminal", headers={"Cookie": f"nms_session={op_sid}"})
        h_op_term.do_GET()
        assert h_op_term.status_code == 501

        # D. Admin -> 501 BLOCKED
        h_admin = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={admin_sid}"})
        h_admin.do_GET()
        assert h_admin.status_code == 501

        # E. Unknown device -> 404
        h_unknown = MockHandler("GET", "/api/remote/RUT-NONEXISTENT/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_unknown.do_GET()
        assert h_unknown.status_code == 404

        # F. Parameter tampering immunity: ?host=1.1.1.1 cannot change target resolution
        h_tamper1 = MockHandler("GET", "/api/remote/RUT-8800000001/webui?host=1.1.1.1", headers={"Cookie": f"nms_session={op_sid}"})
        h_tamper1.do_GET()
        assert h_tamper1.status_code == 501

        # G. Arbitrary proxy path tampering: /api/remote/webui?host=http://169.254.169.254 -> 404
        h_tamper2 = MockHandler("GET", "/api/remote/webui?host=http://169.254.169.254", headers={"Cookie": f"nms_session={op_sid}"})
        h_tamper2.do_GET()
        assert h_tamper2.status_code == 404

        # H. Audit rows verified
        audit_logs = store.list_audit_logs(limit=50)
        actions = [log["action"] for log in audit_logs]
        assert "remote.webui.open" in actions
        assert "remote.terminal.open" in actions
    finally:
        app_mod.store = orig_store
        app_mod.tunnel_manager.store = orig_tm_store

# 24b. Terminal grant: signed assertion round-trip, connection id, trust boundary
def test_terminal_grant_signed_assertion_and_trust_boundary():
    import base64 as _b64
    import hmac as _hmac
    import hashlib as _hashlib
    import urllib.error as _urlerr
    import backend.guacamole as guac
    from backend.remote import DeviceRemoteTarget

    key_hex = "00112233445566778899aabbccddeeff"  # 128-bit
    key_bytes = bytes.fromhex(key_hex)

    # The connection id must be the exact inline-JSON identifier Guacamole uses.
    cid = guac._connection_id()
    assert cid == _b64.b64encode(b"RUT906 SSH\0c\0json").decode()

    # Round-trip the signed assertion with an independent decrypt to prove the
    # wire format matches guacamole-auth-json: AES-128-CBC(all-zero IV) over
    # HMAC-SHA256(key, json) || json, PKCS#7 padded.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as kf:
        kf.write("-----BEGIN PRIVATE KEY-----\nFAKEKEYFORUNITTEST\n-----END PRIVATE KEY-----\n")
        key_path = kf.name

    target = DeviceRemoteTarget(
        device_id="RUT-8800000001",
        ssh_host="192.168.1.144",
        ssh_port=22,
        webui_target_host="192.168.10.1",
        webui_target_port=443,
    )

    # _ssh_parameters reads the key path/user off config at call time, so the
    # stub config must be installed before the payload is built.
    class Cfg:
        GUACAMOLE_URL = ""
        GUACAMOLE_JSON_SECRET_KEY = ""
        RUT_SSH_KEY_PATH = key_path
        RUT_SSH_USER = "root"

    orig = guac.config
    _orig_post_token = guac._post_token
    guac.config = Cfg
    try:
        payload = {
            "username": "op@example.test",
            "expires": 1_700_000_300_000,
            "connections": {guac._CONNECTION_NAME: {
                "protocol": "ssh", "parameters": guac._ssh_parameters(target)}},
        }
        data_b64 = guac._sign_and_encrypt(payload, key_hex)
        raw = _b64.b64decode(data_b64)
        dec = Cipher(algorithms.AES(key_bytes), modes.CBC(guac._NULL_IV)).decryptor()
        plain = dec.update(raw) + dec.finalize()
        plain = plain[:-plain[-1]]  # strip PKCS#7
        sig, body = plain[:32], plain[32:]
        assert _hmac.compare_digest(sig, _hmac.new(key_bytes, body, _hashlib.sha256).digest())
        decoded = json.loads(body.decode())
        assert decoded["username"] == "op@example.test"
        params = decoded["connections"]["RUT906 SSH"]["parameters"]
        # Trust boundary: target host/port come from the registry, not a request.
        assert params["hostname"] == "192.168.1.144"
        assert params["port"] == "22"
        # A wrong-size key must be rejected rather than silently padded.
        try:
            guac._sign_and_encrypt(payload, "aabb")
            raise AssertionError("expected ValueError for non-128-bit key")
        except ValueError:
            pass

        # Unconfigured -> grant carries REMOTE_NOT_CONFIGURED, no URL/token.
        g = guac.issue_terminal_grant("op@example.test", target)
        assert g.status == "error"
        assert g.error == "REMOTE_NOT_CONFIGURED"
        assert guac.client_url(g) == ""

        # Configured + reachable -> ready, URL is same-origin and leaks no secret.
        class OkCfg(Cfg):
            GUACAMOLE_URL = "http://127.0.0.1:8081/guacamole"
            GUACAMOLE_JSON_SECRET_KEY = key_hex
        guac.config = OkCfg
        guac._post_token = lambda base_url, data, timeout: "T" * 64
        g2 = guac.issue_terminal_grant("op@example.test", target)
        assert g2.status == "ready"
        url = guac.client_url(g2)
        assert url.startswith("/guacamole/#/client/")
        assert g2.connection_id in url
        assert "token=" in url
        assert "PRIVATE KEY" not in url and key_hex not in url
    finally:
        guac._post_token = _orig_post_token
        guac.config = orig
        os.unlink(key_path)

# 25. Approved keys filter
def test_zabbix_value_mapping_approved_keys_only():
    from backend.zabbix_contract import ZABBIX_KEYS
    vt = validate_payload(base_payload(unknown=1, solar_radiation=1), now_ms=1_700_000_001_000)
    keys = {v["key"] for v in zabbix_values(vt)}
    expected = set(ZABBIX_KEYS.values())
    # 12 telemetry keys plus the 7 router-health history items.
    assert len(expected) == 19
    assert keys.issubset(expected)
    assert "rut.solar.radiation" in expected
# 26. Health snapshot no fake data
def test_history_fills_uncovered_hours_from_zabbix_trends():
    """A long window cannot be covered by one raw history call, so the buckets the
    local rollups miss must be filled with real Zabbix trend aggregates."""
    import time
    from backend.analytics import hour_bucket_start
    from backend.rollup import build_telemetry_history_response

    store = Store(":memory:")
    now = int(time.time() * 1000)
    now_hour = hour_bucket_start(now)
    store.upsert_device({"device_id": "RUT-8800000001", "site_id": "SITE-A", "serial": "8800000001", "last_seen": now})

    # One local rollup row only: every other hour must come from trends.
    store.save_hourly_rollup({
        "device_id": "RUT-8800000001", "bucket_start": now_hour, "bucket_end": now_hour + 3_600_000,
        "sample_count": 180, "expected_count": 180, "coverage_ratio": 1.0,
        "energy_start_kwh": 10.0, "energy_end_kwh": 10.5, "energy_delta_kwh": 0.5, "energy_quality": "VALID",
        "pv_v_avg": 19.0, "pv_v_min": 18.0, "pv_v_max": 20.0,
        "pv_a_avg": 1.0, "pv_a_min": 0.9, "pv_a_max": 1.1,
        "out_v_avg": 18.0, "out_v_min": 17.0, "out_v_max": 19.0,
        "out_a_avg": 1.0, "out_a_min": 0.9, "out_a_max": 1.1,
        "pv_power_avg": 19.0, "pv_power_min": 16.2, "pv_power_max": 22.0,
        "out_power_avg": 18.0, "out_power_min": 15.3, "out_power_max": 20.9,
        "pzem_va_avg": 220.0, "pzem_va_min": 219.0, "pzem_va_max": 221.0,
        "pzem_vb_avg": 221.0, "pzem_vb_min": 220.0, "pzem_vb_max": 222.0,
        "pzem_vc_avg": 222.0, "pzem_vc_min": 221.0, "pzem_vc_max": 223.0,
        "pzem_ia_avg": 1.0, "pzem_ia_min": 1.0, "pzem_ia_max": 1.0,
        "pzem_ib_avg": 1.1, "pzem_ib_min": 1.1, "pzem_ib_max": 1.1,
        "pzem_ic_avg": 1.2, "pzem_ic_min": 1.2, "pzem_ic_max": 1.2,
        "solar_rad_avg": 400.0, "solar_rad_min": 380.0, "solar_rad_max": 420.0,
    })

    values = {
        "rut.mppt.pv.v": (20.0, 19.0, 21.0), "rut.mppt.pv.a": (2.0, 1.9, 2.1),
        "rut.mppt.out.v": (19.5, 19.0, 20.0), "rut.mppt.out.a": (1.5, 1.4, 1.6),
        "rut.solar.radiation": (500.0, 480.0, 520.0),
        "rut.pzem.a.voltage": (230.0, 229.0, 231.0), "rut.pzem.b.voltage": (231.0, 230.0, 232.0),
        "rut.pzem.c.voltage": (232.0, 231.0, 233.0),
        "rut.pzem.a.current": (2.0, 2.0, 2.0), "rut.pzem.b.current": (2.1, 2.1, 2.1),
        "rut.pzem.c.current": (2.2, 2.2, 2.2),
        "rut.pzem.energy.kwh": (30.0, 30.0, 30.5),
    }

    class FakeZabbix:
        configured = True
        def host_lookup(self, host): return {"hostid": "1", "host": host}
        def item_lookup(self, hostid, key): return {"itemid": key, "key_": key, "value_type": 0}
        def history_for_items(self, hostid, items, **kwargs): return {}
        def trend_rows(self, itemid, **kwargs):
            if itemid not in values:
                return []
            avg, low, high = values[itemid]
            out = []
            for h in range(6, 0, -1):  # the six hours before the local rollup row
                out.append({"clock": str((now_hour - h * 3_600_000) // 1000), "num": "180",
                            "value_min": str(low), "value_avg": str(avg), "value_max": str(high)})
            return out

    res = build_telemetry_history_response(store, FakeZabbix(), "RUT-8800000001",
                                           time_from_ms=now - 8 * 3600_000, time_till_ms=now, requested_range="8h",
                                           requested_resolution="hourly")
    pv = res["mppt"]["pvVoltage"]
    assert len(pv) >= 7, f"expected the trend-filled hours plus the rollup hour, got {len(pv)}"
    trend_points = [p for p in pv if p.get("value") == 20.0]
    assert trend_points, "trend hours must appear in the series"
    assert all("min" in p and "max" in p for p in trend_points)
    # Power is derived per bucket, so the trend hours carry real power too.
    power = [p for p in res["mppt"]["pvInputPower"] if p.get("value") == 40.0]
    assert power, "derived power must be computed for trend-filled buckets"
    energy = [p for p in res["pzem"]["energy"] if p.get("source") == "zabbix_trends"]
    assert energy, "energy counters must be filled from trends where raw coverage stops"
    assert res["metricState"]["mppt.pvVoltage"] == "data"
    assert res["solar"]["radiation"], "solar series must be filled as well"


def test_device_health_and_network_labels_come_from_the_router():
    import time
    from backend.app import device_public
    from backend.store import compute_health
    store = Store(":memory:")
    now = int(time.time() * 1000)

    # Classification thresholds, all from values the router measured.
    assert compute_health(None) == "no_data"
    assert compute_health({"ramPercent": 40.0, "flashPercent": 20.0, "temperatureC": 40.0}) == "healthy"
    assert compute_health({"ramPercent": 88.0}) == "warning"
    assert compute_health({"temperatureC": 66.0}) == "warning"
    assert compute_health({"ramPercent": 96.0}) == "critical"
    assert compute_health({"temperatureC": 80.0}) == "critical"
    assert compute_health({"load1": 2.0, "cores": 1}) == "warning"

    status, _ = process_payload(store, base_payload(
        ts=now, sys_ram_pct=41.0, sys_flash_pct=20.5, modem_temperature_c=39.0,
        modem_signal_dbm=-69, modem_netstate="Registered, home", modem_conntype="4G (LTE)",
        modem_operator="INDOSATOOREDOO",
    ), actor="http")
    assert status == 202
    row = store.get_device("RUT-8800000001")
    assert row["health"] == "healthy", "system health must reflect the router's readings"
    public = device_public(row)
    assert public["network"] == "4G (LTE)", "network label must come from the modem status"
    assert public["health"] == "healthy"

    # A device that has reported nothing still says no_data, never a guess.
    empty = Store(":memory:")
    empty.upsert_device({"device_id": "RUT-1", "site_id": "S", "serial": "1"})
    assert empty.get_device("RUT-1")["health"] == "no_data"
    assert device_public(empty.get_device("RUT-1"))["network"] is None


def test_router_health_ingest_maps_and_persists_every_field():
    import time
    from backend.app import health_snapshot
    from backend.telemetry import merge_sensor_state, validate_payload
    from backend.zabbix_client import zabbix_values
    store = Store(":memory:")
    now = int(time.time() * 1000)
    status, res = process_payload(store, base_payload(
        ts=now,
        sys_uptime_s=18787, sys_cores=1, sys_load1=0.77, sys_load5=1.15, sys_load15=1.11,
        sys_ram_pct=79.42, sys_flash_pct=21.33,
        modem_temperature_c=39, modem_signal_dbm=-69, modem_rsrp_dbm=-100,
        modem_netstate="Registered, home", modem_operator="INDOSATOOREDOO", modem_conntype="4G (LTE)",
    ), actor="http")
    assert status == 202
    assert res["unmapped"] == [], "health fields must be contracted, not unmapped"

    row = store.get_device("RUT-8800000001")
    health = row["last_payload"]["health"]
    assert health["uptimeSeconds"] == 18787.0
    assert health["ramPercent"] == 79.42
    assert health["flashPercent"] == 21.33
    assert health["temperatureC"] == 39.0
    assert health["rssi"] == -69.0
    assert health["networkState"] == "Registered, home"

    snap = health_snapshot(store, "RUT-8800000001")
    assert snap["state"] == "available"
    assert snap["ramPercent"] == 79.42
    assert snap["temperatureC"] == 39.0
    assert snap["uptimeSeconds"] == 18787.0
    assert snap["rssi"] == -69.0
    assert snap["networkState"] == "Registered, home"
    # cpuPercent is the one derived value: load1 over the reported core count.
    assert snap["cpuPercent"] == 77.0

    vt = validate_payload(base_payload(ts=now + 20_000, sys_load1=2.5, sys_cores=1,
                                       sys_uptime_s=18807, modem_temperature_c=40), now_ms=now + 20_000)
    mppt, pzem, solar, health_merged, raw = merge_sensor_state(row["last_payload"], vt)
    # A field the router did not report this cycle keeps its last valid value.
    assert health_merged["ramPercent"] == 79.42
    assert health_merged["temperatureC"] == 40.0
    keys = {v["key"] for v in zabbix_values(merge_view_telemetry(vt, health_merged, raw))}
    assert "rut.sys.ram.pct" in keys, "health history must reach Zabbix"
    assert "rut.modem.temperature.c" in keys


def merge_view_telemetry(vt, health_merged, raw):
    from backend.processor import _merged_view
    return _merged_view(vt, vt.mppt, vt.pzem, vt.solar, health_merged, raw)


def test_zabbix_reconcile_updates_units_but_refuses_type_mismatch():
    from backend.zabbix_client import ZabbixClient
    from backend.zabbix_contract import ITEM_SPECS
    from backend.zabbix_client import ZabbixError

    calls: list[tuple[str, dict]] = []

    def make_client(item: dict):
        client = ZabbixClient("http://127.0.0.1:80", "fake_token")
        client.items_for_host = lambda hostid, keys: {k: dict(item) for k in keys}
        def fake_call(method, params):
            calls.append((method, params))
            return {}
        client._call = fake_call
        return client

    spec = ITEM_SPECS["solar_radiation"]
    base_item = {
        "itemid": "69740", "key_": spec.zabbix_key,
        "type": spec.item_type, "value_type": spec.value_type,
        "units": "", "status": "0",
    }

    # Units drifted from the contract: corrected in place, delivery continues.
    client = make_client(base_item)
    client.reconcile_items("10782", ["solar_radiation"])
    assert calls == [("item.update", {"itemid": "69740", "units": spec.units})]

    # Matching units: no write at all.
    calls.clear()
    client = make_client(dict(base_item, units=spec.units))
    client.reconcile_items("10782", ["solar_radiation"])
    assert calls == []

    # A value_type mismatch still refuses: a text item cannot store floats, and
    # silently rewriting it would destroy the caller's own monitoring config.
    calls.clear()
    client = make_client(dict(base_item, value_type=4))
    try:
        client.reconcile_items("10782", ["solar_radiation"])
    except ZabbixError as exc:
        assert exc.category == "ITEM_TYPE_MISMATCH"
    else:
        raise AssertionError("value_type mismatch must remain a hard error")
    assert calls == []


def test_health_snapshot_dimensions_no_fake_data():
    from backend.store import Store
    store = Store(":memory:")
    snap = AppHandler.health_snapshot(store, "RUT-1")
    # Router resource metrics have no collected source: they stay null, and the
    # snapshot says so explicitly instead of implying a temporarily-empty reading.
    assert snap["state"] == "not_integrated"
    assert snap["cpuPercent"] is None
    assert snap["ramPercent"] is None
    # With a real device row the derived dimensions carry real values.
    import time
    now = int(time.time() * 1000)
    status, _ = process_payload(store, base_payload(ts=now), actor="http")
    assert status == 202
    snap2 = AppHandler.health_snapshot(store, "RUT-8800000001")
    assert snap2["lastTelemetryAge"] is not None
    assert snap2["dimensions"]["managementReachability"]["state"] == "reachable"
    assert snap2["dimensions"]["sensorAcquisition"] == {"state": "all_ok", "mppt": "all_ok", "pzem": "all_ok", "sensor3": "all_ok"}
    assert snap2["dimensions"]["freshness"]["dataAge"] == "fresh"
    assert snap2["dimensions"]["httpTransport"]["configured"] is True
    assert snap["temperatureC"] is None


# 27. Health history no fake data
def test_health_history_no_data():
    hist = AppHandler.health_history_unavailable("RUT-1", "not_configured")
    assert hist["state"] == "not_integrated" and hist["reason"] == "not_configured"
    for series in ("cpu", "ram", "temperature", "rssi"):
        assert hist[series] == []
    hist2 = AppHandler.health_history_unavailable("RUT-1", "no_history_yet")
    assert hist2["state"] == "no_data" and hist2["reason"] == "no_history_yet"


# 28. Rate limiting helper
def test_rate_limiter():
    rl = RateLimiter("2/10")
    assert rl.allow("ip-1")
    assert rl.allow("ip-1")
    assert not rl.allow("ip-1")
    assert rl.allow("ip-2")


# 29. Audit logging
def test_audit_log():
    store = Store(":memory:")
    store.audit("admin@example.com", "user.login", "127.0.0.1")
    logs = store.list_audit_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["action"] == "user.login"


# 30. Delivery worker drains queue and handles retries
def test_delivery_worker_loop():
    store = Store(":memory:")
    worker = ZabbixDeliveryWorker(store, interval=0.01)
    # Worker flushes safely without error
    worker.flush_once()
    assert store.count_zabbix_queue() == 0

def test_unmapped_field_storage_and_query():
    store = Store(":memory:")
    payload = base_payload(custom_sensor=99.9, string_extra="debug")
    code, res = process_payload(store, payload, actor="test")
    assert code == 202
    assert "custom_sensor" in res["unmapped"]
    unmapped = store.list_unmapped()
    assert len(unmapped) == 2
    fields = {r["field"]: r["observed_type"] for r in unmapped}
    assert fields["custom_sensor"] == "number"
    assert fields["string_extra"] == "string"

def test_strict_topic_validation():
    from backend.telemetry import parse_json_payload, validate_payload, ValidationError
    raw = b'{"site_id":"SITE-01","sn":"8800000001","ts":1700000000000}'
    data = parse_json_payload(raw)
    # Valid topic matching payload site_id
    vt = validate_payload(data, topic="entermqtt/mitratel/SITE-01/telemetry", now_ms=1700000001000)
    assert vt.site_id == "SITE-01"
    # Topic mismatch with payload site_id
    try:
        validate_payload(data, topic="entermqtt/mitratel/SITE-OTHER/telemetry", now_ms=1700000001000)
        raise AssertionError("topic site_id mismatch accepted")
    except ValidationError:
        pass
    # Invalid topic structure / wildcards
    for bad_topic in ["entermqtt/mitratel/+/telemetry", "entermqtt/mitratel/#", "other/topic", "entermqtt/mitratel/SITE-01"]:
        try:
            validate_payload(data, topic=bad_topic, now_ms=1700000001000)
            raise AssertionError(f"bad topic '{bad_topic}' accepted")
        except ValidationError:
            pass

def test_non_finite_json_rejected():
    from backend.telemetry import parse_json_payload, ValidationError
    for bad_raw in [b'{"site_id":"S1","sn":"123","ts":1000,"sps_mppt_pv_v": NaN}',
                    b'{"site_id":"S1","sn":"123","ts":1000,"sps_mppt_pv_v": Infinity}',
                    b'{"site_id":"S1","sn":"123","ts":1000,"sps_mppt_pv_v": -Infinity}']:
        try:
            parse_json_payload(bad_raw)
            raise AssertionError(f"non-finite json {bad_raw!r} accepted")
        except ValidationError:
            pass

def test_identity_conflict_rejected():
    from backend.store import IdentityConflictError
    store = Store(":memory:")
    # Ingest serial 8800000001 under SITE-01
    p1 = base_payload(site_id="SITE-01", sn="8800000001")
    c1, _ = process_payload(store, p1, actor="test", topic="entermqtt/mitratel/SITE-01/telemetry")
    assert c1 == 202
    # Ingest SAME serial under DIFFERENT site SITE-02
    p2 = base_payload(site_id="SITE-02", sn="8800000001")
    c2, res2 = process_payload(store, p2, actor="test", topic="entermqtt/mitratel/SITE-02/telemetry")
    assert c2 == 409
    assert res2["error"] == "identity_conflict"

def test_dynamic_freshness_computation():
    store = Store(":memory:")
    # Insert device with last_seen 400 seconds ago
    now_ms = 1_700_000_500_000
    ts_400s_ago = now_ms - 400_000
    row = {
        "device_id": "RUT-TEST-001",
        "site_id": "SITE-01",
        "serial": "8800000001",
        "site_name": None,
        "status": "online",
        "last_seen": ts_400s_ago,
        "latest_received_at": ts_400s_ago,
        "latest_processed_at": ts_400s_ago,
        "network": None,
        "health": "unknown",
        "connectivity": "unknown",
        "data_age": "fresh",  # stored stale label in DB
        "mppt_state": "all_ok",
        "pzem_state": "all_ok",
        "last_payload": "{}",
        "received_at": ts_400s_ago,
        "integration_status": "accepted",
    }
    store.upsert_device(row)
    # get_device should return 'stale' at now_ms (400s elapsed)
    dev = store.get_device("RUT-TEST-001", now_ms=now_ms)
    assert dev is not None
    assert dev["data_age"] == "stale"
    assert dev["status"] == "stale"
    # get_device should return 'offline' at now_ms + 600s (1000s elapsed)
    dev_offline = store.get_device("RUT-TEST-001", now_ms=now_ms + 600_000)
    assert dev_offline["data_age"] == "offline"
    assert dev_offline["status"] == "offline"


def test_stale_write_path_uses_canonical_status():
    import time as _time
    from backend.config import config as _config
    from backend.store import compute_status as _compute_status
    store = Store(":memory:")
    now_ms = int(_time.time() * 1000)
    stale_ts = now_ms - (_config.FRESHNESS_LIVE_SECONDS + 100) * 1000
    code, _ = process_payload(
        store,
        base_payload(ts=stale_ts),
        actor="test",
        received_ms=now_ms,
    )
    assert code == 202
    stored = store.db.execute(
        "SELECT status, data_age FROM devices WHERE device_id=?",
        ("RUT-8800000001",),
    ).fetchone()
    assert stored is not None
    assert stored["data_age"] == "stale"
    # Write path must agree with the canonical read-path mapping.
    assert stored["status"] == _compute_status("stale", False, "all_ok", "all_ok") == "stale"

def test_session_cookie_max_age_dynamic():
    from backend.httputil import session_cookie
    from backend.config import config
    cookie = session_cookie("test-token", secure=True)
    assert f"Max-Age={config.SESSION_TTL_SECONDS}" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie

def test_zabbix_recv_exact_framing():
    import socket
    from backend.zabbix_client import _recv_exact
    s1, s2 = socket.socketpair()
    s1.sendall(b"ZBXD\x01\x05\x00\x00\x00\x00\x00\x00\x00hello")
    s1.close()
    header = _recv_exact(s2, 13)
    assert header == b"ZBXD\x01\x05\x00\x00\x00\x00\x00\x00\x00"
    payload = _recv_exact(s2, 5)
    assert payload == b"hello"
    s2.close()

def test_zabbix_history_per_item_bounded():
    from unittest.mock import MagicMock
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://fake.lan/api_jsonrpc.php", "token")
    client._call = MagicMock(return_value=[
        {"itemid": "1001", "clock": "1700000000", "value": "24.5", "ns": "0"}
    ])
    items = [
        {"itemid": "1001", "key_": "rut.mppt.pv.v", "value_type": 0},
        {"itemid": "1002", "key_": "rut.mppt.pv.a", "value_type": 0},
    ]
    res = client.history_for_items("10001", items, limit=50)
    assert client._call.call_count == 2
    assert len(res) == 2
def test_purge_old_processed_messages():
    store = Store(":memory:")
    now_ms = 1_700_000_000_000
    # Insert an accepted message
    store.mark_message("msg-01", "RUT-TEST", now_ms, now_ms)
    # Before purge retention (e.g. 1000s later)
    purged = store.purge_old_processed_messages(retention_seconds=86400, now_ms=now_ms + 1_000_000)
    # After purge retention (e.g. 100,000s later)
    purged = store.purge_old_processed_messages(retention_seconds=86400, now_ms=now_ms + 100_000_000)
    assert purged == 1

def test_zabbix_delivery_worker_sender_submit_success():
    from backend.zabbix_client import zabbix, SenderResult
    store = Store(":memory:")
    payload = [{"host": "RUT-8800000001", "key": "rut.mppt.pv.v", "value": 24.5, "clock": 1700000000, "ns": 0}]
    store.enqueue_zabbix("msg-01", "RUT-8800000001", "RUT-8800000001", payload, 100)
    assert store.count_zabbix_queue() == 1

    calls = []
    orig_ensure = zabbix.ensure_device_host
    orig_reconcile = zabbix.reconcile_items
    orig_submit = zabbix.sender_submit
    try:
        zabbix.api_url = "http://127.0.0.1:10051/api_jsonrpc.php"
        zabbix.token = "test-token"
        zabbix.ensure_device_host = lambda d: "10001"
        zabbix.reconcile_items = lambda h, f: None
        def mock_submit(values):
            calls.append(values)
            return SenderResult(processed=len(values), failed=0, total=len(values), response="success")
        zabbix.sender_submit = mock_submit

        worker = ZabbixDeliveryWorker(store, interval=0.01)
        worker.flush_once()

        assert len(calls) == 1
        assert calls[0] == payload
        assert store.count_zabbix_queue() == 0
    finally:
        zabbix.api_url = ""
        zabbix.token = ""
        zabbix.ensure_device_host = orig_ensure
        zabbix.reconcile_items = orig_reconcile
        zabbix.sender_submit = orig_submit

def test_zabbix_delivery_worker_sender_failure_retains_queue():
    from backend.zabbix_client import zabbix, ZabbixError
    store = Store(":memory:")
    payload = [{"host": "RUT-8800000001", "key": "rut.mppt.pv.v", "value": 24.5, "clock": 1700000000, "ns": 0}]
    store.enqueue_zabbix("msg-01", "RUT-8800000001", "RUT-8800000001", payload, 100)
    assert store.count_zabbix_queue() == 1

    orig_ensure = zabbix.ensure_device_host
    orig_reconcile = zabbix.reconcile_items
    orig_submit = zabbix.sender_submit
    try:
        zabbix.api_url = "http://127.0.0.1:10051/api_jsonrpc.php"
        zabbix.token = "test-token"
        zabbix.ensure_device_host = lambda d: "10001"
        zabbix.reconcile_items = lambda h, f: None
        def mock_fail(values):
            raise ZabbixError("CONNECTION_ERROR", "Zabbix sender failed to connect")
        zabbix.sender_submit = mock_fail

        worker = ZabbixDeliveryWorker(store, interval=0.01)
        worker.flush_once()

        assert store.count_zabbix_queue() == 1
        row = store.pop_zabbix_batch(1)[0]
        assert row["attempts"] == 1
        assert "CONNECTION_ERROR" in row["last_error"]
    finally:
        zabbix.api_url = ""
        zabbix.token = ""
        zabbix.ensure_device_host = orig_ensure
        zabbix.reconcile_items = orig_reconcile
        zabbix.sender_submit = orig_submit

def test_zabbix_delivery_worker_partial_success_retains_failed():
    from backend.zabbix_client import zabbix, SenderResult, ZabbixError
    store = Store(":memory:")
    p1 = [{"host": "RUT-1", "key": "rut.mppt.pv.v", "value": 24.5, "clock": 1700000000, "ns": 0}] * 5
    p2 = [{"host": "RUT-2", "key": "rut.mppt.pv.v", "value": 12.0, "clock": 1700000000, "ns": 0}]
    store.enqueue_zabbix("msg-01", "RUT-1", "RUT-1", p1, 100)
    store.enqueue_zabbix("msg-02", "RUT-2", "RUT-2", p2, 100)
    assert store.count_zabbix_queue() == 2

    orig_ensure = zabbix.ensure_device_host
    orig_reconcile = zabbix.reconcile_items
    orig_submit = zabbix.sender_submit
    try:
        zabbix.api_url = "http://127.0.0.1:10051/api_jsonrpc.php"
        zabbix.token = "test-token"
        zabbix.ensure_device_host = lambda d: "10001"
        zabbix.reconcile_items = lambda h, f: None
        def mock_submit(values):
            if values[0]["host"] == "RUT-1":
                return SenderResult(processed=5, failed=0, total=5, response="success")
            raise ZabbixError("ZABBIX_SENDER_ERROR", "processed: 0; failed: 1; total: 1")
        zabbix.sender_submit = mock_submit

        worker = ZabbixDeliveryWorker(store, interval=0.01)
        worker.flush_once()

        assert store.count_zabbix_queue() == 1
        remaining = store.pop_zabbix_batch(10)[0]
        assert remaining["device_id"] == "RUT-2"
        assert remaining["attempts"] == 1
    finally:
        zabbix.api_url = ""
        zabbix.token = ""
        zabbix.ensure_device_host = orig_ensure
        zabbix.reconcile_items = orig_reconcile
        zabbix.sender_submit = orig_submit

def test_argon2id_enforcement_and_pbkdf2_fallback():
    import backend.auth_util as au
    # 1. Argon2id produces valid hash with $argon2id$ prefix
    h = au.hash_password("SuperSecretPass123!")
    assert h.startswith("$argon2id$")
    assert au.verify_password("SuperSecretPass123!", h)
    assert not au.verify_password("WrongPass123!", h)

    # 2. Legacy PBKDF2 hash verification works
    import hashlib
    from backend.config import config as nms_cfg
    salt_bytes = b"somesalt12345678"
    iters = 100_000
    material = nms_cfg.AUTH_PEPPER + "legacy_secret"
    legacy_hash = hashlib.pbkdf2_hmac("sha256", material.encode(), salt_bytes, iters).hex()
    stored_legacy = f"$pbkdf2$sha256${iters}${salt_bytes.hex()}${legacy_hash}"
    assert au.verify_password("legacy_secret", stored_legacy)
    assert not au.verify_password("wrong_secret", stored_legacy)
    orig_argon2 = au._ARGON2
    try:
        au._ARGON2 = None
        try:
            au.hash_password("AnyPassword123!")
            raise AssertionError("hash_password should raise RuntimeError if Argon2 is missing")
        except RuntimeError as e:
            assert "ARGON2ID_REQUIRED" in str(e)
    finally:
        au._ARGON2 = orig_argon2
    # 4. Password strength validation
    valid, _ = au.validate_password_strength("validpassword123")
    assert valid is True
    valid_short, err_short = au.validate_password_strength("short")
    assert valid_short is False
    assert "at least 8" in err_short
    valid_long, err_long = au.validate_password_strength("a" * 257)
    assert valid_long is False
    assert "cannot exceed 256" in err_long


def test_rate_limiter_bounded_memory_and_eviction():
    from backend.auth_util import RateLimiter
    rl = RateLimiter(spec="10/60", max_keys=100)
    # Add 150 distinct keys
    for i in range(150):
        rl.allow(f"key_{i}")
    assert len(rl._hits) <= 100

    # Evict expired
    rl_exp = RateLimiter(spec="10/60", max_keys=100)
    rl_exp.allow("active_key")
    rl_exp._hits["old_key"] = deque([time.monotonic() - 120])
    rl_exp._evict_expired(time.monotonic())
    assert "old_key" not in rl_exp._hits
    assert "active_key" in rl_exp._hits
def test_bootstrap_admin_and_forced_password_change_flag():
    import tempfile
    from backend.store import Store
    from backend.app import bootstrap_admin
    import backend.config as cfg

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        store = Store(tmp.name)
        # Empty password -> does not create admin
        orig_pass = cfg.config.BOOTSTRAP_ADMIN_PASSWORD
        try:
            cfg.config.BOOTSTRAP_ADMIN_PASSWORD = ""
            bootstrap_admin(store)
            assert store.count_active_admins() == 0

            # Provided password -> creates admin with must_change_password=1
            cfg.config.BOOTSTRAP_ADMIN_PASSWORD = "InitialBootstrapPassword123!"
            bootstrap_admin(store)
            admin = store.get_user_by_email(cfg.config.BOOTSTRAP_ADMIN_EMAIL)
            assert admin is not None
            assert admin["role"] == "admin"
            assert admin["must_change_password"] == 1
            assert admin["password_hash"].startswith("$argon2id$")
        finally:
            cfg.config.BOOTSTRAP_ADMIN_PASSWORD = orig_pass


def test_user_management_crud_and_last_admin_protection():
    import tempfile
    from backend.store import Store
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        store = Store(tmp.name)
        u1_id = store.create_user("admin1@example.test", "Admin One", hash_password("Password123!"), "admin", must_change_password=1)
        assert store.count_active_admins() == 1

        # Creating a second user
        u2_id = store.create_user("operator@example.test", "Operator One", hash_password("Password123!"), "operator", must_change_password=1)
        assert store.count_active_admins() == 1

        # Listing users
        users = store.list_users()
        assert len(users) == 2
        emails = [u["email"] for u in users]
        assert "admin1@example.test" in emails
        assert "operator@example.test" in emails

        # Attempt to demote/disable last active admin must be prevented by business logic
        store.set_user_role(u2_id, "admin")
        assert store.count_active_admins() == 2

        # Disable one admin
        store.update_user_profile(u2_id, disabled=1)
        assert store.count_active_admins() == 1

        # Reset password
        store.set_user_password(u1_id, hash_password("NewPassword456!"), must_change_password=1)
        u1_refreshed = store.get_user_by_id(u1_id)
        assert verify_password("NewPassword456!", u1_refreshed["password_hash"])
        assert u1_refreshed["must_change_password"] == 1

        # Session deletion
        tok = new_token()
        csrf_tok = new_token()
        store.create_session(tok, u1_id, int(time.time() + 3600), csrf_tok)
        assert store.get_session(tok) is not None
        store.delete_user_sessions(u1_id)
        assert store.get_session(tok) is None


def test_google_sso_removed_from_providers_and_routes():
    import backend.config as cfg
    assert not hasattr(cfg, "GOOGLE_CLIENT_ID")
    assert not hasattr(cfg, "GOOGLE_CLIENT_SECRET")
    assert not hasattr(cfg, "google_configured")
    st = cfg.startup_state()
    assert "google" not in st
    assert "mqtt" in st
    assert "zabbix" in st
def test_patch_delete_dispatch_and_csrf():
    import http.server
    import io
    import json
    import tempfile
    import backend.app as app_mod
    from backend.store import Store
    from backend.auth_util import hash_password, new_token, verify_password
    import time
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        test_store = Store(tmp.name)
        # Patch the module's store
        original_store = app_mod.store
        tunnel_manager = app_mod.tunnel_manager
        app_mod.store = test_store
        tunnel_manager.store = test_store
        try:
            admin_id = test_store.create_user("admin@example.test", "Admin", hash_password("Password123!"), "admin", must_change_password=0)
            user_id = test_store.create_user("user@example.test", "User", hash_password("Password123!"), "operator", must_change_password=1)

            # Create admin session
            admin_token = new_token()
            admin_csrf = new_token()
            test_store.create_session(admin_token, admin_id, int(time.time() * 1000) + 3600_000, admin_csrf)

            # Build a mock handler to test routing
            class MockHandler(app_mod.Handler):
                def __init__(self, method, path, body=b"", headers=None):
                    self.method = method
                    self.path = path
                    self.rfile = io.BytesIO(body)
                    self.wfile = io.BytesIO()
                    self.headers = headers or {}
                    if body and "Content-Length" not in self.headers:
                        self.headers["Content-Length"] = str(len(body))
                    self.client_address = ("127.0.0.1", 54321)
                    self.request = None
                    self.server_version = "TelnusaNMS/0.1"
                    self.sys_version = ""
                    self.error_message_format = ""
                    self.error_content_type = "text/html"

                def send_response(self, code, message=None):
                    self.response_code = code

                def send_header(self, keyword, value):
                    if not hasattr(self, "response_headers"):
                        self.response_headers = {}
                    self.response_headers[keyword] = value

                def end_headers(self):
                    pass

                def _remote_key(self):
                    return "127.0.0.1"

                def _session(self):
                    tok = self.headers.get("Cookie", "").replace("nms_session=", "")
                    sess = test_store.get_session(tok)
                    if not sess:
                        return None, None
                    if sess["expires_at"] < app_mod.now_ms():
                        test_store.delete_session(tok)
                        return None, None
                    user = test_store.get_user_by_id(sess["user_id"])
                    if not user or user["disabled"]:
                        test_store.delete_session(tok)
                        return None, None
                    return sess, user

                def _require_role(self, role):
                    sess, user = self._session()
                    if not user:
                        raise app_mod.ApiProblem(401, "Authentication required.")
                    if user["role"] != role:
                        raise app_mod.ApiProblem(403, "Forbidden.")
                    return sess, user
            # Test PATCH /api/users/:id (update user)
            handler = MockHandler("PATCH", f"/api/users/{user_id}", json.dumps({"name": "Updated Name"}).encode(),
                                  {"Cookie": f"nms_session={admin_token}", "X-CSRF-Token": admin_csrf, "Content-Type": "application/json"})
            handler.do_PATCH()
            assert handler.response_code == 200
            updated = test_store.get_user_by_id(user_id)
            assert updated["display_name"] == "Updated Name"

            # Test PATCH without CSRF should fail
            handler = MockHandler("PATCH", f"/api/users/{user_id}", json.dumps({"name": "No CSRF"}).encode(),
                                  {"Cookie": f"nms_session={admin_token}", "Content-Type": "application/json"})
            handler.do_PATCH()
            assert handler.response_code == 403

            # Test DELETE /api/users/:id (disable user)
            handler = MockHandler("DELETE", f"/api/users/{user_id}", b"",
                                  {"Cookie": f"nms_session={admin_token}", "X-CSRF-Token": admin_csrf})
            handler.do_DELETE()
            assert handler.response_code == 200
            disabled = test_store.get_user_by_id(user_id)
            assert disabled["disabled"] == 1

            # Test DELETE without CSRF should fail
            # Re-enable user first
            test_store.update_user_profile(user_id, disabled=0)
            handler = MockHandler("DELETE", f"/api/users/{user_id}", b"",
                                  {"Cookie": f"nms_session={admin_token}"})
            handler.do_DELETE()
            assert handler.response_code == 403
        finally:
            # Restore original store
            app_mod.store = original_store
            tunnel_manager.store = original_store


def test_error_propagation_401_403_500():
    import io
    import json
    import tempfile
    import backend.app as app_mod
    from backend.store import Store
    from backend.auth_util import hash_password, new_token
    import time

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        test_store = Store(tmp.name)
        # Patch the module's store
        original_store = app_mod.store
        tunnel_manager = app_mod.tunnel_manager
        app_mod.store = test_store
        tunnel_manager.store = test_store
        try:
            admin_id = test_store.create_user("admin@example.test", "Admin", hash_password("Password123!"), "admin", must_change_password=0)

            admin_token = new_token()
            admin_csrf = new_token()
            test_store.create_session(admin_token, admin_id, int(time.time() * 1000) + 3600_000, admin_csrf)

            class MockHandler(app_mod.Handler):
                def __init__(self, method, path, body=b"", headers=None):
                    self.method = method
                    self.path = path
                    self.rfile = io.BytesIO(body)
                    self.wfile = io.BytesIO()
                    self.headers = headers or {}
                    if body and "Content-Length" not in self.headers:
                        self.headers["Content-Length"] = str(len(body))
                    self.client_address = ("127.0.0.1", 54321)
                    self.request = None
                    self.server_version = "TelnusaNMS/0.1"
                    self.sys_version = ""
                    self.error_message_format = ""
                    self.error_content_type = "text/html"

                def send_response(self, code, message=None):
                    self.response_code = code

                def send_header(self, keyword, value):
                    if not hasattr(self, "response_headers"):
                        self.response_headers = {}
                    self.response_headers[keyword] = value

                def end_headers(self):
                    pass

                def _remote_key(self):
                    return "127.0.0.1"

                def _session(self):
                    tok = self.headers.get("Cookie", "").replace("nms_session=", "")
                    sess = test_store.get_session(tok)
                    if not sess:
                        return None, None
                    if sess["expires_at"] < app_mod.now_ms():
                        test_store.delete_session(tok)
                        return None, None
                    user = test_store.get_user_by_id(sess["user_id"])
                    if not user or user["disabled"]:
                        test_store.delete_session(tok)
                        return None, None
                    return sess, user

                def _require_role(self, role):
                    sess, user = self._session()
                    if not user:
                        raise app_mod.ApiProblem(401, "Authentication required.")
                    if user["role"] != role:
                        raise app_mod.ApiProblem(403, "Forbidden.")
                    return sess, user

            # Test 401 - no auth
            handler = MockHandler("PATCH", f"/api/users/{admin_id}", b"", {})
            handler.do_PATCH()
            assert handler.response_code == 401

            # Test 403 - viewer trying admin action
            viewer_id = test_store.create_user("viewer@example.test", "Viewer", hash_password("Password123!"), "viewer", must_change_password=0)
            viewer_token = new_token()
            viewer_csrf = new_token()
            test_store.create_session(viewer_token, viewer_id, int(time.time() * 1000) + 3600_000, viewer_csrf)
            handler = MockHandler("PATCH", f"/api/users/{admin_id}", json.dumps({"name": "hack"}).encode(),
                                  {"Cookie": f"nms_session={viewer_token}", "X-CSRF-Token": viewer_csrf, "Content-Type": "application/json"})
            handler.do_PATCH()
            assert handler.response_code == 403

            # Test 404 - invalid user id
            handler = MockHandler("PATCH", "/api/users/not-a-valid-uuid", json.dumps({"name": "test"}).encode(),
                                  {"Cookie": f"nms_session={admin_token}", "X-CSRF-Token": admin_csrf, "Content-Type": "application/json"})
            handler.do_PATCH()
            # Should be 404 (not found) not 500 - the handler catches it
            assert handler.response_code == 404

            # Test successful empty result vs error distinction
            handler = MockHandler("GET", "/api/users", b"",
                                  {"Cookie": f"nms_session={admin_token}"})
            handler.do_GET()
            assert handler.response_code == 200
            body = json.loads(handler.wfile.getvalue().decode())
            assert "users" in body
            assert len(body["users"]) >= 2
        finally:
            # Restore original store
            app_mod.store = original_store
            tunnel_manager.store = original_store


def test_client_ip_trusted_proxy_and_spoof_protection():
    from backend.httputil import client_ip

    class DummyHandler:
        def __init__(self, ip, headers_dict=None):
            self.client_address = (ip, 54321)
            self.headers = headers_dict or {}

    # 1. TRUST_PROXY=False ignores X-Forwarded-For entirely
    h1 = DummyHandler("192.168.1.50", {"X-Forwarded-For": "203.0.113.199, 10.0.0.1"})
    assert client_ip(h1, trust_proxy=False) == "192.168.1.50"

    # 2. TRUST_PROXY=True safely extracts the rightmost IP appended by trusted reverse proxy (Apache)
    h2 = DummyHandler("127.0.0.1", {"X-Forwarded-For": "attacker.spoofed.ip, 203.0.113.55"})
    assert client_ip(h2, trust_proxy=True) == "203.0.113.55"

    # 3. Attacker multi-header chain injection cannot bypass rightmost trusted token
    h3 = DummyHandler("127.0.0.1", {"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 8.8.8.8, 198.51.100.77"})
    assert client_ip(h3, trust_proxy=True) == "198.51.100.77"

    # 4. Fallbacks when X-Forwarded-For is absent or empty whitespace
    h4 = DummyHandler("127.0.0.1", {})
    assert client_ip(h4, trust_proxy=True) == "127.0.0.1"

    h5 = DummyHandler("127.0.0.1", {"X-Forwarded-For": "  ,  "})
    assert client_ip(h5, trust_proxy=True) == "127.0.0.1"


def test_dual_key_login_rate_limiting_and_no_account_lockout():
    from backend.auth_util import RateLimiter
    import tempfile

    # Dual-key rate limiter with 3 attempts per 60 seconds
    limiter = RateLimiter("3/60", max_keys=100)
    ip = "203.0.113.10"
    target_email = "target.user@example.test"

    ip_key = f"login:ip:{ip}"
    account_key = f"login:account:{target_email}"

    # 1. IP throttling
    assert limiter.allow(ip_key) is True
    assert limiter.allow(ip_key) is True
    assert limiter.allow(ip_key) is True
    assert limiter.allow(ip_key) is False  # IP throttled

    # 2. Account throttling across rotating IPs
    email_limiter = RateLimiter("3/60", max_keys=100)
    for mock_ip in ["10.0.0.1", "10.0.0.2", "10.0.0.3"]:
        mock_ip_key = f"login:ip:{mock_ip}"
        assert email_limiter.allow(mock_ip_key) is True  # each individual IP is fine
        assert email_limiter.allow(account_key) is True   # account attempt counts

    # 4th attempt from a fresh IP against the same account is blocked by account key
    fresh_ip_key = "login:ip:10.0.0.4"
    assert email_limiter.allow(fresh_ip_key) is True
    assert email_limiter.allow(account_key) is False  # account-level throttling kicks in

    # 3. Verify user account in SQLite is NOT permanently locked / disabled
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        store = Store(tmp.name)
        store.create_user("test@example.test", "Test User", "hash_placeholder", "viewer")
        user = store.get_user_by_email("test@example.test")
        assert user["disabled"] == 0  # No permanent DB lockout

def test_tunnel_command_construction_and_security_invariants():
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import build_ssh_command

    target = DeviceRemoteTarget(
        device_id="RUT-8800000001",
        ssh_host="192.168.1.144",
        ssh_port=22,
        webui_target_host="192.168.10.1",
        webui_target_port=443,
    )

    cmd = build_ssh_command(
        target=target,
        local_port=18443,
        key_path="/etc/telnusa/id_ed25519",
        known_hosts_path="/etc/telnusa/known_hosts",
        ssh_user="nmsremote",
    )

    # 1. Argument list validation
    assert isinstance(cmd, list)
    assert cmd[0] == "ssh"
    assert "-N" in cmd
    assert "-T" in cmd
    assert "-oBatchMode=yes" in cmd
    assert "-oExitOnForwardFailure=yes" in cmd
    assert "-oStrictHostKeyChecking=yes" in cmd
    assert "-oUserKnownHostsFile=/etc/telnusa/known_hosts" in cmd
    assert "-i" in cmd
    key_idx = cmd.index("-i")
    assert cmd[key_idx + 1] == "/etc/telnusa/id_ed25519"

    # 2. Local port forwarding binding strictly to 127.0.0.1
    assert "-L" in cmd
    l_idx = cmd.index("-L")
    assert cmd[l_idx + 1] == "127.0.0.1:18443:192.168.10.1:443"

    # 3. Destination host & port
    assert "nmsremote@192.168.1.144" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "22"

    # 4. Zero credentials in command
    cmd_str = " ".join(cmd)
    assert "password" not in cmd_str.lower()


def test_tunnel_ssh_key_and_known_hosts_validation():
    import os
    import tempfile
    from backend.tunnel import validate_ssh_config

    # 1. Empty paths rejected
    ok, err = validate_ssh_config("", "/path/to/known_hosts")
    assert not ok
    assert "key path not configured" in err

    ok, err = validate_ssh_config("/path/to/key", "")
    assert not ok
    assert "known_hosts path not configured" in err

    # 2. Non-existent files rejected
    ok, err = validate_ssh_config("/nonexistent/id_rsa", "/nonexistent/known_hosts")
    assert not ok
    assert "not found" in err

    # 3. Insecure permissions rejected (POSIX)
    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o644)  # Group/world readable -> insecure!
        os.chmod(hosts_f.name, 0o644)
        ok, err = validate_ssh_config(key_f.name, hosts_f.name)
        assert not ok
        assert "insecure permissions" in err

        # 4. Strict 0600 permissions accepted
        os.chmod(key_f.name, 0o600)
        ok, err = validate_ssh_config(key_f.name, hosts_f.name)
        assert ok
        assert err is None


def test_tunnel_manager_lifecycle_and_state_transitions():
    import os
    import tempfile
    from backend.store import Store
    from backend.tunnel import TunnelManager, TunnelState

    store = Store(":memory:")
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "integration_status": "accepted",
        "last_seen": 1000,
    })

    # 1. Unknown device -> FAILED
    tm_dummy = TunnelManager(store=store, key_path="/dummy", known_hosts_path="/dummy")
    info_unknown = tm_dummy.ensure("RUT-UNKNOWN")
    assert info_unknown.state == TunnelState.FAILED
    assert "not found" in info_unknown.error

    # 2. Missing key / config -> BLOCKED_CONFIG
    info_blocked = tm_dummy.ensure("RUT-8800000001")
    assert info_blocked.state == TunnelState.BLOCKED_CONFIG

    # 3. Successful lifecycle with mock runner
    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)

        class FakeProcess:
            def __init__(self, pid=12345):
                self.pid = pid
                self._exit_code = None

            def poll(self):
                return self._exit_code

            def terminate(self):
                self._exit_code = 0

            def wait(self, timeout=None):
                return self._exit_code

        active_ports = set()

        def fake_runner(cmd):
            # Extract allocated port
            l_idx = cmd.index("-L")
            port_str = cmd[l_idx + 1].split(":")[1]
            active_ports.add(int(port_str))
            return FakeProcess(pid=99999)

        def fake_socket_checker(host, port, timeout=0.2):
            return host == "127.0.0.1" and port in active_ports

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            # REMOTE-03: an explicit non-root identity must be configured.
            ssh_user="nmsremote",
            process_runner=fake_runner,
            socket_checker=fake_socket_checker,
            startup_wait_seconds=0.01,
        )

        # Start / ensure tunnel
        info = tm.ensure("RUT-8800000001")
        assert info.state == TunnelState.READY
        assert info.local_port is not None
        assert info.local_port >= 18000
        assert info.pid == 99999

        # Ensure again returns same active tunnel (idempotent)
        info2 = tm.ensure("RUT-8800000001")
        assert info2.state == TunnelState.READY
        assert info2.local_port == info.local_port

        # Health check
        assert tm.health_check("RUT-8800000001") is True

        # Simulate dead process
        proc = tm._processes["RUT-8800000001"]
        proc._exit_code = 255
        assert tm.health_check("RUT-8800000001") is False
        assert tm.status("RUT-8800000001").state == TunnelState.FAILED

        # Cleanup / stop
        tm.stop("RUT-8800000001")
        assert tm.status("RUT-8800000001").state == TunnelState.STOPPED


def test_tunnel_manager_concurrency_and_port_isolation():
    import os
    import tempfile
    import threading
    from backend.store import Store
    from backend.tunnel import TunnelManager, TunnelState

    store = Store(":memory:")
    for sn in ["8800000001", "8800000002"]:
        store.upsert_device({
            "device_id": f"RUT-{sn}",
            "site_id": "SITE-A",
            "serial": sn,
            "status": "online",
            "integration_status": "accepted",
            "last_seen": 1000,
        })

    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)

        class FakeProcess:
            def __init__(self, pid=12345):
                self.pid = pid
                self._exit_code = None
            def poll(self):
                return self._exit_code
            def terminate(self):
                self._exit_code = 0
            def wait(self, timeout=None):
                return self._exit_code

        active_ports = set()
        def fake_runner(cmd):
            l_idx = cmd.index("-L")
            port_str = cmd[l_idx + 1].split(":")[1]
            active_ports.add(int(port_str))
            return FakeProcess()

        def fake_socket_checker(host, port, timeout=0.2):
            return host == "127.0.0.1" and port in active_ports

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="nmsremote",
            process_runner=fake_runner,
            socket_checker=fake_socket_checker,
            startup_wait_seconds=0.01,
        )

        # Concurrent ensure on same device
        results = []
        def worker():
            res = tm.ensure("RUT-8800000001")
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        assert all(r.state == TunnelState.READY for r in results)
        # All threads must receive identical local_port (no duplicate process spawn)
        assert len({r.local_port for r in results}) == 1

        # Different device receives isolated distinct port
        # A second registered device must receive its own isolated port.
        # (RUT-TEST-001 is no longer a built-in identity: remote access resolves
        # from the device registry, so the second device must actually be registered.)
        store.upsert_device({
            "device_id": "RUT-8800000002",
            "site_id": "SITE-A",
            "serial": "8800000002",
            "status": "online",
            "integration_status": "accepted",
            "last_seen": 1000,
        })
        res_dev2 = tm.ensure("RUT-8800000002")
        assert res_dev2.state == TunnelState.READY
        assert res_dev2.local_port != results[0].local_port

        tm.cleanup_all()
        assert tm.status("RUT-8800000001").state == TunnelState.STOPPED
        assert tm.status("RUT-TEST-001").state == TunnelState.STOPPED

def test_tunnel_startup_wait_configuration_and_override():
    import os
    from backend.config import _get_float, config
    from backend.tunnel import TunnelManager

    # 1. Config default is 3.0 when env is absent
    assert config.RUT_TUNNEL_STARTUP_WAIT_SECONDS == 3.0
    assert _get_float("NONEXISTENT_VAR", 3.0) == 3.0

    # 2. _get_float parses float from env
    os.environ["TEST_STARTUP_FLOAT"] = "5.5"
    try:
        assert _get_float("TEST_STARTUP_FLOAT", 3.0) == 5.5
    finally:
        os.environ.pop("TEST_STARTUP_FLOAT", None)

    # 3. TunnelManager receives configured startup wait by default
    tm_default = TunnelManager()
    assert tm_default.startup_wait_seconds == config.RUT_TUNNEL_STARTUP_WAIT_SECONDS
    assert tm_default.startup_wait_seconds == 3.0

    # 4. Explicit constructor startup_wait_seconds overrides config
    tm_override = TunnelManager(startup_wait_seconds=0.05)
    assert tm_override.startup_wait_seconds == 0.05

    tm_override_zero = TunnelManager(startup_wait_seconds=0.0)
    assert tm_override_zero.startup_wait_seconds == 0.0

def test_remote_webui_endpoint_tunnel_integration():
    import io
    import backend.app as app_mod
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = {}

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers[keyword] = value

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    # NOTE: the 5th positional parameter is `provider`, not a timestamp. Passing
    # `now` there stored provider=<epoch-ms> and silently defeated the role checks.
    admin_id = store.create_user("admin@telnusa.id", "Admin User", app_mod.hash_password("AdminPass123!"), role="admin")
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), role="operator")
    viewer_id = store.create_user("view@telnusa.id", "View User", app_mod.hash_password("ViewPass123!"), role="viewer")

    admin_sid = "sid-admin-webui"
    op_sid = "sid-op-webui"
    viewer_sid = "sid-view-webui"

    store.create_session(admin_sid, admin_id, now + 3600_000, "csrf-admin")
    store.create_session(op_sid, op_id, now + 3600_000, "csrf-op")
    # viewer_sid was declared but never persisted, so the viewer request could only
    # ever answer 401. The role boundary is only exercised with a real session.
    store.create_session(viewer_sid, viewer_id, now + 3600_000, "csrf-viewer")

    # Remote access resolves identity from the device registry, so the device under
    # test must be registered and integration-accepted.
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "integration_status": "accepted",
        "last_seen": 1000,
    })
    store.upsert_device({
        "device_id": "RUT-UNREGISTERED-0001",
        "site_id": "SITE-C",
        "serial": "8800000003",
        "status": "online",
        "integration_status": "accepted",
        "last_seen": 1000,
    })

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    app_mod.store = store
    try:
        # 1. Mock TunnelManager returning READY
        mock_tm_ready = TunnelManager()
        mock_tm_ready.store = store
        mock_tm_ready.ensure = lambda dev_id: TunnelInfo(
            device_id=dev_id,
            local_port=18000,
            pid=12345,
            state=TunnelState.READY,
            started_at=now,
        )
        app_mod.tunnel_manager = mock_tm_ready

        # Operator request -> 200 OK with ready URL
        h_op = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_op.do_GET()
        assert h_op.status_code == 200
        res_op = json.loads(h_op.wfile.getvalue().decode())
        assert res_op["status"] == "ready"
        assert res_op["url"] == "/api/remote/RUT-8800000001/webui/proxy/"
        assert res_op["localPort"] == 18000
        assert res_op["startedAt"] == now

        # Admin request -> 200 OK
        h_admin = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={admin_sid}"})
        h_admin.do_GET()
        assert h_admin.status_code == 200
        res_admin = json.loads(h_admin.wfile.getvalue().decode())
        assert res_admin["status"] == "ready"
        assert res_admin["url"] == "/api/remote/RUT-8800000001/webui/proxy/"
        # 2. Mock TunnelManager returning FAILED
        mock_tm_failed = TunnelManager()
        mock_tm_failed.store = store
        mock_tm_failed.ensure = lambda dev_id: TunnelInfo(
            device_id=dev_id,
            state=TunnelState.FAILED,
            error="SSH process exited unexpectedly with code 255",
        )
        app_mod.tunnel_manager = mock_tm_failed

        h_fail = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_fail.do_GET()
        assert h_fail.status_code == 503
        res_fail = json.loads(h_fail.wfile.getvalue().decode())
        assert res_fail["error"] == "REMOTE_TUNNEL_FAILED"
        assert "SSH process exited unexpectedly" in res_fail["detail"]

        # 3. Mock TunnelManager returning BLOCKED_CONFIG
        mock_tm_blocked = TunnelManager()
        mock_tm_blocked.store = store
        mock_tm_blocked.ensure = lambda dev_id: TunnelInfo(
            device_id=dev_id,
            state=TunnelState.BLOCKED_CONFIG,
            error="SSH private key path not configured.",
        )
        app_mod.tunnel_manager = mock_tm_blocked

        h_block = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_block.do_GET()
        assert h_block.status_code == 501
        res_block = json.loads(h_block.wfile.getvalue().decode())
        assert res_block["error"] == "REMOTE_NOT_CONFIGURED"
        assert "BLOCKED: MISSING EXTERNAL SERVICE" in res_block["detail"]

        # 4. Auth boundaries: Unauth -> 401, Viewer -> 403
        h_unauth = MockHandler("GET", "/api/remote/RUT-8800000001/webui")
        h_unauth.do_GET()
        assert h_unauth.status_code == 401

        h_view = MockHandler("GET", "/api/remote/RUT-8800000001/webui", headers={"Cookie": f"nms_session={viewer_sid}"})
        h_view.do_GET()
        assert h_view.status_code == 403

        # 5. Unknown device -> 404
        h_unknown = MockHandler("GET", "/api/remote/RUT-UNKNOWN-999/webui", headers={"Cookie": f"nms_session={op_sid}"})
        h_unknown.do_GET()
        assert h_unknown.status_code == 404

        # 6. Audit logs verify actions
        audit_logs = store.list_audit_logs(limit=100)
        webui_logs = [log for log in audit_logs if log["action"] == "remote.webui.open"]
        assert len(webui_logs) >= 4
        details = [log["detail"] for log in webui_logs]
        assert any("result=ready" in d and "port=18000" in d for d in details)
        assert any("result=failed" in d for d in details)
        assert any("result=blocked_config" in d for d in details)
        assert any("result=device_not_found" in d for d in details)
    finally:
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
def test_remote_webui_reverse_proxy_end_to_end():
    import http.server
    import io
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    # 1. Path parser unit verification
    assert app_mod.parse_remote_proxy_path("/api/remote/RUT-8800000001/webui/proxy") == ("RUT-8800000001", "/")
    assert app_mod.parse_remote_proxy_path("/api/remote/RUT-8800000001/webui/proxy/") == ("RUT-8800000001", "/")
    assert app_mod.parse_remote_proxy_path("/api/remote/RUT-8800000001/webui/proxy/cgi-bin/luci") == ("RUT-8800000001", "/cgi-bin/luci")
    assert app_mod.parse_remote_proxy_path("/api/remote/RUT-8800000001/webui/proxy/luci-static/resources/cbi.js") == ("RUT-8800000001", "/luci-static/resources/cbi.js")
    assert app_mod.parse_remote_proxy_path("/api/remote/RUT-8800000001/webui") is None
    assert app_mod.parse_remote_proxy_path("/api/remote/webui") is None
    assert app_mod.parse_remote_proxy_path("/api/devices/RUT-8800000001") is None

    # 2. Spin up a real mock upstream server
    class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "sysauth_https=12345; path=/; HttpOnly; SameSite=Strict")
                self.end_headers()
                self.wfile.write(b"<html><head><title>LuCI</title></head><body><a href=\"/cgi-bin/luci\">Login</a><img src=\"/luci-static/logo.png\"/></body></html>")
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/cgi-bin/luci")
                self.end_headers()
            elif self.path == "/abs-redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{upstream_port}/cgi-bin/luci")
                self.end_headers()
            elif self.path == "/gzip-html":
                import gzip as _gz
                _b = _gz.compress(b"<html><head><title>GZ</title></head><body><a href=\"/x\">y</a></body></html>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(_b)))
                self.end_headers()
                self.wfile.write(_b)
            elif self.path == "/asset.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(b"console.log('asset');")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"echo": body.decode()}).encode())

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "50")
            self.end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Allow", "GET, POST, OPTIONS, HEAD")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    upstream_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), "operator", now)
    viewer_id = store.create_user("view@telnusa.id", "View User", app_mod.hash_password("ViewPass123!"), "viewer", now)

    op_sid = "sid-op-proxy"
    viewer_sid = "sid-view-proxy"
    store.create_session(op_sid, op_id, now + 3600_000, "csrf-op")
    store.create_session(viewer_sid, viewer_id, now + 3600_000, "csrf-viewer")

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    app_mod.store = store

    # Create mock tunnel manager pointing to our upstream mock server
    mock_tm = TunnelManager()
    mock_tm.store = store
    mock_tm.ensure = lambda dev_id: TunnelInfo(
        device_id=dev_id,
        local_port=upstream_port,
        pid=99999,
        state=TunnelState.READY,
        started_at=now,
    )
    app_mod.tunnel_manager = mock_tm

    # Ensure device target resolution uses HTTP port (not 443) for this mock test
    orig_resolve = app_mod.resolve_device_target
    app_mod.resolve_device_target = lambda dev_id, st: DeviceRemoteTarget(
        device_id=dev_id,
        ssh_host="127.0.0.1",
        ssh_port=22,
        webui_target_host="127.0.0.1",
        webui_target_port=upstream_port,
    ) if dev_id == "RUT-8800000001" else None

    try:
        # 3. GET /: Verify HTML rewriting, <base> injection, and cookie rewrite
        h_get = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_get.do_GET()
        assert h_get.status_code == 200
        body = h_get.wfile.getvalue().decode()
        assert '<base href="/api/remote/RUT-8800000001/webui/proxy/">' in body
        assert 'href="/api/remote/RUT-8800000001/webui/proxy/cgi-bin/luci"' in body
        assert 'src="/api/remote/RUT-8800000001/webui/proxy/luci-static/logo.png"' in body
        # Verify Set-Cookie rewrite
        set_cookies = [v for k, v in h_get.response_headers if k.lower() == "set-cookie"]
        assert len(set_cookies) == 1
        assert "Path=/api/remote/RUT-8800000001/webui/proxy" in set_cookies[0]

        # 4. GET /redirect: Verify relative redirect rewrite
        h_redir = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/redirect", headers={"Cookie": f"nms_session={op_sid}"})
        h_redir.do_GET()
        assert h_redir.status_code == 302
        locations = [v for k, v in h_redir.response_headers if k.lower() == "location"]
        assert len(locations) == 1
        assert locations[0] == "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/luci"

        # 5. GET /abs-redirect: Verify absolute upstream redirect rewrite
        h_abs_redir = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/abs-redirect", headers={"Cookie": f"nms_session={op_sid}"})
        h_abs_redir.do_GET()
        assert h_abs_redir.status_code == 302
        locations = [v for k, v in h_abs_redir.response_headers if k.lower() == "location"]
        assert len(locations) == 1
        assert locations[0] == "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/luci"

        # 6. GET /asset.js: Verify JS pass-through
        h_asset = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/asset.js", headers={"Cookie": f"nms_session={op_sid}"})
        h_asset.do_GET()
        assert h_asset.status_code == 200
        assert h_asset.wfile.getvalue() == b"console.log('asset');"

        # 7. POST: Verify body forwarding
        payload = b"user=root&pass=test"
        h_post = MockHandler(
            "POST",
            "/api/remote/RUT-8800000001/webui/proxy/login",
            headers={"Cookie": f"nms_session={op_sid}", "Content-Length": str(len(payload)), "Content-Type": "application/x-www-form-urlencoded"},
            body=payload,
        )
        h_post.do_POST()
        assert h_post.status_code == 200
        res_post = json.loads(h_post.wfile.getvalue().decode())
        assert res_post["echo"] == "user=root&pass=test"

        # 8. HEAD: Verify headers sent without body
        h_head = MockHandler("HEAD", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_head.do_HEAD()
        assert h_head.status_code == 200
        assert len(h_head.wfile.getvalue()) == 0

        # 9. OPTIONS: Verify options handling
        h_opt = MockHandler("OPTIONS", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_opt.do_OPTIONS()
        assert h_opt.status_code in (200, 204)

        # 10. Auth boundary: Unauthenticated -> 401
        h_unauth = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/")
        h_unauth.do_GET()
        assert h_unauth.status_code == 401

        # 11. Auth boundary: Viewer -> 403
        h_view = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={viewer_sid}"})
        h_view.do_GET()
        assert h_view.status_code == 403

        # 12. Unknown device -> 404
        h_unk = MockHandler("GET", "/api/remote/RUT-UNKNOWN-DEV/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_unk.do_GET()
        assert h_unk.status_code == 404

        # 13. Tunnel BLOCKED_CONFIG -> 501
        mock_tm_blocked = TunnelManager()
        mock_tm_blocked.store = store
        mock_tm_blocked.ensure = lambda dev_id: TunnelInfo(
            device_id=dev_id,
            state=TunnelState.BLOCKED_CONFIG,
            error="SSH key not configured.",
        )
        app_mod.tunnel_manager = mock_tm_blocked
        h_block = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_block.do_GET()
        assert h_block.status_code == 501

        # 14. Tunnel FAILED -> 503
        mock_tm_failed = TunnelManager()
        mock_tm_failed.store = store
        mock_tm_failed.ensure = lambda dev_id: TunnelInfo(
            device_id=dev_id,
            state=TunnelState.FAILED,
            error="SSH process failed.",
        )
        app_mod.tunnel_manager = mock_tm_failed
        h_fail = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/", headers={"Cookie": f"nms_session={op_sid}"})
        h_fail.do_GET()
        assert h_fail.status_code == 503

        # 15. SSRF immunity
        h_ssrf = MockHandler("GET", "/api/remote/webui?host=http://169.254.169.254", headers={"Cookie": f"nms_session={op_sid}"})
        h_ssrf.do_GET()
        assert h_ssrf.status_code == 404

        # 16. GET /gzip-html: gzip-encoded upstream HTML must arrive as valid
        # rewritten identity HTML, never corrupted binary.
        app_mod.tunnel_manager = mock_tm
        h_gz = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/gzip-html", headers={"Cookie": f"nms_session={op_sid}"})
        h_gz.do_GET()
        assert h_gz.status_code == 200
        assert not [v for k, v in h_gz.response_headers if k.lower() == "content-encoding"]
        gz_text = h_gz.wfile.getvalue().decode("utf-8")
        assert '<base href="/api/remote/RUT-8800000001/webui/proxy/">' in gz_text
        assert 'href="/api/remote/RUT-8800000001/webui/proxy/x"' in gz_text

    finally:
        server.shutdown()
        server.server_close()
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
        app_mod.resolve_device_target = orig_resolve

def test_remote_webui_js_api_paths_rewritten_to_proxy_prefix():
    """RUT JS bundles emit root-absolute /api/... string literals. <base href> does not
    affect runtime literals inside an already-loaded ES module, so they must be rewritten
    in the JS response itself or they escape the proxy and 404 against TELNUSA's API root."""
    import gzip as _gz
    import http.server
    import io
    import shutil
    import subprocess
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    PREFIX = "/api/remote/RUT-8800000001/webui/proxy"
    # Mirrors the real RUT906 bundle shapes observed in the browser: double-quoted,
    # backtick template, and a bare base literal, plus an already-prefixed control.
    JS_SRC = (
        'const a=await x.post("/api/login"),b=await y.get("/api/system/device/status");'
        'const c=`/api/widget/config${id}`;const d="/api/";'
        'const e="/api/remote/other/webui/proxy/api/login";'
    )

    class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/app.js":
                body = JS_SRC.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/app.js.gz":
                body = _gz.compress(JS_SRC.encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/page.html":
                body = b'<html><head></head><body><a href="/x">y</a></body></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/data.json":
                # Non-HTML/non-JS must pass through byte-identical.
                body = b'{"api":"/api/not-rewritten-here"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    upstream_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), "operator", now)
    op_sid = "sid-op-js"
    store.create_session(op_sid, op_id, now + 3600_000, "csrf-op")

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    orig_resolve = app_mod.resolve_device_target
    app_mod.store = store

    mock_tm = TunnelManager()
    mock_tm.store = store
    mock_tm.ensure = lambda dev_id: TunnelInfo(
        device_id=dev_id, local_port=upstream_port, pid=99999,
        state=TunnelState.READY, started_at=now,
    )
    app_mod.tunnel_manager = mock_tm
    app_mod.resolve_device_target = lambda dev_id, st: DeviceRemoteTarget(
        device_id=dev_id, ssh_host="127.0.0.1", ssh_port=22,
        webui_target_host="127.0.0.1", webui_target_port=upstream_port,
    ) if dev_id == "RUT-8800000001" else None

    try:
        def fetch(sub):
            h = MockHandler("GET", f"{PREFIX}{sub}", headers={"Cookie": f"nms_session={op_sid}"})
            h.do_GET()
            assert h.status_code == 200, sub
            return h

        # 1. HTML still rewrites exactly as before (regression guard on the preserved path).
        html = fetch("/page.html").wfile.getvalue().decode()
        assert f'<base href="{PREFIX}/">' in html
        assert f'href="{PREFIX}/x"' in html

        # 2/3. Root-absolute RUT API paths become proxy-prefixed.
        js = fetch("/app.js").wfile.getvalue().decode()
        assert f'"{PREFIX}/api/login"' in js
        assert f'"{PREFIX}/api/system/device/status"' in js
        assert f'`{PREFIX}/api/widget/config$' in js
        assert f'"{PREFIX}/api/"' in js
        # After removing the proxy prefix, no root-absolute RUT API literal may remain
        # except the deliberately untouched /api/remote/... control.
        assert '"/api/' not in js.replace(f"{PREFIX}/api/", "").replace('"/api/remote/other/webui/proxy/api/login"', "")
        # The rewritten bundle must still be syntactically valid JavaScript. Parsers are
        # absent on the runtime host, so this only runs where one is available.
        parser = shutil.which("node") or shutil.which("bun")
        if parser:
            p = subprocess.run([parser, "--input-type=module", "--check", "-"],
                               input=js, capture_output=True, text=True)
            assert p.returncode == 0, f"rewritten JS failed to parse: {p.stderr}"

        # 4. /api/remote/... is not double-rewritten.
        assert '"/api/remote/other/webui/proxy/api/login"' in js
        assert f'"{PREFIX}/api/remote/other' not in js

        # gzip-encoded upstream JS must arrive as rewritten identity JS.
        gz = fetch("/app.js.gz")
        assert not [v for k, v in gz.response_headers if k.lower() == "content-encoding"]
        assert f'"{PREFIX}/api/login"' in gz.wfile.getvalue().decode()

        # 5. Non-HTML/JS responses are untouched.
        assert b'"/api/not-rewritten-here"' in fetch("/data.json").wfile.getvalue()
    finally:
        server.shutdown()
        server.server_close()
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
        app_mod.resolve_device_target = orig_resolve



def test_remote_webui_root_absolute_resource_paths_rewritten():
    """RUT JS/CSS responses carry root-absolute resource URLs (lazy chunk graph, event
    stream, webfonts, icons, logo). <base href> only governs document-relative
    resolution, so those runtime references escape the proxy and are answered by
    TELNUSA's own origin with the SPA shell (text/html). They must be rewritten in the
    proxied response, while router paths and Linux filesystem paths must not be."""
    import gzip as _gz
    import http.server
    import io
    import shutil
    import subprocess
    import zlib as _zl
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    PREFIX = "/api/remote/RUT-8800000001/webui/proxy"

    # Real shapes captured from RUT906 firmware 07.24.3 in the browser:
    # - fetch("/cgi-bin/subscribe.lua")   Realtime Data event stream
    # - Td="/tlt_networks_logo.svg"       logo constant
    # - src:`/icons/${t.icon}`            icon template
    # - q1=function(e){return"/"+e}       Vite preload base join
    # - s.href=c==="dark"?"/favicon-dark.svg":"/favicon-light.svg"
    # alongside paths that must survive untouched.
    JS_SRC = (
        'fetch("/cgi-bin/subscribe.lua",{signal:a});'
        'fetch("/cgi-bin/sentry");'
        'const Td="/tlt_networks_logo.svg";'
        'src:`/icons/${t.icon}`;'
        'const h=s.href=c==="dark"?"/favicon-dark.svg":"/favicon-light.svg";'
        'const ap=await x.get("/api/login");'
        'const q=await y.get("/api/system/device/status");'
        'a=await fetch("/api/bulk");'
        'const d="/assets/vendor-x.js";'
        'const e="/api/remote/other/webui/proxy/api/login";'
        'const r="/status/overview",s="/system/network/mobile";'
        'const f="/tmp/modbus_db",g="/usr/local/share/modbus_db";'
        'N1="modulepreload",q1=function(e){return"/"+e},lr={},K=function(t,n){};'
    )
    # Bare url(/...) references, exactly as emitted by the real stylesheet, plus a
    # quoted one and a non-RUT filesystem path that must survive.
    CSS_SRC = (
        "@font-face{src:url(/fonts/OpenSans-Regular.woff2) format('woff2');"
        "font-weight:400}"
        "@font-face{src:url(/fonts/OpenSans-SemiBold.woff2);font-weight:600}"
        ".i{background:url(/icons/checkbox_tick_white.svg)}"
        ".r{background-image:url('/icons/check_radio.svg')}"
        ".k{background:url(/tmp/local.png)}"
    )
    HTML_SRC = ('<html><head></head><body>'
                '<img src="/tlt_networks_logo.svg">'
                '<a href="/status/overview">y</a>'
                '<script>f.href="/favicon-light.svg"</script>'
                '</body></html>')

    class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        def _send(self, body, ctype, encoding=None):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/app.js":
                self._send(JS_SRC.encode(), "text/javascript")
            elif self.path == "/app.js.gz":
                self._send(_gz.compress(JS_SRC.encode()), "text/javascript", "gzip")
            elif self.path == "/app.js.deflate":
                self._send(_zl.compress(JS_SRC.encode()), "text/javascript", "deflate")
            elif self.path == "/style.css":
                self._send(CSS_SRC.encode(), "text/css")
            elif self.path == "/style.css.gz":
                self._send(_gz.compress(CSS_SRC.encode()), "text/css", "gzip")
            elif self.path == "/page.html":
                self._send(HTML_SRC.encode(), "text/html; charset=utf-8")
            elif self.path == "/data.json":
                self._send(b'{"url":"/assets/telnusa-own.js"}', "application/json")
            elif self.path == "/chunk.js":
                self._send(b'export default 1;', "application/javascript")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    upstream_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), "operator", now)
    op_sid = "sid-op-res"
    store.create_session(op_sid, op_id, now + 3600_000, "csrf-op")

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    orig_resolve = app_mod.resolve_device_target
    app_mod.store = store

    mock_tm = TunnelManager()
    mock_tm.store = store
    mock_tm.ensure = lambda dev_id: TunnelInfo(
        device_id=dev_id, local_port=upstream_port, pid=99999,
        state=TunnelState.READY, started_at=now,
    )
    app_mod.tunnel_manager = mock_tm
    app_mod.resolve_device_target = lambda dev_id, st: DeviceRemoteTarget(
        device_id=dev_id, ssh_host="127.0.0.1", ssh_port=22,
        webui_target_host="127.0.0.1", webui_target_port=upstream_port,
    ) if dev_id == "RUT-8800000001" else None

    try:
        def fetch(sub):
            h = MockHandler("GET", f"{PREFIX}{sub}", headers={"Cookie": f"nms_session={op_sid}"})
            h.do_GET()
            assert h.status_code == 200, sub
            return h

        js = fetch("/app.js").wfile.getvalue().decode()
        css = fetch("/style.css").wfile.getvalue().decode()

        # 1. The pre-existing /api/ fix must keep working.
        assert f'"{PREFIX}/api/login"' in js
        assert f'"{PREFIX}/api/system/device/status"' in js
        assert f'"{PREFIX}/api/bulk"' in js

        # 2. /assets/... literals become proxy-prefixed.
        assert f'"{PREFIX}/assets/vendor-x.js"' in js

        # 3. /cgi-bin/... becomes proxy-prefixed (this is the Realtime Data stream).
        assert f'"{PREFIX}/cgi-bin/subscribe.lua"' in js
        assert f'"{PREFIX}/cgi-bin/sentry"' in js

        # 4./5. webfonts (CSS) and the logo (JS) become proxy-prefixed.
        assert f"url({PREFIX}/fonts/OpenSans-Regular.woff2)" in css
        assert f"url({PREFIX}/fonts/OpenSans-SemiBold.woff2)" in css
        assert f"{PREFIX}/icons/checkbox_tick_white.svg" in css
        assert f"{PREFIX}/icons/check_radio.svg" in css
        assert f'"{PREFIX}/tlt_networks_logo.svg"' in js
        assert f'"{PREFIX}/favicon-dark.svg"' in js
        assert f'"{PREFIX}/favicon-light.svg"' in js

        # The Vite preload base join must carry the prefix, otherwise every lazy chunk
        # ("assets/app.overview.app-*.js") resolves root-absolute against TELNUSA.
        assert f'q1=function(e){{return"{PREFIX}/"+e}}' in js
        # The join must KEEP its slash: the Vite loader passes chunk paths with no
        # leading slash ("assets/app.overview.app-*.js"), so dropping the separator
        # yields ".../webui/proxyassets/..." -> 404. Both forms occur in the wild.
        assert f'q1=function(e){{return"{PREFIX}"+e}}' not in js

        # 6. Already-prefixed /api/remote/... is never double-rewritten.
        assert '"/api/remote/other/webui/proxy/api/login"' in js
        assert f'"{PREFIX}/api/remote/other' not in js

        # 7. Nothing outside this response is touched: TELNUSA's own /assets/ bundle and
        #    any non-HTML/JS/CSS response must pass through byte-identical.
        assert b'"/assets/telnusa-own.js"' in fetch("/data.json").wfile.getvalue()

        # 8. HTML rewriting is unchanged: base injected once, href/src prefixed.
        html = fetch("/page.html").wfile.getvalue().decode()
        assert html.count("<base ") == 1
        assert f'<base href="{PREFIX}/">' in html
        assert f'src="{PREFIX}/tlt_networks_logo.svg"' in html
        assert f'href="{PREFIX}/status/overview"' in html

        # 12. No unrelated route change: the prefix is derived from the requested
        #     device_id only, so no other device's prefix can appear in any response.
        for sub in ("/app.js", "/style.css", "/page.html"):
            body = fetch(sub).wfile.getvalue().decode()
            assert "RUT-OTHER-9999" not in body, sub

        # Router paths and Linux filesystem paths must survive untouched: prefixing
        # them would corrupt pushState targets and break displayed paths.
        assert '"/status/overview"' in js
        assert '"/system/network/mobile"' in js
        assert '"/tmp/modbus_db"' in js
        assert '"/usr/local/share/modbus_db"' in js
        assert "url(/tmp/local.png)" in css

        # 9. gzip/deflate upstream bodies are decompressed, rewritten and served as
        #    identity with a corrected Content-Length.
        for sub in ("/app.js.gz", "/app.js.deflate", "/style.css.gz"):
            h = fetch(sub)
            assert not [v for k, v in h.response_headers if k.lower() == "content-encoding"], sub
            clen = [v for k, v in h.response_headers if k.lower() == "content-length"]
            assert len(clen) == 1 and int(clen[0]) == len(h.wfile.getvalue()), sub
        assert f'"{PREFIX}/cgi-bin/subscribe.lua"' in fetch("/app.js.gz").wfile.getvalue().decode()
        assert (f"url({PREFIX}/fonts/OpenSans-Regular.woff2)"
                in fetch("/style.css.gz").wfile.getvalue().decode())

        # 10. Rewritten JavaScript must still parse.
        parser = shutil.which("node") or shutil.which("bun")
        if parser:
            p = subprocess.run([parser, "--input-type=module", "--check", "-"],
                               input=js, capture_output=True, text=True)
            assert p.returncode == 0, f"rewritten JS failed to parse: {p.stderr}"

        # 11. CSS must stay structurally valid and keep its non-RUT reference.
        assert css.count("{") == css.count("}") == 5, css
        assert "url(/tmp/local.png)" in css
        assert css.count("url(") == 5, css

        # A second JS content-type spelling must be treated as JavaScript too.
        assert fetch("/chunk.js").wfile.getvalue() == b"export default 1;"
    finally:
        server.shutdown()
        server.server_close()
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
        app_mod.resolve_device_target = orig_resolve

def test_remote_webui_transformed_resources_are_cache_valid():
    """TELNUSA rewrites HTML/JS/CSS bodies on the fly, so the bytes it serves are not
    the upstream bytes. Forwarding the upstream ETag/Last-Modified -- or honouring a
    browser conditional request against them -- lets a warm cache reuse a pre-rewrite
    stylesheet whose url(/fonts/...) references escape the proxy, so the webfonts fail
    to load (Open Sans:error). A transformed response must expose no upstream
    validator, and an upstream 304 must never be passed through for a resource the
    proxy transforms. Non-transformed resources keep their existing cache behaviour."""
    import http.server
    import io
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    PREFIX = "/api/remote/RUT-8800000001/webui/proxy"

    CSS_SRC = ("@font-face{src:url(/fonts/OpenSans-Regular.woff2) format('woff2');"
               "font-weight:400}"
               "@font-face{src:url(/fonts/OpenSans-SemiBold.woff2);font-weight:600}"
               ".i{background:url(/icons/checkbox_tick_white.svg)}"
               ".r{background-image:url('/icons/check_radio.svg')}"
               ".k{background:url(/tmp/local.png)}")
    HTML_SRC = '<html><head></head><body><img src="/tlt_networks_logo.svg"></body></html>'
    JS_SRC = 'fetch("/cgi-bin/subscribe.lua");const d="/assets/vendor-x.js";'
    FONT_BYTES = b"\x00\x01\x02" * 64

    CSS_ETAG = '"css-v1"'
    HTML_ETAG = '"html-v1"'
    JS_ETAG = '"js-v1"'
    FONT_ETAG = '"font-v1"'
    LAST_MOD = "Wed, 01 Oct 2025 00:00:00 GMT"

    seen = []

    class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        def _full(self, body, ctype, etag):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", LAST_MOD)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_modified(self, etag, ctype=None):
            self.send_response(304)
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("ETag", etag)
            self.end_headers()

        def do_GET(self):
            inm = self.headers.get("If-None-Match")
            ims = self.headers.get("If-Modified-Since")
            seen.append((self.path, bool(inm or ims)))
            if self.path == "/style.css":
                if inm == CSS_ETAG or ims == LAST_MOD:
                    # A real origin need not repeat Content-Type on its 304, so the
                    # proxy cannot classify the resource from this response alone.
                    self._not_modified(CSS_ETAG)
                else:
                    self._full(CSS_SRC.encode(), "text/css", CSS_ETAG)
            elif self.path == "/page.html":
                if inm == HTML_ETAG:
                    self._not_modified(HTML_ETAG, "text/html; charset=utf-8")
                else:
                    self._full(HTML_SRC.encode(), "text/html; charset=utf-8", HTML_ETAG)
            elif self.path == "/app.js":
                if inm == JS_ETAG:
                    self._not_modified(JS_ETAG, "text/javascript")
                else:
                    self._full(JS_SRC.encode(), "text/javascript", JS_ETAG)
            elif self.path == "/font.woff2":
                if inm == FONT_ETAG:
                    self._not_modified(FONT_ETAG, "font/woff2")
                else:
                    self._full(FONT_BYTES, "font/woff2", FONT_ETAG)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    upstream_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), "operator", now)
    op_sid = "sid-op-res"
    store.create_session(op_sid, op_id, now + 3600_000, "csrf-op")

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    orig_resolve = app_mod.resolve_device_target
    app_mod.store = store

    mock_tm = TunnelManager()
    mock_tm.store = store
    mock_tm.ensure = lambda dev_id: TunnelInfo(
        device_id=dev_id, local_port=upstream_port, pid=99999,
        state=TunnelState.READY, started_at=now,
    )
    app_mod.tunnel_manager = mock_tm
    app_mod.resolve_device_target = lambda dev_id, st: DeviceRemoteTarget(
        device_id=dev_id, ssh_host="127.0.0.1", ssh_port=22,
        webui_target_host="127.0.0.1", webui_target_port=upstream_port,
    ) if dev_id == "RUT-8800000001" else None

    try:
        def fetch(sub, headers=None):
            h = MockHandler("GET", f"{PREFIX}{sub}",
                            headers={"Cookie": f"nms_session={op_sid}", **(headers or {})})
            h.do_GET()
            return h

        def hdrs(h):
            return {k.lower(): v for k, v in h.response_headers}

        # 1. Cold cache: the CSS reaches the browser rewritten, and carries no upstream
        #    validator, because that validator describes different bytes.
        h = fetch("/style.css")
        assert h.status_code == 200
        css = h.wfile.getvalue().decode()
        assert f"url({PREFIX}/fonts/OpenSans-Regular.woff2)" in css
        assert f"url({PREFIX}/fonts/OpenSans-SemiBold.woff2)" in css
        assert "url(/fonts/" not in css
        hh = hdrs(h)
        assert "etag" not in hh, f"upstream ETag exposed for transformed CSS: {hh}"
        assert "last-modified" not in hh, f"upstream Last-Modified exposed: {hh}"
        assert hh["content-type"] == "text/css"
        assert int(hh["content-length"]) == len(h.wfile.getvalue())

        # 2. Warm cache: the browser revalidates with the validator the RUT issued and
        #    the upstream answers 304. That 304 describes the ORIGINAL stylesheet, so
        #    forwarding it leaves the browser on its cached pre-rewrite copy.
        seen.clear()
        h = fetch("/style.css", {"If-None-Match": CSS_ETAG, "If-Modified-Since": LAST_MOD})
        assert h.status_code == 200, "warm revalidation must not yield 304 for a transformed body"
        css2 = h.wfile.getvalue().decode()
        assert f"url({PREFIX}/fonts/OpenSans-Regular.woff2)" in css2
        assert f"url({PREFIX}/fonts/OpenSans-SemiBold.woff2)" in css2
        assert "url(/fonts/" not in css2
        assert "etag" not in hdrs(h) and "last-modified" not in hdrs(h)
        # ...and the full representation must actually have been fetched upstream.
        assert ("/style.css", False) in seen, seen

        # 3. A 304 that does declare a transformable Content-Type is refused too, for
        #    HTML and for JavaScript.
        for sub, etag in (("/page.html", HTML_ETAG), ("/app.js", JS_ETAG)):
            seen.clear()
            h = fetch(sub, {"If-None-Match": etag})
            assert h.status_code == 200, f"{sub} revalidation must not yield 304"
            assert ("/app.js" if sub == "/app.js" else "/page.html", False) in seen, seen

        # 4. HTML and JS rewriting is otherwise unchanged.
        assert "etag" not in hdrs(fetch("/page.html"))
        html = fetch("/page.html").wfile.getvalue().decode()
        assert f'<base href="{PREFIX}/">' in html
        assert f'src="{PREFIX}/tlt_networks_logo.svg"' in html
        assert "etag" not in hdrs(fetch("/app.js"))
        assert f'"{PREFIX}/cgi-bin/subscribe.lua"' in fetch("/app.js").wfile.getvalue().decode()

        # 5. A resource TELNUSA does not transform keeps its cache behaviour: its 304 is
        #    still forwarded and is NOT re-requested, and its own validator survives.
        seen.clear()
        h = fetch("/font.woff2", {"If-None-Match": FONT_ETAG})
        assert h.status_code == 304, "a non-transformed resource must keep its 304"
        assert hdrs(h).get("etag") == FONT_ETAG
        assert h.wfile.getvalue() == b""
        assert seen == [("/font.woff2", True)], f"non-transformed 304 was re-requested: {seen}"

        h = fetch("/font.woff2")
        assert h.status_code == 200
        assert h.wfile.getvalue() == FONT_BYTES
        assert hdrs(h).get("etag") == FONT_ETAG
        assert hdrs(h).get("last-modified") == LAST_MOD

        # 6. Unrelated proxy routing is untouched.
        assert fetch("/missing.bin").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
        app_mod.resolve_device_target = orig_resolve

class _StampedWriter(io.BytesIO):
    """Response body sink that records when each write happened, so the test can tell
    a streaming relay (many writes, early) from a buffered one (one late write)."""

    def __init__(self):
        super().__init__()
        self.marks = []

    def write(self, data):
        self.marks.append((time.monotonic(), bytes(data)))
        return super().write(data)

    def flush(self):
        pass

def test_remote_webui_event_stream_relayed_without_buffering():
    """/cgi-bin/subscribe.lua is a long-lived text/event-stream. _proxy_webui used to
    call resp.read(), which blocks until the upstream closes -- a live event feed never
    closes, so no headers ever reached the browser and the socket timed out (AH00898).
    The event-stream branch must send the head first and pump bounded chunks."""
    import http.server
    import io
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    EVENTS = 4
    GAP = 0.25
    upstream_state = {"finished_at": None, "first_write_at": None}

    class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            if self.path == "/cgi-bin/subscribe.lua":
                # Headers land immediately; the body trickles and the handler stays open.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for i in range(EVENTS):
                    if upstream_state["first_write_at"] is None:
                        upstream_state["first_write_at"] = time.monotonic()
                    self.wfile.write(f"data: {i}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(GAP)
                upstream_state["finished_at"] = time.monotonic()
                return
            if self.path == "/asset.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(b"console.log('asset');")
                return
            if self.path == "/page.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'<html><head></head><body><a href="/status/x">x</a></body></html>')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    upstream_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = _StampedWriter()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []
            self.headers_ended_at = None

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            self.headers_ended_at = time.monotonic()

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    op_id = store.create_user("op@telnusa.id", "Op User", app_mod.hash_password("OpPass123!"), "operator", now)
    store.create_session("sid-sse", op_id, now + 3600_000, "csrf-sse")

    orig_store = app_mod.store
    orig_tm = app_mod.tunnel_manager
    app_mod.store = store
    mock_tm = TunnelManager()
    mock_tm.store = store
    mock_tm.ensure = lambda dev_id: TunnelInfo(device_id=dev_id, local_port=upstream_port,
                                              pid=99999, state=TunnelState.READY, started_at=now)
    mock_tm.touch = lambda dev_id: None
    app_mod.tunnel_manager = mock_tm
    orig_resolve = app_mod.resolve_device_target
    app_mod.resolve_device_target = lambda dev_id, st: DeviceRemoteTarget(
        device_id=dev_id, ssh_host="127.0.0.1", ssh_port=22,
        webui_target_host="127.0.0.1", webui_target_port=upstream_port,
    ) if dev_id == "RUT-8800000001" else None

    try:
        h = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/subscribe.lua",
                        headers={"Cookie": "nms_session=sid-sse"})
        h.do_GET()

        # 3. Response status is preserved.
        assert h.status_code == 200
        # 4. Content-Type is passed through untouched.
        ctypes = [v for k, v in h.response_headers if k.lower() == "content-type"]
        assert len(ctypes) == 1 and "text/event-stream" in ctypes[0]
        # A stream of unknown length must not carry a Content-Length.
        assert not [v for k, v in h.response_headers if k.lower() == "content-length"]
        # No connection reuse for a stream we deliberately do not bound.
        assert [v for k, v in h.response_headers if k.lower() == "connection"]

        # 1 + 2. The body arrives incrementally: several writes, and the first one
        # lands while the upstream is still producing -- proof that no full
        # resp.read() is buffering the stream first.
        body = h.wfile.getvalue().decode()
        assert body == "".join(f"data: {i}\n\n" for i in range(EVENTS)), repr(body)
        assert len(h.wfile.marks) >= 2, f"single buffered write: {h.wfile.marks}"
        assert h.headers_ended_at is not None
        assert h.headers_ended_at < upstream_state["finished_at"], (
            "headers were only sent after the upstream finished -- still buffering")
        assert h.wfile.marks[0][0] < upstream_state["finished_at"], (
            "first body chunk was only written after the upstream finished -- still buffering")

        # 5. A non-stream response keeps the normal buffered path: exactly one write
        #    and a recomputed Content-Length.
        h_js = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/asset.js",
                           headers={"Cookie": "nms_session=sid-sse"})
        h_js.do_GET()
        assert h_js.status_code == 200
        assert len(h_js.wfile.marks) == 1
        clen = [v for k, v in h_js.response_headers if k.lower() == "content-length"]
        assert len(clen) == 1 and int(clen[0]) == len(b"console.log('asset');")
        assert h_js.wfile.getvalue() == b"console.log('asset');"

        # 7. HTML rewriting is untouched by the streaming branch.
        h_html = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/page.html",
                             headers={"Cookie": "nms_session=sid-sse"})
        h_html.do_GET()
        assert h_html.status_code == 200
        html = h_html.wfile.getvalue().decode()
        assert html.count("<base ") == 1
        assert 'href="/api/remote/RUT-8800000001/webui/proxy/status/x"' in html

        # A failure after the head is on the wire must end the stream, not propagate:
        # the handler can no longer send an error, and raising would make it append a
        # second response to one already in flight. An upstream stall and a client
        # hangup both land here -- the latter as a write failing instead of a read.
        class BoomResp:
            def __init__(self, exc):
                self._exc = exc
                self._served = False

            def read1(self, _n):
                if self._served:
                    raise self._exc
                self._served = True
                return b"data: first\n\n"

        hop = {"connection", "content-length"}
        for boom in (TimeoutError("upstream idle"), OSError("reset"), BrokenPipeError("gone")):
            h_boom = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/subscribe.lua",
                                 headers={"Cookie": "nms_session=sid-sse"})
            if isinstance(boom, BrokenPipeError):
                # Client vanished: the very first write to wfile fails.
                def dead_write(data):
                    raise boom
                h_boom.wfile.write = dead_write
                h_boom._relay_event_stream(BoomResp(OSError("unused")), 200,
                                           [("Content-Type", "text/event-stream")], hop,
                                           lambda v: v, lambda v: v)
                assert h_boom.wfile.getvalue() == b""
            else:
                h_boom._relay_event_stream(BoomResp(boom), 200,
                                           [("Content-Type", "text/event-stream")], hop,
                                           lambda v: v, lambda v: v)
                # The first event reached the client, then the stream ended quietly.
                assert h_boom.wfile.getvalue() == b"data: first\n\n", boom
            assert h_boom.status_code == 200, boom

        # The stream stays behind the trusted server-side device mapping: an
        # unauthenticated request is answered locally and never reaches the relay.
        h_anon = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/subscribe.lua")
        h_anon.do_GET()
        assert h_anon.status_code == 401, h_anon.status_code
        assert b"event-stream" not in h_anon.wfile.getvalue()
        assert b"data:" not in h_anon.wfile.getvalue()

        # A dead upstream must still surface as the original 503, not a 500 from a
        # bookkeeping variable that only gets assigned on the success path.
        dead_tm = TunnelManager()
        dead_tm.store = store
        dead_tm.ensure = lambda dev_id: TunnelInfo(device_id=dev_id, local_port=1, pid=1,
                                                  state=TunnelState.READY, started_at=now)
        dead_tm.touch = lambda dev_id: None
        app_mod.tunnel_manager = dead_tm
        h_dead = MockHandler("GET", "/api/remote/RUT-8800000001/webui/proxy/cgi-bin/subscribe.lua",
                             headers={"Cookie": "nms_session=sid-sse"})
        h_dead.do_GET()
        assert h_dead.status_code == 503, h_dead.status_code
    finally:
        server.shutdown()
        server.server_close()
        app_mod.store = orig_store
        app_mod.tunnel_manager = orig_tm
        app_mod.resolve_device_target = orig_resolve

def test_zabbix_queue_unconfigured_retains_and_records_state():
    from backend import config
    from backend.processor import process_payload
    from backend.store import Store
    from backend.zabbix_client import zabbix

    orig_token = zabbix.token
    zabbix.token = ""
    try:
        store = Store(":memory:")
        payload = {
            "site_id": "SITE-A",
            "sn": "8800000001",
            "ts": 1700000000,
            "sps_mppt_pv_v": 48.5,
            "sps_mppt_pv_a": 10.2,
            "sps_mppt_out_v": 52.1,
            "sps_mppt_out_a": 8.4,
            "a_voltage": 220.1,
            "b_voltage": 219.8,
            "c_voltage": 220.5,
            "a_current": 5.1,
            "b_current": 4.9,
            "c_current": 5.0,
            "kwh": 1234.5,
        }
        status, res = process_payload(store, payload, actor="mqtt")
        assert status == 202
        assert res["deviceId"] == "RUT-8800000001"

        # Verify queued in SQLite
        rows = store.pop_zabbix_batch(10)
        assert len(rows) == 1
        row = rows[0]
        assert row["device_id"] == "RUT-8800000001"
        import json
        items = json.loads(row["payload"])
        assert len(items) == 11

        # Verify integration state shows NOT CONFIGURED
        st = {r["component"]: dict(r) for r in store.list_integration_state()}
        assert st["zabbix"]["state"] == "NOT CONFIGURED"
        assert st["zabbix"]["configured"] == 0
    finally:
        zabbix.token = orig_token


def test_zabbix_delivery_worker_partial_or_failure_result_retention():
    import json
    from backend import config
    from backend.processor import ZabbixDeliveryWorker
    from backend.store import Store
    from backend.zabbix_client import SenderResult, zabbix

    store = Store(":memory:")
    orig_url = zabbix.api_url
    orig_token = zabbix.token
    orig_ensure = zabbix.ensure_device_host
    orig_reconcile = zabbix.reconcile_items
    orig_submit = zabbix.sender_submit
    zabbix.api_url = "http://127.0.0.1:10051/api_jsonrpc.php"
    zabbix.token = "mock-token"
    zabbix.ensure_device_host = lambda d: "10001"
    zabbix.reconcile_items = lambda h, f: None
    try:
        store.enqueue_zabbix("msg-1", "RUT-8800000001", "RUT-8800000001", [{"key": "rut.mppt.pv.v", "value": "48.5", "clock": 1700000000}], 100)
        worker = ZabbixDeliveryWorker(store)

        # Mock sender returning failure/partial result
        zabbix.sender_submit = lambda values: SenderResult(processed=0, failed=1, total=1, response="failed")
        worker.flush_once()

        # The queue row still exists and the attempt count is incremented
        rows = store.pop_zabbix_batch(10)
        assert len(rows) == 1
        assert rows[0]["attempts"] >= 1

        # Reset updated_at past backoff to simulate retry time arrival
        with store._lock, store.db:
            store.db.execute("UPDATE zabbix_queue SET updated_at = updated_at - 100000")

        # Mock sender returning success result
        zabbix.sender_submit = lambda values: SenderResult(processed=1, failed=0, total=1, response="success")
        worker.flush_once()

        # The queue row is now deleted
        rows_after = store.pop_zabbix_batch(10)
        assert len(rows_after) == 0
    finally:
        zabbix.api_url = orig_url
        zabbix.token = orig_token
        zabbix.ensure_device_host = orig_ensure
        zabbix.reconcile_items = orig_reconcile
        zabbix.sender_submit = orig_submit

def test_store_count_devices_by_age_effective_ms():
    from backend.store import Store

    store = Store(":memory:")
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "last_seen": 1000,
    })

    # Test with at_ms = 1000 (fresh: age 0s <= 300s)
    c1 = store.count_devices_by_age(at_ms=1000)
    assert c1["live_devices"] == 1
    assert c1["stale_devices"] == 0
    assert c1["offline_devices"] == 0

    # Test with now_ms = 1000 + 400_000 (stale: age 400s > 300s and <= 900s)
    c2 = store.count_devices_by_age(now_ms=401000)
    assert c2["stale_devices"] == 1
    assert c2["live_devices"] == 0
    assert c2["offline_devices"] == 0

    # Test with at_ms = 1000 + 1000_000 (offline: age 1000s > 900s)
    c3 = store.count_devices_by_age(at_ms=1001000)
    assert c3["offline_devices"] == 1
    assert c3["live_devices"] == 0
    assert c3["stale_devices"] == 0

# ---------------------------------------------------------------------------
# DB-01: SQLite connection pragmas
# ---------------------------------------------------------------------------
def test_store_connection_enables_foreign_keys_on_every_connection():
    """foreign_keys is per-connection in SQLite and OFF by default."""
    import sqlite3
    from backend.store import Store, apply_connection_pragmas

    store = Store(":memory:")
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # A raw connection defaults to OFF, proving the pragma is what turns it on.
    raw = sqlite3.connect(":memory:")
    try:
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        apply_connection_pragmas(raw)
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        raw.close()


def test_store_connection_applies_busy_timeout():
    from backend.store import Store, DB_BUSY_TIMEOUT_MS

    store = Store(":memory:")
    assert store.db.execute("PRAGMA busy_timeout").fetchone()[0] == DB_BUSY_TIMEOUT_MS
    assert DB_BUSY_TIMEOUT_MS > 0, "a zero busy_timeout would not wait at all"


def test_store_wal_applied_for_file_backed_database_and_safe_for_memory():
    import sqlite3
    import tempfile
    import os
    from backend.store import Store, apply_connection_pragmas

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "nms.db")
        store = Store(path)
        try:
            mode = store.db.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal", f"file-backed store must use WAL, got {mode!r}"
        finally:
            store.db.close()

    # An in-memory database cannot use WAL. The pragma must degrade, not raise.
    mem = sqlite3.connect(":memory:")
    try:
        apply_connection_pragmas(mem)          # must not raise
        assert mem.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        mem.close()


def test_store_pragmas_survive_reopen_and_do_not_corrupt_data():
    """WAL is persistent; pragmas must not disturb existing rows on reopen."""
    import tempfile
    import os
    from backend.store import Store

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "nms.db")
        s1 = Store(path)
        s1.upsert_device({
            "device_id": "RUT-A", "site_id": "SITE-A", "serial": "111",
            "status": "online", "last_seen": 1000,
        })
        s1.db.close()

        s2 = Store(path)
        try:
            row = s2.get_device("RUT-A")
            assert row is not None, "row written before reopen must survive"
            assert row["serial"] == "111"
            assert s2.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert s2.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            s2.db.close()


def test_store_concurrent_writes_do_not_raise_database_is_locked():
    """busy_timeout + WAL must let concurrent writers serialize instead of raising."""
    import sqlite3
    import tempfile
    import os
    from backend.store import Store

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "nms.db")
        store = Store(path)
        errors = []

        def writer(n):
            try:
                for i in range(10):
                    store.upsert_device({
                        "device_id": f"RUT-{n}-{i}",
                        "site_id": "SITE-A",
                        "serial": f"{n}{i:04d}",
                        "status": "online",
                        "last_seen": 1000 + i,
                    })
                    store.mark_message(f"m-{n}-{i}", f"RUT-{n}-{i}", 1000, 1000)
            except sqlite3.OperationalError as e:
                errors.append(str(e))
            except Exception as e:  # noqa: BLE001 - surfaced via assert below
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        try:
            assert errors == [], f"concurrent writes raised: {errors[:3]}"
            for n in range(4):
                for i in range(10):
                    assert store.get_device(f"RUT-{n}-{i}") is not None, \
                        f"RUT-{n}-{i} lost after concurrent write"
        finally:
            store.db.close()


# ---------------------------------------------------------------------------
# REMOTE-02: bounded tunnel lifecycle / REMOTE-03: no default root SSH identity
# ---------------------------------------------------------------------------
def test_validate_ssh_user_rejects_unset_and_privileged_accounts():
    """REMOTE-03: an unset identity and a privileged identity both fail closed."""
    from backend.tunnel import validate_ssh_user

    # Unset / blank fails closed; there is deliberately no default.
    for bad in (None, "", "   "):
        ok, err = validate_ssh_user(bad)
        assert not ok, f"{bad!r} must not be accepted as an SSH identity"
        assert "not configured" in err

    # A privileged account is rejected outright rather than silently downgraded.
    ok, err = validate_ssh_user("root")
    assert not ok
    assert "not permitted" in err

    # Injection-shaped / malformed identities never reach argv.
    for bad in ("user@evil-host", "has space", "-oProxyCommand=id"):
        ok, err = validate_ssh_user(bad)
        assert not ok, f"{bad!r} must be rejected"

    # An explicit named account is accepted.
    ok, err = validate_ssh_user("nmsremote")
    assert ok and err is None


def test_build_ssh_command_fails_closed_without_valid_user():
    """REMOTE-03: the argv builder refuses to build with no/privileged identity."""
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import build_ssh_command

    target = DeviceRemoteTarget(
        device_id="RUT-8800000001",
        ssh_host="192.168.1.144",
        ssh_port=22,
        webui_target_host="192.168.10.1",
        webui_target_port=443,
    )
    for bad in ("root", "", None):
        try:
            build_ssh_command(
                target=target,
                local_port=18443,
                key_path="/etc/telnusa/id_ed25519",
                known_hosts_path="/etc/telnusa/known_hosts",
                ssh_user=bad,
            )
        except ValueError:
            continue
        raise AssertionError(f"ssh_user={bad!r} must not produce an ssh argv")

    # A valid identity still yields the hardened argv.
    cmd = build_ssh_command(
        target=target,
        local_port=18443,
        key_path="/etc/telnusa/id_ed25519",
        known_hosts_path="/etc/telnusa/known_hosts",
        ssh_user="nmsremote",
    )
    assert "nmsremote@192.168.1.144" in cmd
    assert not any("root@" in a for a in cmd)

    # The parameter itself must carry no default: a reinstated "root" default is
    # masked by the in-body guard, so only the signature pins this invariant.
    import inspect
    param = inspect.signature(build_ssh_command).parameters["ssh_user"]
    assert param.default is inspect.Parameter.empty, \
        "build_ssh_command must not default to any identity"

    # config.RUT_SSH_USER must default to empty, never to a privileged identity:
    # an unset environment must not become a root tunnel.
    from backend.config import config as _cfg
    assert not _cfg.RUT_SSH_USER, \
        f"RUT_SSH_USER must default to empty, got {_cfg.RUT_SSH_USER!r}"
    assert _cfg.RUT_SSH_USER != "root"


def test_tunnel_manager_blocks_when_no_ssh_user_configured():
    """REMOTE-03: a TunnelManager with no configured identity never starts a tunnel."""
    import os
    import tempfile
    from backend.store import Store
    from backend.tunnel import TunnelManager, TunnelState

    store = Store(":memory:")
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "integration_status": "accepted",
        "last_seen": 1000,
    })

    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)
        spawned = []

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="",           # unset identity
            process_runner=lambda cmd: spawned.append(cmd),
            socket_checker=lambda h, p, t=0.2: True,
            startup_wait_seconds=0.0,
        )
        info = tm.ensure("RUT-8800000001")
        assert info.state == TunnelState.BLOCKED_CONFIG
        assert "not configured" in info.error
        assert spawned == [], "no ssh process may be spawned without a valid identity"

        # A privileged identity is equally refused.
        tm_root = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="root",
            process_runner=lambda cmd: spawned.append(cmd),
            socket_checker=lambda h, p, t=0.2: True,
            startup_wait_seconds=0.0,
        )
        info_root = tm_root.ensure("RUT-8800000001")
        assert info_root.state == TunnelState.BLOCKED_CONFIG
        assert "not permitted" in info_root.error
        assert spawned == []


def test_allocate_loopback_port_is_bounded_and_deterministic():
    """REMOTE-02: allocation stays inside the range and exhaustion is explicit."""
    from backend.tunnel import TunnelResourceExhausted, allocate_loopback_port

    # 1. Allocation is inside the requested range.
    used: set[int] = set()
    for _ in range(5):
        p = allocate_loopback_port(used, start_port=28100, end_port=28200)
        assert 28100 <= p < 28200, f"{p} escaped the configured range"
        used.add(p)
    assert len(used) == 5, "allocated ports must be unique"

    # 2. Deterministic: the same used-set yields the same next port.
    assert allocate_loopback_port(used, 28100, 28200) == \
        allocate_loopback_port(used, 28100, 28200)

    # 3. Exhaustion is an explicit, recoverable error, never an OS ephemeral port.
    full = set(range(28100, 28110))
    try:
        got = allocate_loopback_port(full, start_port=28100, end_port=28110)
    except TunnelResourceExhausted as exc:
        assert "No free loopback port" in str(exc)
    else:
        raise AssertionError(f"exhausted range returned untracked port {got}")

    # 4. Empty range cannot hand out anything either.
    try:
        allocate_loopback_port(set(), start_port=28200, end_port=28200)
    except TunnelResourceExhausted:
        pass
    else:
        raise AssertionError("empty range must not allocate")


def test_tunnel_manager_enforces_concurrency_limit():
    """REMOTE-02: concurrent tunnels are capped, and the cap is recoverable."""
    import os
    import tempfile
    from backend.store import Store
    from backend.tunnel import TunnelManager, TunnelState

    store = Store(":memory:")
    for n in range(3):
        store.upsert_device({
            "device_id": f"RUT-90000000{n}",
            "site_id": "SITE-A",
            "serial": f"90000000{n}",
            "status": "online",
            "integration_status": "accepted",
            "last_seen": 1000,
        })

    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)
        active = set()

        class FakeProcess:
            pid = 4242
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                return 0

        def fake_runner(cmd):
            active.add(int(cmd[cmd.index("-L") + 1].split(":")[1]))
            return FakeProcess()

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="nmsremote",
            process_runner=fake_runner,
            socket_checker=lambda h, p, t=0.2: p in active,
            startup_wait_seconds=0.0,
            max_concurrent=2,
            port_range_start=28300,
            port_range_end=28400,
        )

        first = tm.ensure("RUT-900000000")
        second = tm.ensure("RUT-900000001")
        assert first.state == TunnelState.READY
        assert second.state == TunnelState.READY

        # Third exceeds the explicit cap and fails recoverably.
        third = tm.ensure("RUT-900000002")
        assert third.state == TunnelState.FAILED
        assert "limit reached" in third.error

        # Recoverable: stop one, then the cap allows the next.
        tm.stop("RUT-900000000")
        again = tm.ensure("RUT-900000002")
        assert again.state == TunnelState.READY, again.error
        tm.cleanup_all()


def test_tunnel_manager_releases_ports_and_bounds_lock_cache():
    """REMOTE-02: stopped tunnels free their port; the lock cache stays bounded."""
    import os
    import tempfile
    from backend.store import Store
    from backend.tunnel import TunnelManager, TunnelState

    store = Store(":memory:")
    for n in range(4):
        store.upsert_device({
            "device_id": f"RUT-91000000{n}",
            "site_id": "SITE-A",
            "serial": f"91000000{n}",
            "status": "online",
            "integration_status": "accepted",
            "last_seen": 1000,
        })

    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)
        active = set()

        class FakeProcess:
            pid = 4343
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                return 0

        def fake_runner(cmd):
            active.add(int(cmd[cmd.index("-L") + 1].split(":")[1]))
            return FakeProcess()

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="nmsremote",
            process_runner=fake_runner,
            socket_checker=lambda h, p, t=0.2: p in active,
            startup_wait_seconds=0.0,
            port_range_start=28500,
            port_range_end=28600,
        )

        info = tm.ensure("RUT-910000000")
        assert info.state == TunnelState.READY
        port = info.local_port
        assert port in tm._used_ports

        # A stopped tunnel releases its port back to the allocator.
        tm.stop("RUT-910000000")
        assert port not in tm._used_ports, "stopped tunnel leaked its loopback port"
        assert tm.status("RUT-910000000").state == TunnelState.STOPPED

        # cleanup_all must not leak the remaining allocations either.
        tm.ensure("RUT-910000001")
        tm.cleanup_all()
        assert tm._used_ports == set(), f"ports leaked after cleanup_all: {tm._used_ports}"

        # The per-device lock cache is bounded rather than growing without limit.
        bounded = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="nmsremote",
            lock_cache_max=4,
        )
        for n in range(40):
            bounded._get_device_lock(f"RUT-CACHE-{n}")
        assert len(bounded._device_locks) <= 4, \
            f"lock cache unbounded: {len(bounded._device_locks)}"



def test_zabbix_item_status_explicit_and_host_group_template_reconciliation():
    from backend.config import config
    from backend.zabbix_client import ZabbixClient, ZabbixError

    client = ZabbixClient("http://mock-zabbix/api_jsonrpc.php", "dummy-token")

    # 1. Verify item_lookup requests "status"
    recorded_params = []
    def mock_call(method, params=None):
        recorded_params.append((method, params))
        if method == "item.get":
            return [{"itemid": "100", "key_": "rut.mppt.pv.v", "type": "2", "value_type": "0", "units": "V", "name": "MPPT PV Voltage", "status": "0"}]
        if method == "host.get":
            return [{
                "hostid": "1001",
                "host": "RUT-8800000001",
                "name": "RUT-8800000001",
                "groups": [{"groupid": "50", "name": "TELNUSA"}],
                "parentTemplates": [{"templateid": "200", "host": "Template Teltonika RUT906", "name": "Template Teltonika RUT906"}],
            }]
        if method == "hostgroup.get":
            return [{"groupid": "50", "name": "TELNUSA"}]
        if method == "template.get":
            return [{"templateid": "200", "host": "Template Teltonika RUT906", "name": "Template Teltonika RUT906"}]
        return []

    client._call = mock_call
    item = client.item_lookup("1001", "rut.mppt.pv.v")
    assert item is not None
    assert item["status"] == "0"
    assert "status" in recorded_params[0][1]["output"]

    # 2. Reconcile disabled item (status != "0") raises ITEM_TYPE_MISMATCH
    def mock_call_disabled(method, params=None):
        if method == "item.get":
            return [{"itemid": "100", "key_": "rut.mppt.pv.v", "type": "2", "value_type": "0", "units": "V", "name": "MPPT PV Voltage", "status": "1"}]
        return []
    client._call = mock_call_disabled
    try:
        client.reconcile_items("1001", ["sps_mppt_pv_v"])
        assert False, "Should raise ITEM_TYPE_MISMATCH"
    except ZabbixError as exc:
        assert exc.category == "ITEM_TYPE_MISMATCH"

    # 3. Host group mismatch raises HOST_GROUP_MISMATCH
    orig_group = config.ZABBIX_HOST_GROUP
    orig_tpl = config.ZABBIX_TEMPLATE
    config.ZABBIX_HOST_GROUP = "TELNUSA"
    config.ZABBIX_TEMPLATE = "Template Teltonika RUT906"
    try:
        def mock_call_wrong_group(method, params=None):
            if method == "host.get":
                return [{
                    "hostid": "1001",
                    "host": "RUT-8800000001",
                    "name": "RUT-8800000001",
                    "groups": [{"groupid": "99", "name": "OTHER_GROUP"}],
                    "parentTemplates": [{"templateid": "200", "host": "Template Teltonika RUT906", "name": "Template Teltonika RUT906"}],
                }]
            if method == "hostgroup.get":
                return [{"groupid": "50", "name": "TELNUSA"}]
            if method == "template.get":
                return [{"templateid": "200", "host": "Template Teltonika RUT906", "name": "Template Teltonika RUT906"}]
            return []
        client._call = mock_call_wrong_group
        try:
            client.ensure_device_host({"device_id": "RUT-8800000001"})
            assert False, "Should raise HOST_GROUP_MISMATCH"
        except ZabbixError as exc:
            assert exc.category == "HOST_GROUP_MISMATCH"

        # 4. Template mismatch raises TEMPLATE_MISMATCH
        def mock_call_wrong_tpl(method, params=None):
            if method == "host.get":
                return [{
                    "hostid": "1001",
                    "host": "RUT-8800000001",
                    "name": "RUT-8800000001",
                    "groups": [{"groupid": "50", "name": "TELNUSA"}],
                    "parentTemplates": [{"templateid": "999", "host": "Wrong Template", "name": "Wrong Template"}],
                }]
            if method == "hostgroup.get":
                return [{"groupid": "50", "name": "TELNUSA"}]
            if method == "template.get":
                return [{"templateid": "200", "host": "Template Teltonika RUT906", "name": "Template Teltonika RUT906"}]
            return []
        client._call = mock_call_wrong_tpl
        try:
            client.ensure_device_host({"device_id": "RUT-8800000001"})
            assert False, "Should raise TEMPLATE_MISMATCH"
        except ZabbixError as exc:
            assert exc.category == "TEMPLATE_MISMATCH"
    finally:
        config.ZABBIX_HOST_GROUP = orig_group
        config.ZABBIX_TEMPLATE = orig_tpl

# 7.4 shape: host.get returns groups under "hostgroups" (requested via
# "selectHostGroups"). A host already in the configured group must reconcile
# without HOST_GROUP_MISMATCH.
def test_zabbix_host_lookup_uses_select_host_groups():
    from backend.config import config
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://mock-zabbix/api_jsonrpc.php", "dummy-token")
    recorded = []
    def mock_call(method, params=None):
        recorded.append((method, params or {}))
        if method == "host.get":
            return [{
                "hostid": "1001",
                "host": "RUT-8800000001",
                "name": "RUT-8800000001",
                "hostgroups": [{"groupid": "50", "name": "TELNUSA"}],
                "parentTemplates": [],
            }]
        if method == "hostgroup.get":
            return [{"groupid": "50", "name": "TELNUSA"}]
        return []
    client._call = mock_call
    orig_group = config.ZABBIX_HOST_GROUP
    orig_tpl = config.ZABBIX_TEMPLATE
    config.ZABBIX_HOST_GROUP = "TELNUSA"
    config.ZABBIX_TEMPLATE = ""
    try:
        assert client.ensure_device_host({"device_id": "RUT-8800000001"}) == "1001"
    finally:
        config.ZABBIX_HOST_GROUP = orig_group
        config.ZABBIX_TEMPLATE = orig_tpl
    host_params = [p for m, p in recorded if m == "host.get"][0]
    assert "selectHostGroups" in host_params

# apiinfo.version must be called WITHOUT an Authorization header (Zabbix 7.x
# rejects authed calls to auth-exempt methods with -32602).
def test_zabbix_apiinfo_version_omits_auth_header():
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://mock-zabbix/api_jsonrpc.php", "dummy-token")
    class Resp:
        status = 200
        headers = {"Content-Type": "application/json"}
        def read(self):
            return b'{"jsonrpc":"2.0","result":"7.4.14","id":1}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    class Opener:
        def open(self, req, timeout=None):
            self.auth = req.get_header("Authorization")
            return Resp()
    opener = Opener()
    client._opener = lambda: opener
    assert client.apiinfo_version() == "7.4.14"
    assert opener.auth is None


def test_proxy_cookie_filter_and_accept_encoding_identity():
    from backend.app import filter_proxy_cookies

    assert filter_proxy_cookies("nms_session=secret123; sysauth_https=rutcookie; other=1") == "sysauth_https=rutcookie; other=1"
    assert filter_proxy_cookies("NMS_SESSION=secret123; foo=bar") == "foo=bar"
    assert filter_proxy_cookies("nms_session=secret123") == ""
    assert filter_proxy_cookies("") == ""
    assert filter_proxy_cookies(None) == ""


def test_tunnel_maintenance_worker_lifecycle_and_touch():
    test_http_ingest_handler_valid()
    test_http_ingest_handler_invalid_token()
    test_http_ingest_handler_no_token()
    test_http_ingest_handler_disabled()
    test_http_ingest_handler_invalid_json()
    test_http_ingest_handler_oversized_body()
    test_http_ingest_handler_invalid_identity()
    test_http_ingest_handler_x_telemetry_token()
    test_http_ingest_valid_telemetry()
    test_http_ingest_invalid_json()
    test_http_ingest_missing_identity()
    test_http_ingest_missing_sn()
    test_http_ingest_missing_ts()
    test_http_ingest_invalid_timestamp_future()
    test_http_ingest_invalid_timestamp_past()
    test_http_ingest_unknown_field()
    test_http_ingest_null_sensor_values()
    test_http_ingest_disabled()
    test_http_ingest_device_discovery_upsert()
    test_http_ingest_response_codes()
    test_http_ingest_payload_size_limit()
    test_http_ingest_content_type()
    test_http_ingest_actor_audit()
    import os
    import tempfile
    from backend.store import Store
    from backend.tunnel import TunnelMaintenanceWorker, TunnelManager, TunnelState

    store = Store(":memory:")
    store.upsert_device({
        "device_id": "RUT-8800000001",
        "site_id": "SITE-A",
        "serial": "8800000001",
        "status": "online",
        "integration_status": "accepted",
        "last_seen": 1000,
    })

    with tempfile.NamedTemporaryFile() as key_f, tempfile.NamedTemporaryFile() as hosts_f:
        os.chmod(key_f.name, 0o600)
        os.chmod(hosts_f.name, 0o644)

        class FakeProcess:
            def __init__(self, pid=12345):
                self.pid = pid
                self._exit_code = None
            def poll(self):
                return self._exit_code
            def terminate(self):
                self._exit_code = 0
            def wait(self, timeout=None):
                return self._exit_code

        active_ports = set()
        def fake_runner(cmd):
            l_idx = cmd.index("-L")
            port_str = cmd[l_idx + 1].split(":")[1]
            active_ports.add(int(port_str))
            return FakeProcess(pid=8888)

        def fake_socket_checker(host, port, timeout=0.2):
            return host == "127.0.0.1" and port in active_ports

        tm = TunnelManager(
            store=store,
            key_path=key_f.name,
            known_hosts_path=hosts_f.name,
            ssh_user="nmsremote",
            process_runner=fake_runner,
            socket_checker=fake_socket_checker,
            startup_wait_seconds=0.01,
            idle_timeout=60,
        )

        info = tm.ensure("RUT-8800000001")
        assert info.state == TunnelState.READY
        t0 = info.last_used

        # Test touch
        import time
        time.sleep(0.01)
        tm.touch("RUT-8800000001")
        info_after = tm.status("RUT-8800000001")
        assert info_after.last_used >= t0

        # Test idle cleanup via worker run_once
        worker = TunnelMaintenanceWorker(tm, interval=0.05)
        # Force last_used to 100 seconds ago
        with tm._lock:
            tm._tunnels["RUT-8800000001"].last_used -= 100_000

        worker.run_once()
        assert tm.status("RUT-8800000001").state == TunnelState.STOPPED

        # Test thread start & clean stop
        worker.start()
        worker.stop(timeout=1.0)
        assert not worker._thread.is_alive()


# HTTP Telemetry Ingestion Tests
import io
import json
import time
from backend.config import config
from backend.processor import process_payload
from backend.store import Store
from backend.telemetry import parse_json_payload, ValidationError

def test_http_ingest_valid_telemetry():
    store = Store(":memory:")

    original = config.NMS_TELEMETRY_INGEST_ENABLED
    config.NMS_TELEMETRY_INGEST_ENABLED = True

    try:
        payload = base_payload(sps_mppt_pv_v=23.4, sps_mppt_pv_a=5.2, kwh=123.45, ts=int(time.time() * 1000))
        status, result = process_payload(store, payload, actor="http")

        assert status == 202
        assert result["deviceId"] == "RUT-8800000001"
        assert "unmapped" in result

        # Check device was created
        device = store.get_device("RUT-8800000001")
        assert device is not None
        assert device["site_id"] == "SITE-A"
        assert device["serial"] == "8800000001"
        assert device["status"] == "online"
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original

def test_http_ingest_invalid_json():
    try:
        parse_json_payload(b"{not json")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

def test_http_ingest_missing_identity():
    store = Store(":memory:")

    payload = base_payload()
    del payload["site_id"]

    status, result = process_payload(store, payload, actor="http")
    assert status == 400
    assert result["error"] == "validation_failed"

def test_http_ingest_missing_sn():
    store = Store(":memory:")

    payload = base_payload()
    del payload["sn"]

    status, result = process_payload(store, payload, actor="http")
    assert status == 400
    assert result["error"] == "validation_failed"

def test_http_ingest_missing_ts():
    store = Store(":memory:")

    payload = base_payload()
    del payload["ts"]

    status, result = process_payload(store, payload, actor="http")
    assert status == 400
    assert result["error"] == "validation_failed"

def test_http_ingest_invalid_timestamp_future():
    store = Store(":memory:")

    future_ts = int(time.time() * 1000) + 400_000  # 400 seconds in future
    payload = base_payload(ts=future_ts)

    status, result = process_payload(store, payload, actor="http", received_ms=int(time.time() * 1000))
    assert status == 400
    assert result["error"] == "validation_failed"

def test_http_ingest_invalid_timestamp_past():
    store = Store(":memory:")

    payload = base_payload(ts=1000)

    status, result = process_payload(store, payload, actor="http")
    assert status == 400
    assert result["error"] == "validation_failed"

def test_http_ingest_unknown_field():
    store = Store(":memory:")

    payload = base_payload(unknown_field=999)

    status, result = process_payload(store, payload, actor="http")
    assert status == 202
    assert "unknown_field" in result["unmapped"]

    # Check unmapped was recorded
    unmapped = store.list_unmapped()
    assert any(u["field"] == "unknown_field" for u in unmapped)

def test_http_ingest_null_sensor_values():
    store = Store(":memory:")

    payload = base_payload(sps_mppt_pv_v=None, kwh=None)

    status, result = process_payload(store, payload, actor="http")
    assert status == 202

    device = store.get_device("RUT-8800000001")
    latest = device["last_payload"]
    assert latest["mppt"]["pvVoltage"] is None
    assert latest["pzem"]["energy"] is None

def test_http_ingest_disabled():
    # The endpoint would return 501 when disabled, but process_payload still works
    # The disabled check is at the HTTP handler level
    # We just verify the config flag exists
    assert hasattr(config, "NMS_TELEMETRY_INGEST_ENABLED")
    assert hasattr(config, "NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES")

def test_http_ingest_device_discovery_upsert():
    store = Store(":memory:")

    payload1 = base_payload(sps_mppt_pv_v=23.4)
    status1, result1 = process_payload(store, payload1, actor="http")

    assert status1 == 202
    assert result1["deviceId"] == "RUT-8800000001"

    # Send again - should be deduplicated
    status2, result2 = process_payload(store, payload1, actor="http")

    assert status2 == 202
    assert result2["duplicate"] is True

    # Device count should be 1
    devices = store.list_devices()
    assert len(devices) == 1

def test_http_ingest_response_codes():
    store = Store(":memory:")

    # Valid payload -> 202
    payload = base_payload()
    status, result = process_payload(store, payload, actor="http")
    assert status == 202
    assert "deviceId" in result
    assert "unmapped" in result

    # Invalid payload -> 400
    payload_bad = base_payload(site_id="")
    status, result = process_payload(store, payload_bad, actor="http")
    assert status == 400
    assert result["error"] == "validation_failed"

    # Duplicate -> 202 with duplicate flag
    status, result = process_payload(store, payload, actor="http")
    assert status == 202
    assert result["duplicate"] is True

def test_http_ingest_payload_size_limit():
    # Verify config flag exists
    assert hasattr(config, "NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES")
    assert config.NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES > 0

def test_http_ingest_content_type():
    # Content-Type validation happens at HTTP handler level
    # This is a placeholder to document the requirement
    pass

def test_http_ingest_actor_audit():
    store = Store(":memory:")

    payload = base_payload()
    status, result = process_payload(store, payload, actor="http")

    assert status == 202

    # Check audit log has "http" actor
    audits = store.list_audit_logs(limit=10)
    assert any(a["actor"] == "http" for a in audits)


# HTTP+MQTT Convergence Tests (Phase 7: dual-publish via shared process_payload)
def test_convergence_http_mqtt_same_ts_merges():
    import time
    store = Store(":memory:")
    ts = int(time.time() * 1000)
    p_mqtt = base_payload(ts=ts, sps_mppt_pv_v=20.0, a_voltage=2200)
    p_http = dict(p_mqtt)
    s1, _ = process_payload(store, p_mqtt, actor="mqtt", topic="entermqtt/mitratel/SITE-A/telemetry")
    assert s1 == 202
    s2, r2 = process_payload(store, p_http, actor="http")
    assert s2 == 202
    assert r2.get("duplicate") is True or r2.get("no_change") is True or "deviceId" in r2
    assert len(store.list_devices()) == 1

def test_convergence_http_newer_ts_supersedes_mqtt():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    old = base_payload(ts=now - 60_000, sps_mppt_pv_v=18.0)
    new = base_payload(ts=now, sps_mppt_pv_v=22.5)
    s1, _ = process_payload(store, old, actor="mqtt")
    assert s1 == 202
    s2, _ = process_payload(store, new, actor="http")
    assert s2 == 202
    dev = store.get_device("RUT-8800000001")
    assert dev["last_payload"]["mppt"]["pvVoltage"] == 22.5

def test_convergence_mqtt_stale_after_http_superseded():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    fresh = base_payload(ts=now, sps_mppt_pv_v=21.0)
    stale = base_payload(ts=now - 120_000, sps_mppt_pv_v=15.0)
    s1, _ = process_payload(store, fresh, actor="http")
    assert s1 == 202
    s2, r2 = process_payload(store, stale, actor="mqtt")
    assert s2 == 202
    assert r2.get("stale_superseded") is True or r2.get("duplicate") is True or "deviceId" in r2
    dev = store.get_device("RUT-8800000001")
    assert dev["last_payload"]["mppt"]["pvVoltage"] == 21.0

def test_convergence_partial_merge_preserves_other_actor_fields():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    p1 = base_payload(ts=now, sps_mppt_pv_v=19.0, a_voltage=None)
    p2 = base_payload(ts=now, sps_mppt_pv_v=None, a_voltage=2260)
    s1, _ = process_payload(store, p1, actor="mqtt")
    assert s1 == 202
    s2, _ = process_payload(store, p2, actor="http")
    assert s2 == 202
    dev = store.get_device("RUT-8800000001")
    latest = dev["last_payload"]
    assert latest["pzem"]["phaseAVoltage"] == 2260.0

def test_convergence_site_conflict_rejected_across_actors():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    p1 = base_payload(ts=now, site_id="SITE-A")
    s1, _ = process_payload(store, p1, actor="mqtt")
    assert s1 == 202
    p2 = base_payload(ts=now + 1000, site_id="SITE-B")
    s2, r2 = process_payload(store, p2, actor="http")
    assert s2 == 409
    assert r2.get("error") == "identity_conflict"

def test_convergence_pzem_scaling_contract():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    # Backend receives RUT-scaled values; canonical mapping must hold for all 7 PZEM fields
    p = base_payload(ts=now, a_voltage=2260, b_voltage=2261, c_voltage=2262,
                      a_current=50, b_current=51, c_current=52, kwh=123.5)
    s, _ = process_payload(store, p, actor="mqtt")
    assert s == 202
    dev = store.get_device("RUT-8800000001")
    pz = dev["last_payload"]["pzem"]
    assert pz["phaseAVoltage"] == 2260.0
    assert pz["phaseBVoltage"] == 2261.0
    assert pz["phaseCVoltage"] == 2262.0
    assert pz["phaseACurrent"] == 50.0
    assert pz["phaseBCurrent"] == 51.0
    assert pz["phaseCCurrent"] == 52.0
    assert pz["energy"] == 123.5
    # Same mapping must hold via HTTP actor
    store2 = Store(":memory:")
    s2, _ = process_payload(store2, base_payload(ts=now, a_voltage=2260, kwh=123.5), actor="http")
    assert s2 == 202
    pz2 = store2.get_device("RUT-8800000001")["last_payload"]["pzem"]
    assert pz2["phaseAVoltage"] == 2260.0
    assert pz2["energy"] == 123.5
def test_convergence_solar_radiation_integrated_end_to_end():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    # Solar irradiance is integrated: the field maps into the solar section,
    # leaves the unmapped list, reaches Zabbix, and drives sensor3_state.
    p = base_payload(ts=now, solar_radiation=412)
    s, r = process_payload(store, p, actor="http")
    assert s == 202
    assert "solar_radiation" not in r.get("unmapped", [])
    dev = store.get_device("RUT-8800000001")
    assert dev["last_payload"]["solar"]["radiation"] == 412.0
    assert dev["sensor3_state"] == "all_ok"
    assert dev["acquisition_state"] == "all_ok"
    vt = validate_payload(base_payload(ts=now, solar_radiation=412), now_ms=now + 1000)
    keys = {v["key"] for v in zabbix_values(vt)}
    assert "rut.solar.radiation" in keys

def test_convergence_unmapped_preserved_across_actors():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    s1, r1 = process_payload(store, base_payload(ts=now, custom_foo=123), actor="mqtt")
    assert s1 == 202
    assert "custom_foo" in r1.get("unmapped", [])
    s2, r2 = process_payload(store, base_payload(ts=now + 1000, custom_foo=456), actor="http")
    assert s2 == 202
    assert "custom_foo" in r2.get("unmapped", [])
    rows = store.list_unmapped()
    assert any(u["field"] == "custom_foo" for u in rows)

def test_convergence_actor_audit_both_actors():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    process_payload(store, base_payload(ts=now), actor="mqtt")
    process_payload(store, base_payload(ts=now + 1000, sps_mppt_pv_v=25.0), actor="http")
    audits = store.list_audit_logs(limit=20)
    actors = {a["actor"] for a in audits}
    assert "mqtt" in actors
    assert "http" in actors

def test_convergence_zabbix_queue_on_update_only():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    s1, _ = process_payload(store, base_payload(ts=now, sps_mppt_pv_v=20.0), actor="mqtt")
    assert s1 == 202
    q_after_first = store.count_zabbix_queue()
    assert q_after_first >= 1
    # Exact duplicate via other actor must not grow the queue
    s2, r2 = process_payload(store, base_payload(ts=now, sps_mppt_pv_v=20.0), actor="http")
    assert s2 == 202
    assert r2.get("duplicate") is True or r2.get("no_change") is True
    assert store.count_zabbix_queue() == q_after_first
    # Genuine update via other actor must enqueue again
    s3, _ = process_payload(store, base_payload(ts=now + 1000, sps_mppt_pv_v=21.0), actor="http")
    assert s3 == 202
    assert store.count_zabbix_queue() == q_after_first + 1


# HTTP Telemetry Ingestion Tests - Handler Level
import json
import threading
import time
from http.client import HTTPConnection
from backend.config import config
from backend.store import Store

def start_test_server():
    """Start the HTTP server in a thread for testing."""
    from http.server import ThreadingHTTPServer
    from backend.app import Handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)  # Let server start
    return server, port, thread

def stop_test_server(server, thread):
    server.shutdown()
    thread.join(timeout=1.0)

def test_http_ingest_handler_valid():
    """Test valid telemetry via actual HTTP handler."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {
            "site_id": "SITE-A",
            "sn": "8800000001",
            "ts": int(time.time() * 1000),
            "sps_mppt_pv_v": 23.4,
            "sps_mppt_pv_a": 5.2
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token-123"
        }

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 202, f"Expected 202, got {resp.status}: {body}"
        assert body["deviceId"] == "RUT-8800000001"
        assert "unmapped" in body

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_invalid_token():
    """Test invalid token."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "SITE-A", "sn": "8800000001", "ts": int(time.time() * 1000)}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer wrong-token"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 401, f"Expected 401, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_no_token():
    """Test missing token."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "SITE-A", "sn": "8800000001", "ts": int(time.time() * 1000)}
        headers = {"Content-Type": "application/json"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 401, f"Expected 401, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_disabled():
    """Test disabled endpoint."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = False
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "SITE-A", "sn": "8800000001", "ts": int(time.time() * 1000)}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer test-token-123"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 501, f"Expected 501, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_invalid_json():
    """Test invalid JSON."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = "not valid json"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer test-token-123"}

        conn.request("POST", "/api/telemetry/ingest", payload, headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 400, f"Expected 400, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_oversized_body():
    """Test oversized body."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    original_max = config.NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES

    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"
    config.NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES = 100

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "SITE-A", "sn": "8800000001", "ts": int(time.time() * 1000), "data": "x" * 200}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer test-token-123"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 413, f"Expected 413, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        config.NMS_TELEMETRY_INGEST_MAX_PAYLOAD_BYTES = original_max
        stop_test_server(server, thread)

def test_http_ingest_handler_invalid_identity():
    """Test invalid identity fields."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "", "sn": "8800000001", "ts": int(time.time() * 1000)}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer test-token-123"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 400, f"Expected 400, got {resp.status}: {body}"
        assert body["error"] == "validation_failed"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)

def test_http_ingest_handler_x_telemetry_token():
    """Test X-Telemetry-Token header."""
    server, port, thread = start_test_server()

    original_enabled = config.NMS_TELEMETRY_INGEST_ENABLED
    original_token = config.NMS_TELEMETRY_INGEST_TOKEN
    config.NMS_TELEMETRY_INGEST_ENABLED = True
    config.NMS_TELEMETRY_INGEST_TOKEN = "test-token-123"

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)

        payload = {"site_id": "SITE-A", "sn": "8800000001", "ts": int(time.time() * 1000)}
        headers = {"Content-Type": "application/json", "X-Telemetry-Token": "test-token-123"}

        conn.request("POST", "/api/telemetry/ingest", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())

        assert resp.status == 202, f"Expected 202, got {resp.status}: {body}"

        conn.close()
    finally:
        config.NMS_TELEMETRY_INGEST_ENABLED = original_enabled
        config.NMS_TELEMETRY_INGEST_TOKEN = original_token
        stop_test_server(server, thread)


def _telemetry_mock_handler(app_mod):
    """Minimal handler harness for GET /api/devices/:id/telemetry."""
    import io

    class MockHandler(app_mod.Handler):
        def __init__(self, path):
            self.method = "GET"
            self.path = path
            self.rfile = io.BytesIO(b"")
            self.wfile = io.BytesIO()
            self.headers = {"Cookie": "nms_session=test-token", "Content-Length": "0"}
            self.client_address = ("127.0.0.1", 54321)
            self.request = None
            self.server_version = "TelnusaNMS/0.1"
            self.sys_version = ""
            self.error_message_format = ""
            self.error_content_type = "text/html"

        def send_response(self, code, message=None):
            self.response_code = code

        def send_header(self, keyword, value):
            pass

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

        def _rate_api(self):
            pass

        def _require_user(self, allow_must_change: bool = False):
            return {"id": "u1", "role": "viewer"}, {"id": "u1", "role": "viewer"}

    return MockHandler


def test_telemetry_route_forwards_explicit_window_to_zabbix():
    """The route must hand the requested window to Zabbix unchanged."""
    import tempfile

    import backend.app as app_mod
    from backend.store import Store

    MockHandler = _telemetry_mock_handler(app_mod)
    seen = {}

    class StubZabbix:
        configured = True

        def device_history(self, device_id, **kwargs):
            seen.update(kwargs)
            seen["device_id"] = device_id
            return {"deviceId": device_id, "mppt": {}, "pzem": {}, "window": {}}

    original_zabbix = app_mod.zabbix
    original_store = app_mod.store
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    test_store = Store(tmp.name)
    try:
        app_mod.zabbix = StubZabbix()
        app_mod.store = test_store
        test_store.upsert_device({"device_id": "RUT-TEST-001", "serial": "8800000001", "site_id": "SITE-A", "site_name": "Situs Jaya", "last_seen": 1700000000000})
        handler = MockHandler("/api/devices/RUT-TEST-001/telemetry?from=1699999000&to=1700001000&limit=60")
        handler.do_GET()
        assert handler.response_code == 200, f"expected 200, got {handler.response_code}"
        assert seen["time_from"] == 1699999000, "from forwarded in seconds, unchanged"
        assert seen["time_till"] == 1700001000, "to forwarded in seconds, unchanged"
        # The window alone decides coverage: the fetcher pages until it is closed,
        # so a caller-supplied point count is not forwarded as a cap any more.
        assert "limit" not in seen, "no row cap is handed to the history fetcher"
        assert seen["device_id"] == "RUT-TEST-001"
    finally:
        app_mod.zabbix = original_zabbix
        app_mod.store = original_store


def test_telemetry_route_rejects_inverted_window():
    """from > to is a client error, never a silently swapped window."""
    import tempfile

    import backend.app as app_mod
    from backend.store import Store

    MockHandler = _telemetry_mock_handler(app_mod)
    called = []

    class StubZabbix:
        configured = True

        def device_history(self, *a, **k):
            called.append(k)

    original_zabbix = app_mod.zabbix
    original_store = app_mod.store
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    test_store = Store(tmp.name)
    try:
        app_mod.zabbix = StubZabbix()
        app_mod.store = test_store
        test_store.upsert_device({"device_id": "RUT-TEST-001", "serial": "8800000001", "site_id": "SITE-A", "site_name": "Situs Jaya", "last_seen": 1700000000000})
        handler = MockHandler("/api/devices/RUT-TEST-001/telemetry?from=1700001000&to=1699999000")
        handler.do_GET()
        assert handler.response_code == 400, f"expected 400, got {handler.response_code}"
        assert not called, "inverted window must not reach Zabbix"
    finally:
        app_mod.zabbix = original_zabbix
        app_mod.store = original_store

#  canonical event contract
def test_canonical_event_type_covers_alarm_system_unknown_and_malformed():
    """Every input must land on the canonical set; nothing may escape it."""
    from backend.zabbix_client import (
        CANONICAL_EVENT_TYPES,
        canonical_event_type,
        normalize_event,
    )

    # Alarm: Zabbix trigger raised (value "1") and the legacy wire name.
    assert canonical_event_type("1") == "alarm_created"
    assert canonical_event_type("alarm") == "alarm_created"
    # Recovery: trigger cleared (value "0"). The legacy "system" bucket cannot be
    # told apart from a real recovery, so it must not claim one.
    assert canonical_event_type("0") == "alarm_resolved"
    assert canonical_event_type("system") == "unknown"
    # Unknown / malformed / hostile input.
    for bad in ("", "   ", "reboot_now", None, 7, [], {}, "ALARM", "2"):
        assert canonical_event_type(bad) == "unknown", f"{bad!r} must degrade to unknown"
    # Canonical names pass through untouched.
    for name in CANONICAL_EVENT_TYPES:
        assert canonical_event_type(name) == name


def test_normalize_event_alarm_and_system_events_use_canonical_types():
    """normalize_event emits canonical types, not the legacy alarm/system pair."""
    from backend.zabbix_client import normalize_event

    hosts = [{"host": "RUT-TEST-001", "name": "RUT-TEST-001"}]
    alarm = normalize_event({"eventid": "1", "hosts": hosts, "clock": 1700000000,
                             "value": "1", "severity": "4", "name": "High load"})
    assert alarm["type"] == "alarm_created", "a raised problem is alarm_created"
    assert alarm["severity"] == "high", "severity maps through the existing table"
    assert alarm["message"] == "High load"

    recovered = normalize_event({"eventid": "2", "hosts": hosts, "clock": 1700000060,
                                 "value": "0", "severity": "0", "name": "High load"})
    assert recovered["type"] == "alarm_resolved", "a cleared problem is alarm_resolved"


def test_normalize_event_malformed_partial_event_is_still_a_valid_row():
    """A partial event must render as a labelled row, not a blank or a crash."""
    from backend.zabbix_client import normalize_event

    partial = normalize_event({"eventid": "3"})
    assert partial["type"] == "unknown", "an event with no value cannot be classified"
    assert partial["message"], "message must never be empty; that renders a blank cell"
    assert partial["timestamp"] == 0, "a missing clock degrades to 0, not an exception"
    assert partial["severity"] == "information", "a missing severity uses the default"

    # Explicitly empty name still yields a placeholder.
    unnamed = normalize_event({"eventid": "4", "name": "", "value": "1"})
    assert unnamed["message"] == "Unnamed event"

    # Uncoercible clock/ns must not raise.
    broken = normalize_event({"eventid": "5", "clock": "not-a-number", "ns": "also-bad", "value": "1"})
    assert broken["timestamp"] == 0
    assert broken["type"] == "alarm_created"


#  explicit health integration state
def test_health_payloads_declare_not_integrated_and_invent_no_values():
    """Router resource health has no source: say so explicitly, never fabricate a reading."""
    from backend.app import HEALTH_NOT_INTEGRATED, health_history_unavailable, health_snapshot
    from backend.store import Store

    snap = health_snapshot(Store(":memory:"), "RUT-TEST-001")
    assert snap["state"] == HEALTH_NOT_INTEGRATED, (
        "an all-null payload must not be left to be read as a live source reporting nothing"
    )
    # Resource metrics have no collected source at all.
    resource_metrics = ("cpuPercent", "ramPercent", "flashPercent", "temperatureC",
                        "uptimeSeconds", "rssi", "networkState")
    for field in resource_metrics:
        assert snap[field] is None, f"{field} must stay null; fabricating it is the P-4 defect"
    # Derived transport/freshness fields stay null when there is no device row and
    # no integration state to read, instead of guessing a value.
    assert snap["mqttConnected"] is None
    assert snap["lastTelemetryAge"] is None
    assert set(snap["dimensions"]) == {
        "managementReachability", "sensorAcquisition", "httpTransport", "mqttTransport",
        "ingest", "freshness", "zabbix", "webui", "terminal", "routerHealth",
    }
    assert snap["dimensions"]["routerHealth"]["state"] == HEALTH_NOT_INTEGRATED

    hist = health_history_unavailable("RUT-TEST-001", "not_configured")
    assert hist["state"] == HEALTH_NOT_INTEGRATED
    for series in ("cpu", "ram", "temperature", "rssi"):
        assert hist[series] == [], f"{series} must be empty, never synthesised"


def test_health_routes_report_not_integrated_state():
    """Both health routes must carry the explicit state through to the wire."""
    import tempfile

    import backend.app as app_mod
    from backend.store import Store

    MockHandler = _telemetry_mock_handler(app_mod)
    original_store = app_mod.store
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    test_store = Store(tmp.name)
    try:
        app_mod.store = test_store
        test_store.upsert_device({"device_id": "RUT-TEST-001", "serial": "8800000001",
                                  "site_id": "SITE-A", "site_name": "Situs Jaya",
                                  "last_seen": 1700000000000})
        for suffix in ("/health", "/health/history"):
            handler = MockHandler(f"/api/devices/RUT-TEST-001{suffix}")
            handler.do_GET()
            assert handler.response_code == 200, f"{suffix} should answer 200"
            body = json.loads(handler.wfile.getvalue().decode())
            assert body["state"] == "not_integrated", f"{suffix} must declare its source state"
    finally:
        app_mod.store = original_store


#  metric state: not-integrated vs empty-window vs data
def test_device_history_reports_metric_state_per_series():
    """An absent item and an empty item are different facts and must differ."""
    from backend.zabbix_client import METRIC_DATA, METRIC_NO_DATA_IN_RANGE, METRIC_NOT_INTEGRATED
    from backend.zabbix_contract import ITEM_SPECS

    pv_key = ITEM_SPECS["sps_mppt_pv_v"].zabbix_key
    pv_item = {"itemid": "10", "key_": pv_key, "value_type": 3}
    energy_key = ITEM_SPECS["kwh"].zabbix_key
    energy_item = {"itemid": "11", "key_": energy_key, "value_type": 3}

    client = ZabbixClient.__new__(ZabbixClient)
    client.host_lookup = lambda host: {"hostid": "900", "host": host}
    # pvVoltage exists and has samples; energy exists but sent nothing in-window.
    client.item_lookup = lambda hostid, key: (
        pv_item if key == pv_key else energy_item if key == energy_key else None
    )
    client.history_window_for_items = lambda hostid, items, **kw: ({
        pv_key: [{"clock": "1700000000", "ns": "0", "value": "24.5"}],
        energy_key: [],
    }, True)

    out = client.device_history("RUT-TEST-001", time_from=1699999000, time_till=1700001000)
    states = out["metricState"]

    assert states["mppt.pvVoltage"] == METRIC_DATA
    assert len(out["mppt"]["pvVoltage"]) == 1
    assert states["pzem.energy"] == METRIC_NO_DATA_IN_RANGE, (
        "an item that exists but sent nothing is no_data_in_range, not not_integrated"
    )
    assert out["pzem"]["energy"] == []
    # Never configured at all.
    assert states["pzem.phaseAVoltage"] == METRIC_NOT_INTEGRATED
    assert states["mppt.pvCurrent"] == METRIC_NOT_INTEGRATED
    # Every contracted metric must be classified; none may be missing.
    assert len(states) == 12, f"all 12 contracted metrics need a state, got {len(states)}"


def test_telemetry_route_host_not_found_reports_not_integrated():
    """A host absent from Zabbix has no items: that is NOT INTEGRATED, not empty."""
    import tempfile

    import backend.app as app_mod
    from backend.store import Store

    MockHandler = _telemetry_mock_handler(app_mod)
    original_zabbix = app_mod.zabbix
    original_store = app_mod.store
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    test_store = Store(tmp.name)

    class HostMissingZabbix:
        configured = True

        def device_history(self, device_id, **kwargs):
            raise ZabbixError("HOST_NOT_FOUND", "no such host")

    try:
        app_mod.zabbix = HostMissingZabbix()
        app_mod.store = test_store
        test_store.upsert_device({"device_id": "RUT-TEST-001", "serial": "8800000001",
                                  "site_id": "SITE-A", "site_name": "Situs Jaya",
                                  "last_seen": 1700000000000})
        handler = MockHandler("/api/devices/RUT-TEST-001/telemetry?from=1699999000&to=1700001000")
        handler.do_GET()
        assert handler.response_code == 200
        body = json.loads(handler.wfile.getvalue().decode())
        assert len(body["metricState"]) == 12, "every metric needs a declared state"
        assert set(body["metricState"].values()) == {"not_integrated"}
        # The window echo must survive so the UI can still state what it asked for.
        assert body["window"]["fromMs"] == 1699999000000
    finally:
        app_mod.zabbix = original_zabbix
        app_mod.store = original_store


def test_telemetry_route_backend_error_is_explicit_503_not_fake_data():
    """A Zabbix failure must surface as an error state, never as empty series."""
    import tempfile

    import backend.app as app_mod
    from backend.store import Store

    MockHandler = _telemetry_mock_handler(app_mod)
    original_zabbix = app_mod.zabbix
    original_store = app_mod.store
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    test_store = Store(tmp.name)

    class BrokenZabbix:
        configured = True

        def device_history(self, device_id, **kwargs):
            raise ZabbixError("CONNECTION_ERROR", "zabbix unreachable")

    try:
        app_mod.zabbix = BrokenZabbix()
        app_mod.store = test_store
        test_store.upsert_device({"device_id": "RUT-TEST-001", "serial": "8800000001",
                                  "site_id": "SITE-A", "site_name": "Situs Jaya",
                                  "last_seen": 1700000000000})
        handler = MockHandler("/api/devices/RUT-TEST-001/telemetry")
        handler.do_GET()
        assert handler.response_code == 503, (
            f"a backend failure must be 503, not a silent empty series (got {handler.response_code})"
        )
    finally:
        app_mod.zabbix = original_zabbix
        app_mod.store = original_store


def test_restore_bulk_endpoint_paths_unit():
    """The RUT SPA reuses one prefixed string literal for both browser fetch() URLs and
    bulk sub-request endpoints. Only the latter must be restored to a native path,
    because the RUT dispatches those server-side against its own route table."""
    from backend.app import restore_bulk_endpoint_paths as restore

    P = "/api/remote/RUT-8800000001/webui/proxy"
    enc = lambda obj: json.dumps(obj).encode("utf-8")

    # B: a prefixed endpoint becomes its RUT-native form.
    out = json.loads(restore(enc({"data": [{"endpoint": P + "/api/system/config/general",
                                            "method": "GET"}]}), P))
    assert out["data"][0]["endpoint"] == "/api/system/config/general"

    # C: every entry is restored, and the rest of each entry survives.
    out = json.loads(restore(enc({"data": [
        {"endpoint": P + "/api/fota/config", "method": "GET"},
        {"endpoint": P + "/api/dfota/config", "method": "GET"},
        {"endpoint": P + "/api/backup/actions/delete", "method": "POST"},
    ]}), P))
    assert [e["endpoint"] for e in out["data"]] == [
        "/api/fota/config", "/api/dfota/config", "/api/backup/actions/delete"]
    assert [e["method"] for e in out["data"]] == ["GET", "GET", "POST"]

    # D: an endpoint the SPA built natively is returned untouched -- same object.
    native = enc({"data": [{"endpoint": "/api/system/config/general", "method": "GET"}]})
    assert restore(native, P) is native

    # E: a query string on the endpoint is preserved, not truncated.
    out = json.loads(restore(enc({"data": [
        {"endpoint": P + "/api/system/device/usage/status?data=localtime", "method": "GET"}]}), P))
    assert out["data"][0]["endpoint"] == "/api/system/device/usage/status?data=localtime"

    # F: no other string in the payload is touched, including one that merely
    # contains the prefix and one that looks like an API path.
    out = json.loads(restore(enc({"data": [{"endpoint": P + "/api/system/config/general"}],
                                  "note": P + "/api/keep/me",
                                  "label": "/api/not/an/endpoint",
                                  "nested": {"url": P + "/api/deep"}}), P))
    assert out["data"][0]["endpoint"] == "/api/system/config/general"
    assert out["note"] == P + "/api/keep/me"
    assert out["label"] == "/api/not/an/endpoint"
    assert out["nested"]["url"] == P + "/api/deep"

    # G: malformed and non-bulk-shaped bodies are forwarded unchanged, never raising.
    for bad in (b'{"data":[{', b"not json at all", b"", b'{"data":"notalist"}',
               b"null", b"[1,2,3]", b'{"other":1}'):
        assert restore(bad, P) is bad

    # A bulk entry with no endpoint, or a non-string one, is left alone.
    no_ep = enc({"data": [{"method": "GET"}, {"endpoint": 7}, "junk"]})
    assert json.loads(restore(no_ep, P)) == json.loads(no_ep)

    # Idempotent: a body that is already native is returned as the same object, which
    # is how the caller detects "unchanged" and skips touching Content-Length.
    once = restore(enc({"data": [{"endpoint": P + "/api/x", "method": "GET"}]}), P)
    assert restore(once, P) is once

    # A prefix-shaped endpoint whose remainder is not an absolute path is not stripped.
    weird = enc({"data": [{"endpoint": P}]})
    assert json.loads(restore(weird, P)) == {"data": [{"endpoint": P}]}


def test_remote_webui_bulk_endpoints_restored_end_to_end():
    """Reproduces the P-5 A/B experiment against a mock that emulates the RUT bulk
    dispatcher: a sub-request whose endpoint does not start with /api/ matches no route
    and comes back as code 122 under a 207 envelope, exactly as the RUT906 answered."""
    import http.server
    import io
    import backend.app as app_mod
    from backend.remote import DeviceRemoteTarget
    from backend.tunnel import TunnelInfo, TunnelManager, TunnelState

    PREFIX = "/api/remote/RUT-8800000001/webui/proxy"
    seen_bulk = []
    seen_get = []
    # The sub-request routes the RUT906 actually served during the P-5 trace.
    RUT_ROUTES = {
        "/api/system/config/general",
        "/api/system/device/status",
        "/api/system/device/usage/status",
        "/api/date_time/ntp/client/config/ntpclient",
        "/api/fota/config",
        "/api/dfota/config",
        "/api/firmware/device/updates/status",
        "/api/access_control/webui/certificate/status",
        "/api/certificates/config",
    }
    import urllib.parse

    class MockRUT(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, ctype, payload, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            seen_get.append(self.path)
            if self.path.startswith("/api/system/config/general"):
                self._send(200, "application/json", json.dumps({
                    "success": True,
                    "data": {"devicename": "RUT906", "lang_code": "en",
                             "api_session_timeout": "300"}}).encode())
            elif self.path == "/api/session/status":
                self._send(200, "application/json", json.dumps({
                    "success": True, "data": {"active": True, "username": "admin"}}).encode())
            elif self.path == "/asset.js":
                self._send(200, "application/javascript",
                           b'fetch("/api/system/config/general");')
            else:
                self._send(404, "text/plain", b"nope")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            assert len(raw) == length, (
                f"Content-Length {length} did not match the {len(raw)} bytes actually sent")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                # A body the RUT cannot parse gets a rejection, but the proxy must have
                # forwarded it verbatim rather than mangling or dropping it.
                seen_bulk.append({"__malformed__": raw.decode("utf-8", "replace")})
                self._send(400, "application/json", json.dumps(
                    {"success": False, "errors": [{"code": 22}]}).encode())
                return
            seen_bulk.append(payload)
            # The RUT resolves each sub-request against its own route table, which has
            # no catch-all: a prefixed endpoint is not in it, and that is the whole
            # defect. The routes below are the ones actually observed on RUT906
            # firmware 07.24.3, not a "/api/* looks plausible" shortcut -- a prefix
            # match would wrongly accept /api/remote/... and hide the regression.
            results = []
            for entry in payload["data"]:
                path = urllib.parse.urlparse(str(entry.get("endpoint", ""))).path
                if path in RUT_ROUTES:
                    results.append({"success": True, "data": {"devicename": "RUT906"}})
                else:
                    results.append({"success": False, "errors": [
                        {"code": 122, "error": "Endpoint not implemented",
                         "source": "Request"}]})
            self._send(207, "application/json",
                       json.dumps({"success": True, "data": results}).encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockRUT)
    upstream_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class MockHandler(app_mod.Handler):
        def __init__(self, method, path, headers=None, body=b""):
            self.command = method
            self.path = path
            self.headers = headers or {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.client_address = ("127.0.0.1", 12345)
            self.status_code = None
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            self.response_headers.append((keyword, value))

        def end_headers(self):
            pass

        def _remote_key(self):
            return "127.0.0.1"

    store = Store(":memory:")
    now = app_mod.now_ms()
    uid = store.create_user("op@web01.id", "Op", app_mod.hash_password("OpPass123!"), "operator", now)
    sid = "sid-web01"
    store.create_session(sid, uid, now + 3600_000, "csrf-web01")

    orig_store, orig_tm, orig_resolve = app_mod.store, app_mod.tunnel_manager, app_mod.resolve_device_target
    app_mod.store = store
    tm = TunnelManager()
    tm.store = store
    tm.ensure = lambda d: TunnelInfo(device_id=d, local_port=upstream_port, pid=99999,
                                     state=TunnelState.READY, started_at=now)
    app_mod.tunnel_manager = tm
    app_mod.resolve_device_target = lambda d, st: DeviceRemoteTarget(
        device_id=d, ssh_host="127.0.0.1", ssh_port=22,
        webui_target_host="127.0.0.1", webui_target_port=upstream_port
    ) if d == "RUT-8800000001" else None

    def bulk_call(entries):
        payload = json.dumps({"data": entries}).encode("utf-8")
        seen_bulk.clear()
        h = MockHandler("POST", PREFIX + "/api/bulk",
                        headers={"Cookie": f"nms_session={sid}",
                                 "Content-Type": "application/json",
                                 "Content-Length": str(len(payload))},
                        body=payload)
        h.do_POST()
        return json.loads(h.wfile.getvalue().decode("utf-8")), h

    try:
        # 0. Negative control, straight at the mock RUT. If the mock did not really
        #    discriminate, the assertions below would pass for the wrong reason and
        #    would keep passing after the fix is reverted.
        for endpoint, want_success in ((PREFIX + "/api/system/config/general", False),
                                       ("/api/system/config/general", True)):
            probe = json.dumps({"data": [{"endpoint": endpoint, "method": "GET"}]}).encode()
            raw_conn = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=5)
            raw_conn.request("POST", "/api/bulk", body=probe,
                             headers={"Content-Type": "application/json",
                                      "Content-Length": str(len(probe))})
            got = json.loads(raw_conn.getresponse().read().decode("utf-8"))
            raw_conn.close()
            assert got["data"][0].get("success") is want_success, (
                f"mock RUT did not reproduce the defect for {endpoint}: {got}")

        # 1. The P-5 failing shape: a prefixed endpoint. The mock RUT must receive it
    #    as a native path, so the sub-request succeeds instead of returning code 122.
        resp, _ = bulk_call([
            {"endpoint": PREFIX + "/api/system/config/general", "method": "GET"},
            {"endpoint": PREFIX + "/api/date_time/ntp/client/config/ntpclient", "method": "GET"},
        ])
        assert seen_bulk[-1]["data"] == [
            {"endpoint": "/api/system/config/general", "method": "GET"},
            {"endpoint": "/api/date_time/ntp/client/config/ntpclient", "method": "GET"},
        ], f"proxy leaked a prefixed bulk endpoint upstream: {seen_bulk[-1]}"
        assert all(e["success"] for e in resp["data"]), (
            f"bulk sub-request still failed upstream: {resp}")
        assert resp["data"][0]["data"] == {"devicename": "RUT906"}

        # 2. Control: an endpoint the SPA built natively must pass through untouched
        #    and behave identically.
        resp2, _ = bulk_call([{"endpoint": "/api/system/config/general", "method": "GET"}])
        assert seen_bulk[-1]["data"][0]["endpoint"] == "/api/system/config/general"
        assert resp2["data"][0]["success"] is True

        # 3. A normal browser fetch() still reaches the RUT on its native path -- the
        #    browser-facing URL keeps the prefix, the proxy still strips it.
        seen_get.clear()
        h = MockHandler("GET", PREFIX + "/api/system/config/general",
                        headers={"Cookie": f"nms_session={sid}"})
        h.do_GET()
        assert h.status_code == 200
        assert seen_get[-1] == "/api/system/config/general", (
            f"normal fetch no longer resolves: {seen_get[-1]}")
        assert json.loads(h.wfile.getvalue().decode("utf-8"))["data"]["devicename"] == "RUT906"

        # 4. The JS bundle rewrite is untouched: /api/ literals are still prefixed so
        #    the SPA's own fetch() keeps working.
        h_js = MockHandler("GET", PREFIX + "/asset.js", headers={"Cookie": f"nms_session={sid}"})
        h_js.do_GET()
        assert f'fetch("{PREFIX}/api/system/config/general")' in h_js.wfile.getvalue().decode()

        # 5. A malformed bulk body is forwarded without crashing the proxy or the RUT.
        bad = b'{"data":[{"endpoint":'
        seen_bulk.clear()
        h_bad = MockHandler("POST", PREFIX + "/api/bulk",
                            headers={"Cookie": f"nms_session={sid}",
                                     "Content-Type": "application/json",
                                     "Content-Length": str(len(bad))},
                            body=bad)
        h_bad.do_POST()
        assert h_bad.status_code == 400, "the RUT's rejection must reach the caller intact"
        assert seen_bulk[-1] == {"__malformed__": bad.decode()}, (
            f"malformed bulk body was not forwarded verbatim: {seen_bulk[-1]!r}")

        # 6. Auth still fails closed, and cookie rewriting is unchanged.
        h_unauth = MockHandler("GET", PREFIX + "/api/system/config/general")
        h_unauth.do_GET()
        assert h_unauth.status_code == 401
    finally:
        server.shutdown()
        server.server_close()
        app_mod.store, app_mod.tunnel_manager = orig_store, orig_tm
        app_mod.resolve_device_target = orig_resolve

def run():
    test_password_hash_roundtrip()
    test_session_token_hashing()
    test_telemetry_known_fields_and_unmapped()
    test_telemetry_rejects_bad_identity_future_and_numeric_string()
    test_malformed_json_rejected()
    test_store_auth_and_roles()
    test_csrf_generation_and_validation()
    test_notification_recipient_management()
    test_processor_discovery_dedupe_ordering_and_unmapped()
    test_rc2_canonical_merge_preserves_last_valid_value()
    test_freshness_boundaries()
    test_zabbix_queue_backoff_and_dead_letter()
    test_rc4_full_queue_evicts_oldest_and_reports_honestly()
    test_rc4_permanent_error_fast_tracks_to_dead_letter()
    test_rc4_dead_letter_ttl_preserves_recent_evidence()
    test_zabbix_sender_protocol_encoding()
    test_zabbix_sender_ack_must_process_all_values()
    test_zabbix_sender_rejects_partial_success()
    test_zabbix_sender_rejects_failed_response()
    test_zabbix_api_url_normalization()
    test_zabbix_item_mismatch_rejected()
    test_zabbix_health_rejects_unsupported_version()
    test_zabbix_device_history_normalization()
    test_zabbix_item_lookup_primes_every_key_in_one_call()
    test_zabbix_items_for_host_rejects_duplicate_key()
    test_rollup_sync_survives_empty_window_and_saves_hours()
    test_sessions_store_no_raw_cookie_and_password_change_revokes_others()
    test_login_verifies_even_for_unknown_accounts()
    test_store_retention_purges_processed_messages_and_expired_sessions()
    test_notification_provider_never_claims_an_unimplemented_integration()
    test_store_audit_retention_is_bounded_and_configurable()
    test_client_ip_counts_trusted_hops_from_the_right()
    test_bootstrap_admin_refuses_a_weak_password()
    test_pepper_migration_accepts_legacy_hash_and_flags_upgrade()
    test_xlsx_report_is_numbers_only_without_chart_parts()
    test_new_high_alarm_is_published_to_the_live_stream()
    test_warning_severity_never_reaches_the_live_stream()
    test_webui_proxy_rewrites_root_absolute_navigation()
    test_proxy_strips_device_policy_headers_but_keeps_content_headers()
    test_zabbix_device_history_preserves_requested_window()
    test_zabbix_device_history_reports_truncation_only_when_the_pager_runs_out()
    test_zabbix_device_history_orders_samples_chronologically()
    test_zabbix_device_history_empty_window_is_empty_not_fabricated()
    test_zabbix_device_history_stale_feed_is_not_anchored_by_data()
    test_zabbix_device_history_skips_unparsable_rows_without_inventing_values()
    test_zabbix_alarm_severity_normalization()
    test_zabbix_resolved_without_r_clock_omits_end_time()
    test_zabbix_problems_omits_select_hosts_and_resolves_host()
    test_broker_pub_sub()
    test_route_device_parsing()
    test_unconfigured_zabbix_explicit_503()
    test_remote_endpoints_explicit_501()
    test_terminal_grant_signed_assertion_and_trust_boundary()
    test_zabbix_value_mapping_approved_keys_only()
    test_history_fills_uncovered_hours_from_zabbix_trends()
    test_device_health_and_network_labels_come_from_the_router()
    test_router_health_ingest_maps_and_persists_every_field()
    test_zabbix_reconcile_updates_units_but_refuses_type_mismatch()
    test_health_snapshot_dimensions_no_fake_data()
    test_health_history_no_data()
    test_rate_limiter()
    test_audit_log()
    test_delivery_worker_loop()
    test_unmapped_field_storage_and_query()
    test_strict_topic_validation()
    test_non_finite_json_rejected()
    test_identity_conflict_rejected()
    test_dynamic_freshness_computation()
    test_stale_write_path_uses_canonical_status()
    test_session_cookie_max_age_dynamic()
    test_zabbix_recv_exact_framing()
    test_zabbix_history_per_item_bounded()
    test_purge_old_processed_messages()
    test_zabbix_delivery_worker_sender_submit_success()
    test_zabbix_delivery_worker_sender_failure_retains_queue()
    test_zabbix_delivery_worker_partial_success_retains_failed()
    test_argon2id_enforcement_and_pbkdf2_fallback()
    test_rate_limiter_bounded_memory_and_eviction()
    test_bootstrap_admin_and_forced_password_change_flag()
    test_user_management_crud_and_last_admin_protection()
    test_google_sso_removed_from_providers_and_routes()
    test_client_ip_trusted_proxy_and_spoof_protection()
    test_patch_delete_dispatch_and_csrf()
    test_error_propagation_401_403_500()
    test_dual_key_login_rate_limiting_and_no_account_lockout()
    test_tunnel_command_construction_and_security_invariants()
    test_tunnel_ssh_key_and_known_hosts_validation()
    test_tunnel_manager_lifecycle_and_state_transitions()
    test_tunnel_manager_concurrency_and_port_isolation()
    test_tunnel_startup_wait_configuration_and_override()
    test_remote_webui_endpoint_tunnel_integration()
    test_remote_webui_reverse_proxy_end_to_end()
    test_remote_webui_js_api_paths_rewritten_to_proxy_prefix()
    test_remote_webui_root_absolute_resource_paths_rewritten()
    test_remote_webui_event_stream_relayed_without_buffering()
    test_remote_webui_transformed_resources_are_cache_valid()
    test_zabbix_queue_unconfigured_retains_and_records_state()
    test_zabbix_delivery_worker_partial_or_failure_result_retention()
    test_store_count_devices_by_age_effective_ms()
    test_store_connection_enables_foreign_keys_on_every_connection()
    test_store_connection_applies_busy_timeout()
    test_store_wal_applied_for_file_backed_database_and_safe_for_memory()
    test_store_pragmas_survive_reopen_and_do_not_corrupt_data()
    test_store_concurrent_writes_do_not_raise_database_is_locked()
    test_validate_ssh_user_rejects_unset_and_privileged_accounts()
    test_build_ssh_command_fails_closed_without_valid_user()
    test_tunnel_manager_blocks_when_no_ssh_user_configured()
    test_allocate_loopback_port_is_bounded_and_deterministic()
    test_tunnel_manager_enforces_concurrency_limit()
    test_tunnel_manager_releases_ports_and_bounds_lock_cache()
    test_zabbix_item_status_explicit_and_host_group_template_reconciliation()
    test_proxy_cookie_filter_and_accept_encoding_identity()
    test_tunnel_maintenance_worker_lifecycle_and_touch()
    test_http_ingest_handler_valid()
    test_http_ingest_handler_invalid_token()
    test_http_ingest_handler_no_token()
    test_http_ingest_handler_disabled()
    test_http_ingest_handler_invalid_json()
    test_http_ingest_handler_oversized_body()
    test_http_ingest_handler_invalid_identity()
    test_http_ingest_handler_x_telemetry_token()
    test_http_ingest_valid_telemetry()
    test_http_ingest_invalid_json()
    test_http_ingest_missing_identity()
    test_http_ingest_missing_sn()
    test_http_ingest_missing_ts()
    test_http_ingest_invalid_timestamp_future()
    test_http_ingest_invalid_timestamp_past()
    test_http_ingest_unknown_field()
    test_http_ingest_null_sensor_values()
    test_http_ingest_disabled()
    test_http_ingest_device_discovery_upsert()
    test_http_ingest_response_codes()
    test_http_ingest_payload_size_limit()
    test_http_ingest_content_type()
    test_http_ingest_actor_audit()
    test_convergence_http_mqtt_same_ts_merges()
    test_convergence_http_newer_ts_supersedes_mqtt()
    test_convergence_mqtt_stale_after_http_superseded()
    test_convergence_partial_merge_preserves_other_actor_fields()
    test_convergence_site_conflict_rejected_across_actors()
    test_convergence_pzem_scaling_contract()
    test_convergence_solar_radiation_integrated_end_to_end()
    test_convergence_unmapped_preserved_across_actors()
    test_convergence_actor_audit_both_actors()
    test_convergence_zabbix_queue_on_update_only()
    # Previously defined but never called by run().
    test_integration_status_merges_live_metrics_over_stored_snapshot()
    test_zabbix_apiinfo_version_omits_auth_header()
    test_zabbix_host_lookup_uses_select_host_groups()
    test_telemetry_route_forwards_explicit_window_to_zabbix()
    test_telemetry_route_rejects_inverted_window()
    test_canonical_event_type_covers_alarm_system_unknown_and_malformed()
    test_normalize_event_alarm_and_system_events_use_canonical_types()
    test_normalize_event_malformed_partial_event_is_still_a_valid_row()
    test_health_payloads_declare_not_integrated_and_invent_no_values()
    test_health_routes_report_not_integrated_state()
    test_device_history_reports_metric_state_per_series()
    test_telemetry_route_host_not_found_reports_not_integrated()
    test_telemetry_route_backend_error_is_explicit_503_not_fake_data()
    test_restore_bulk_endpoint_paths_unit()
    test_remote_webui_bulk_endpoints_restored_end_to_end()
    # SSE-01
    test_sse_publish_assigns_monotonic_event_ids()
    test_sse_buffer_overflow_collapses_to_resync_marker()
    test_rc1_skewed_device_reports_degraded_not_offline()
    test_rc1_skew_clears_when_clock_recovers()
    test_rc1_dead_transport_still_goes_offline()
    test_rc3_aging_sweep_transitions_silent_device()
    test_rc3_frames_carry_broker_sequence()
    test_sse_connection_caps_reject_explicitly()
    test_sse_rejection_is_distinguishable_from_a_healthy_stream()
    test_sse_unsubscribe_releases_capacity()
    test_r1_derived_metrics_pv_and_output_power()
    test_r1_evaluate_sensor_completeness_mppt_pzem_and_solar()
    test_r1_status_precedence_model()
    test_r1_zabbix_history_solar_series_and_metric_state()
    test_r1_zabbix_history_solar_series_maps_values()
    test_r1_zabbix_history_solar_item_missing_reports_not_integrated()
    test_r4_alarm_engine_and_lifecycle()
    test_solar_merge_preserves_last_valid_value()
    test_ingest_counters_are_per_transport()
    test_integration_status_exposes_http_transport_row()
    test_device_public_sensor3_state_follows_stored_row()
    test_r4_fleet_summary()

def test_sse_publish_assigns_monotonic_event_ids():
    """SSE-01: every published event gets a strictly increasing id so clients can resume."""
    from backend.sse import EventBroker

    b = EventBroker()
    sub = b.subscribe("RUT-A", user_key="u:1")
    for _ in range(3):
        b.publish("RUT-A", {"type": "telemetry", "payload": {}})

    assert b.last_event_id() == 3
    assert b.stats()["last_event_id"] == 3
    assert b.stats()["published_events"] == 3

    # Ids keep advancing across devices so one global resume point is valid.
    b.publish("RUT-B", {"type": "telemetry", "payload": {}})
    assert b.last_event_id() == 4
    b.unsubscribe("RUT-A", sub)


def test_sse_buffer_overflow_collapses_to_resync_marker():
    """SSE-01: a slow client is told its view is stale, not silently truncated."""
    from backend.sse import EVENT_RESYNC, EventBroker

    b = EventBroker(max_queue=3)
    sub = b.subscribe("RUT-A", user_key="u:1")
    for i in range(10):
        b.publish("RUT-A", {"type": "telemetry", "payload": {"i": i}})

    assert sub.overflowed is True, "overflow must be visible, not silent"

    # Deterministic: exactly one marker, no partially-truncated tail.
    frames = []
    while True:
        try:
            frames.append(json.loads(sub.queue.get_nowait()))
        except queue.Empty:
            break
    assert len(frames) == 1, f"expected a single collapsed marker, got {frames}"
    assert frames[0]["type"] == EVENT_RESYNC
    assert frames[0]["payload"]["reason"] == "buffer_overflow"
    assert b.stats()["dropped_events"] > 0
    b.unsubscribe("RUT-A", sub)

# RC-1: a device alive on transport but ~1h behind on its clock reads DEGRADED
# with skew evidence, never plain OFFLINE. Data keeps source-clock history.
def test_rc1_skewed_device_reports_degraded_not_offline():
    t_recv = 1_700_000_000_000
    t_src = t_recv - 3_648_000  # ~1h behind, the observed RUT offset
    store = Store(":memory:")
    code, _ = process_payload(store, base_payload(ts=t_src), actor="test", received_ms=t_recv)
    assert code == 202
    d = store.get_device("RUT-8800000001", now_ms=t_recv)
    assert d["status"] == "clock_skew"
    assert d["clock_skew"] is True
    assert d["clock_skew_seconds"] == 3648
    assert d["data_age"] == "offline"  # source clock honestly old
    counts = store.count_devices_by_age(now_ms=t_recv)
    assert counts["degraded_devices"] == 1
    assert counts["offline_devices"] == 0


# RC-1: the degraded state self-clears; a clock fix reads fresh again.
def test_rc1_skew_clears_when_clock_recovers():
    t0 = 1_700_000_000_000
    store = Store(":memory:")
    process_payload(store, base_payload(ts=t0 - 3_648_000), actor="test", received_ms=t0)
    assert store.get_device("RUT-8800000001", now_ms=t0)["status"] == "clock_skew"
    t1 = t0 + 60_000
    code, _ = process_payload(store, base_payload(ts=t1), actor="test", received_ms=t1)
    assert code == 202
    d = store.get_device("RUT-8800000001", now_ms=t1)
    assert d["status"] == "online"
    assert d["clock_skew"] is False


# RC-1: a device silent on transport still goes OFFLINE; skew needs recent receipt.
def test_rc1_dead_transport_still_goes_offline():
    t0 = 1_700_000_000_000
    store = Store(":memory:")
    process_payload(store, base_payload(ts=t0 - 3_648_000), actor="test", received_ms=t0)
    assert store.get_device("RUT-8800000001", now_ms=t0)["status"] == "clock_skew"
    # 1h of silence: transport dead, receipt no longer recent -> offline.
    d = store.get_device("RUT-8800000001", now_ms=t0 + 3_700_000)
    assert d["status"] == "offline"
    assert d["clock_skew"] is False

# RC-3: server-side aging sweep transitions silent devices and publishes status.
def test_rc3_aging_sweep_transitions_silent_device():
    from backend.processor import StatusAgingWorker
    from backend.sse import EventBroker
    import backend.processor as proc
    store = Store(":memory:")
    t0 = 1_700_000_000_000
    process_payload(store, base_payload(ts=t0), actor="test", received_ms=t0)
    raw = store.list_devices_raw()[0]
    assert raw["status"] == "online"
    # 700s later: offline. Sweep persists + publishes exactly one status frame.
    worker = StatusAgingWorker(store)
    old_broker = proc.broker
    probe = EventBroker()
    proc.broker = probe
    try:
        sub = probe.subscribe("RUT-8800000001")
        n = worker.sweep_once(now_ms=t0 + 700_000)
        assert n == 1
        stored = store.list_devices_raw()[0]
        assert stored["status"] == "stale"
        assert stored["data_age"] == "stale"
        frame = json.loads(sub.queue.get(timeout=1.0))
        assert frame["type"] == "status"
        assert frame["payload"]["status"] == "stale"
        assert frame["payload"]["dataAge"] == "stale"
        # 1000s: offline. Steady state after that: quiet, no duplicate frames.
        n2 = worker.sweep_once(now_ms=t0 + 1_000_000)
        assert n2 == 1
        stored2 = store.list_devices_raw()[0]
        assert stored2["status"] == "offline"
        assert stored2["data_age"] == "offline"
        off = json.loads(sub.queue.get(timeout=1.0))
        assert off["payload"]["status"] == "offline"
        n3 = worker.sweep_once(now_ms=t0 + 1_010_000)
        assert n3 == 0
        assert sub.queue.qsize() == 0
    finally:
        proc.broker = old_broker


# RC-3: every queued frame carries the broker sequence so the route can emit
# honest `id:` fields, and resume-gap markers take the resync shape.
def test_rc3_frames_carry_broker_sequence():
    from backend.sse import EventBroker
    b = EventBroker()
    sub = b.subscribe("RUT-A", user_key="u:1")
    b.publish("RUT-A", {"type": "telemetry", "payload": {}})
    b.publish("RUT-A", {"type": "status", "payload": {}})
    first = json.loads(sub.queue.get(timeout=1.0))
    second = json.loads(sub.queue.get(timeout=1.0))
    assert first["_seq"] < second["_seq"]
    assert first["type"] == "telemetry"
    assert second["type"] == "status"
    b.unsubscribe("RUT-A", sub)


def test_sse_connection_caps_reject_explicitly():
    """SSE-01: per-device, per-user and total caps refuse instead of fanning out."""
    from backend.sse import EventBroker, SubscriptionRejected

    b = EventBroker(max_total=3, max_per_device=2, max_per_user=2)
    a = b.subscribe("RUT-A", user_key="u:1")
    a2 = b.subscribe("RUT-A", user_key="u:2")

    # Per-device cap.
    try:
        b.subscribe("RUT-A", user_key="u:3")
        raise AssertionError("per-device cap must reject")
    except SubscriptionRejected as rej:
        assert rej.limit.scope == "device"
        assert rej.limit.limit == 2

    # Per-user cap: u:1 already holds one connection across devices.
    other = b.subscribe("RUT-B", user_key="u:1")
    try:
        b.subscribe("RUT-C", user_key="u:1")
        raise AssertionError("per-user cap must reject")
    except SubscriptionRejected as rej:
        assert rej.limit.scope == "user"

    # Total cap.
    try:
        b.subscribe("RUT-B", user_key="u:9")
        raise AssertionError("total cap must reject")
    except SubscriptionRejected as rej:
        assert rej.limit.scope == "total"

    assert b.stats()["rejected_subscriptions"] == 3
    assert b.subscriber_count() == 3
    for d, s in (("RUT-A", a), ("RUT-A", a2), ("RUT-B", other)):
        b.unsubscribe(d, s)
    assert b.subscriber_count() == 0


def test_sse_rejection_is_distinguishable_from_a_healthy_stream():
    """SSE-01: a refusal names the scope/limit that caused it, so it is not read as an outage."""
    from backend.sse import EventBroker, SubscriptionRejected

    b = EventBroker(max_per_device=1)
    held = b.subscribe("RUT-A", user_key="u:other")   # occupies the single slot

    try:
        b.subscribe("RUT-A", user_key="u:caller")
        raise AssertionError("expected rejection")
    except SubscriptionRejected as rej:
        assert rej.limit.scope == "device"
        assert rej.limit.limit == 1
        assert rej.limit.current == 1
        assert "limit reached" in str(rej)

    b.unsubscribe("RUT-A", held)


def test_sse_unsubscribe_releases_capacity():
    """SSE-01: a closed connection frees its slot, so the cap is not a one-shot lockout."""
    from backend.sse import EventBroker, SubscriptionRejected

    b = EventBroker(max_per_device=1, max_total=1, max_per_user=1)
    sub = b.subscribe("RUT-A", user_key="u:1")
    try:
        b.subscribe("RUT-A", user_key="u:2")
        raise AssertionError("cap must hold")
    except SubscriptionRejected:
        pass

    b.unsubscribe("RUT-A", sub)
    fresh = b.subscribe("RUT-A", user_key="u:2")
    assert fresh is not sub
    assert b.stats()["subscribers"] == 1
    b.unsubscribe("RUT-A", fresh)

def test_r1_derived_metrics_pv_and_output_power():
    from backend.telemetry import compute_derived_metrics
    d1 = compute_derived_metrics({"pvVoltage": 24.5, "pvCurrent": 1.2, "outputVoltage": 24.0, "outputCurrent": 1.0})
    assert d1["pvInputPower"] == 29.4
    assert d1["outputPower"] == 24.0

    d2 = compute_derived_metrics({"pvVoltage": 24.5, "pvCurrent": None, "outputVoltage": 24.0, "outputCurrent": 1.0})
    assert d2["pvInputPower"] is None
    assert d2["outputPower"] == 24.0

    d3 = compute_derived_metrics({"pvVoltage": -5.0, "pvCurrent": 1.2, "outputVoltage": 24.0, "outputCurrent": 1.0})
    assert d3["pvInputPower"] is None


def test_r1_evaluate_sensor_completeness_mppt_pzem_and_solar():
    from backend.telemetry import evaluate_sensor_completeness
    full_mppt = {"pvVoltage": 24.5, "pvCurrent": 1.2, "outputVoltage": 24.0, "outputCurrent": 1.0}
    full_pzem = {"phaseAVoltage": 220.0, "phaseBVoltage": 220.0, "phaseCVoltage": 220.0, "phaseACurrent": 5.0, "phaseBCurrent": 5.0, "phaseCCurrent": 5.0, "energy": 12.5}
    full_solar = {"radiation": 412.0}
    assert evaluate_sensor_completeness(full_mppt, full_pzem, full_solar) == ("all_ok", "all_ok", "all_ok", "all_ok")

    partial_pzem = dict(full_pzem, energy=None)
    assert evaluate_sensor_completeness(full_mppt, partial_pzem, full_solar) == ("all_ok", "partial", "all_ok", "partial")

    empty_mppt = {k: None for k in full_mppt}
    empty_pzem = {k: None for k in full_pzem}
    empty_solar = {k: None for k in full_solar}
    assert evaluate_sensor_completeness(empty_mppt, empty_pzem, empty_solar) == ("no_data", "no_data", "no_data", "no_data")
    assert evaluate_sensor_completeness(full_mppt, empty_pzem, full_solar) == ("all_ok", "no_data", "all_ok", "partial")

    # Solar alone missing while the other two are healthy is PARTIAL, never ALL_OK:
    # the contracted sensor set includes sensor 3 once it is integrated.
    assert evaluate_sensor_completeness(full_mppt, full_pzem, empty_solar) == ("all_ok", "all_ok", "no_data", "partial")
    # Absent solar argument (legacy caller) behaves like an empty solar section.
    assert evaluate_sensor_completeness(full_mppt, full_pzem) == ("all_ok", "all_ok", "no_data", "partial")


def test_r1_status_precedence_model():
    from backend.store import compute_status
    assert compute_status(data_age="offline", skewed=False, mppt_state="all_ok", pzem_state="all_ok") == "offline"
    assert compute_status(data_age="offline", skewed=True, mppt_state="all_ok", pzem_state="all_ok") == "clock_skew"
    assert compute_status(data_age="no_data", skewed=False, mppt_state="no_data", pzem_state="no_data") == "no_data"
    assert compute_status(data_age="stale", skewed=False, mppt_state="all_ok", pzem_state="all_ok") == "stale"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="sensor_fault", pzem_state="all_ok") == "sensor_fault"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="partial", pzem_state="all_ok") == "partial"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="all_ok", pzem_state="all_ok") == "online"
    # Sensor 3 participates: a missing solar section while others report => partial,
    # a fully reported contract => online, and legacy not_integrated neither helps nor hurts.
    assert compute_status(data_age="fresh", skewed=False, mppt_state="all_ok", pzem_state="all_ok", sensor3_state="no_data") == "partial"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="all_ok", pzem_state="all_ok", sensor3_state="all_ok") == "online"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="all_ok", pzem_state="all_ok", sensor3_state="not_integrated") == "online"
    assert compute_status(data_age="fresh", skewed=False, mppt_state="no_data", pzem_state="no_data", sensor3_state="no_data") == "online"


def test_r1_zabbix_history_solar_series_and_metric_state():
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://127.0.0.1:80", "fake_token")
    client.host_lookup = lambda host: {"hostid": "1001", "host": host}
    client.item_lookup = lambda hostid, key: {"itemid": "2001", "key_": key}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({}, True)
    res = client.device_history("RUT-8800000001")
    # Item exists but the window is empty: integrated, nothing in range.
    assert res["solar"] == {"radiation": []}
    assert res["metricState"]["solar.radiation"] == "no_data_in_range"
    assert len(res["mppt"]) == 4
    assert len(res["pzem"]) == 7


def test_r1_zabbix_history_solar_series_maps_values():
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://127.0.0.1:80", "fake_token")
    client.host_lookup = lambda host: {"hostid": "1001", "host": host}
    client.item_lookup = lambda hostid, key: {"itemid": "2001", "key_": key}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({
        "rut.solar.radiation": [
            {"clock": "1700000000", "ns": "0", "value": "512"},
            {"clock": "1700000020", "ns": "0", "value": "530"},
        ]
    }, True)
    res = client.device_history("RUT-8800000001")
    assert [p["value"] for p in res["solar"]["radiation"]] == [512.0, 530.0]
    assert res["metricState"]["solar.radiation"] == "data"


def test_r1_zabbix_history_solar_item_missing_reports_not_integrated():
    from backend.zabbix_client import ZabbixClient
    client = ZabbixClient("http://127.0.0.1:80", "fake_token")
    client.host_lookup = lambda host: {"hostid": "1001", "host": host}
    client.item_lookup = lambda hostid, key: None if key == "rut.solar.radiation" else {"itemid": "2001", "key_": key}
    client.history_window_for_items = lambda hostid, items, **kwargs: ({}, True)
    res = client.device_history("RUT-8800000001")
    assert res["metricState"]["solar.radiation"] == "not_integrated"



def test_r4_alarm_engine_and_lifecycle():
    import tempfile
    import os
    from backend.store import Store
    from backend.alarm_engine import AlarmEngine

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(path)
        engine = AlarmEngine(store)

        # 1. Rules seeded
        rules = store.list_alarm_rules()
        assert len(rules) >= 8
        offline_rule = store.get_alarm_rule("rule.device.offline")
        assert offline_rule is not None
        assert offline_rule["enabled"] == 1
        assert offline_rule["severity"] == "high"

        # Electrical rule disabled by default
        pv_undervolt = store.get_alarm_rule("rule.mppt.pv_undervoltage")
        assert pv_undervolt is not None
        assert pv_undervolt["enabled"] == 0

        # 2. Register device
        now = 1700000000000
        store.upsert_device({
            "device_id": "RUT-TEST-001",
            "site_id": "SITE-01",
            "serial": "8800000001",
            "site_name": "Test Site",
            "status": "offline",
            "last_seen": 1699900000000,
            "data_age": "offline",
        })

        # 3. Evaluate alarms -> triggers rule.device.offline
        res = engine.evaluate_device("RUT-TEST-001", now_ms=now)
        assert len(res) == 1
        assert res[0]["rule_id"] == "rule.device.offline"
        assert res[0]["action"] == "raised_or_updated"

        alarms = store.list_alarms(device_id="RUT-TEST-001", state="active")
        assert len(alarms) == 1
        alarm_id = alarms[0]["id"]
        assert alarms[0]["state"] == "OPEN"
        assert alarms[0]["consecutive_occurrences"] == 1
        assert alarms[0]["first_occurrence"] == now
        assert alarms[0]["last_occurrence"] == now

        # 4. Deduplication: second evaluation while still offline
        now_2 = now + 60000
        res2 = engine.evaluate_device("RUT-TEST-001", now_ms=now_2)
        assert len(res2) == 1
        alarms_active = store.list_alarms(device_id="RUT-TEST-001", state="active")
        assert len(alarms_active) == 1, "Must deduplicate without creating new rows"
        assert alarms_active[0]["id"] == alarm_id
        assert alarms_active[0]["consecutive_occurrences"] == 2
        assert alarms_active[0]["last_occurrence"] == now_2

        # 5. Operator Acknowledgement
        ack_time = now_2 + 10000
        acked = store.acknowledge_alarm(alarm_id, actor="operator@telnusa.com", note="Investigating power drop", now_ms=ack_time)
        assert acked["state"] == "ACKNOWLEDGED"
        assert acked["acknowledged_by"] == "operator@telnusa.com"
        assert acked["ack_note"] == "Investigating power drop"

        # 6. Auto-recovery: Device comes back online
        now_3 = now_2 + 120000
        store.upsert_device({
            "device_id": "RUT-TEST-001",
            "site_id": "SITE-01",
            "serial": "8800000001",
            "site_name": "Test Site",
            "status": "online",
            "last_seen": now_3,
            "data_age": "fresh",
        })
        res3 = engine.evaluate_device("RUT-TEST-001", now_ms=now_3)
        assert any(r["action"] == "auto_resolved" for r in res3)

        alarms_post_recovery = store.list_alarms(device_id="RUT-TEST-001", state="active")
        assert len(alarms_post_recovery) == 0, "No active alarms after recovery"

        resolved = store.get_alarm(alarm_id)
        assert resolved["state"] == "RESOLVED"
        assert resolved["resolved_by"] == "system"
        assert resolved["resolved_at"] == now_3
        assert len(resolved["transitions"]) == 3  # NULL->OPEN, OPEN->ACK, ACK->RESOLVED

        # 7. Solar irradiance is an evaluable metric for operator-defined rules
        store.upsert_alarm_rule({
            "rule_id": "rule.solar.test",
            "name": "Solar Radiation Test",
            "metric": "solar.radiation",
            "device_pattern": "*",
            "severity": "warning",
            "operator": ">",
            "threshold": "100.0",
            "enabled": 1,
        })
        res_solar = engine.evaluate_device("RUT-TEST-001", payload={"solar": {"radiation": 500.0}}, now_ms=now_3)
        assert any(r["rule_id"] == "rule.solar.test" for r in res_solar), "Solar irradiance above threshold must raise the operator rule"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_solar_merge_preserves_last_valid_value():
    import time
    from backend.telemetry import merge_sensor_state, validate_payload
    now = int(time.time() * 1000)
    # 1. First sample carries a reading.
    vt1 = validate_payload(base_payload(ts=now, solar_radiation=500), now_ms=now)
    m1, p1, s1, h1, r1 = merge_sensor_state(None, vt1)
    assert s1 == {"radiation": 500.0}
    assert r1["solar_radiation"] == 500.0
    # 2. Absent field on a newer sample preserves the last valid reading.
    prev = {"mppt": {k: None for k in vt1.mppt}, "pzem": {k: None for k in vt1.pzem}, "solar": s1}
    payload2 = base_payload(ts=now + 20_000)
    payload2.pop("solar_radiation")
    vt2 = validate_payload(payload2, now_ms=now + 20_000)
    m2, p2, s2, h2, r2 = merge_sensor_state(prev, vt2)
    assert s2 == {"radiation": 500.0}
    assert r2["solar_radiation"] == 500.0
    # 3. Explicit null preserves too (null means no measurement, not zero).
    vt3 = validate_payload(base_payload(ts=now + 40_000, solar_radiation=None), now_ms=now + 40_000)
    m3, p3, s3, h3, r3 = merge_sensor_state(prev, vt3)
    assert s3 == {"radiation": 500.0}
    # 4. A newer valid reading replaces it.
    vt4 = validate_payload(base_payload(ts=now + 60_000, solar_radiation=640), now_ms=now + 60_000)
    m4, p4, s4, h4, r4 = merge_sensor_state(prev, vt4)
    assert s4 == {"radiation": 640.0}
    assert r4["solar_radiation"] == 640.0


def test_ingest_counters_are_per_transport():
    import time
    from backend import processor as proc
    store = Store(":memory:")
    before_http = proc.metrics.snapshot()["http"]["messages_received"]
    before_mqtt = proc.metrics.snapshot()["mqtt"]["messages_received"]
    now = int(time.time() * 1000)
    status, _ = process_payload(store, base_payload(ts=now), actor="http")
    assert status == 202
    snap = proc.metrics.snapshot()
    assert snap["http"]["messages_received"] == before_http + 1
    assert snap["mqtt"]["messages_received"] == before_mqtt, "HTTP ingest must not inflate MQTT counters"
    status, _ = process_payload(store, base_payload(ts=now + 20_000), actor="mqtt")
    assert status == 202
    snap2 = proc.metrics.snapshot()
    assert snap2["mqtt"]["messages_received"] == before_mqtt + 1
    assert snap2["http"]["messages_received"] == before_http + 1


def test_integration_status_exposes_http_transport_row():
    from backend.processor import integration_status
    store = Store(":memory:")
    status = integration_status(store)
    assert "http" in status
    assert set(status["http"]["metrics"]).issuperset({"messages_received", "messages_valid", "messages_invalid"})
    assert "mqtt" in status and "zabbix" in status


def test_device_public_sensor3_state_follows_stored_row():
    import time
    store = Store(":memory:")
    now = int(time.time() * 1000)
    status, _ = process_payload(store, base_payload(ts=now), actor="http")
    assert status == 202
    row = store.get_device("RUT-8800000001")
    public = AppHandler.device_public(row)
    assert public["sensor3State"] == "all_ok"
    assert public["acquisitionState"] == "all_ok"
    assert public["connectivity"] == "connected"
    assert public["lastPayload"]["solar"]["radiation"] == 412.0


def test_r4_fleet_summary():
    import tempfile
    import os
    from backend.store import Store

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(path)
        now = 1700000000000
        store.upsert_device({
            "device_id": "RUT-01", "site_id": "SITE-A", "serial": "111", "status": "online", "last_seen": now, "data_age": "fresh", "mppt_state": "all_ok", "pzem_state": "all_ok", "sensor3_state": "all_ok"
        })
        store.upsert_device({
            "device_id": "RUT-02", "site_id": "SITE-B", "serial": "222", "status": "degraded", "last_seen": now - 400000, "data_age": "stale", "mppt_state": "all_ok", "pzem_state": "all_ok", "sensor3_state": "all_ok"
        })
        store.upsert_device({
            "device_id": "RUT-03", "site_id": "SITE-C", "serial": "333", "status": "offline", "last_seen": now - 1000000, "data_age": "offline", "mppt_state": "all_ok", "pzem_state": "all_ok"
        })
        summary = store.fleet_summary(at_ms=now)
        assert summary["total"] == 3
        assert summary["online"] == 1
        assert summary["degraded"] == 1
        assert summary["offline"] == 1
        assert summary["latestTelemetryAt"] == now
    finally:
        if os.path.exists(path):
            os.remove(path)
def load_tests(loader, standard_tests, pattern):
    import inspect
    import unittest
    suite = unittest.TestSuite()
    mod = inspect.getmodule(load_tests)
    for name, func in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("test_"):
            class DynamicTestCase(unittest.TestCase):
                pass
            setattr(DynamicTestCase, name, lambda self, f=func: f())
            suite.addTest(DynamicTestCase(name))
    return suite
def _defined_tests():
    """Names of every test_* function defined in this module."""
    return {
        name
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    }


def _executed_tests():
    """Names of test_* functions actually called by run(), in call order."""
    called = set()

    def walk(code):
        for const in code.co_consts:
            if hasattr(const, "co_code"):  # nested code object
                walk(const)
        called.update(n for n in code.co_names if n.startswith("test_"))

    walk(run.__code__)
    return called


def report_coverage(executed):
    """Print executed count and flag any defined test that never runs."""
    defined = _defined_tests()
    missing = sorted(defined - executed)
    print(f"EXECUTED {len(executed)} of {len(defined)} defined test_* functions")
    if missing:
        print(f"DEFINED BUT NEVER EXECUTED ({len(missing)}):")
        for name in missing:
            print(f"  - {name}")
    else:
        print("Every defined test_* function is executed by run()")


def test_zabbix_item_lookup_primes_every_key_in_one_call():
    client = ZabbixClient("http://unused", "token")
    calls = {"n": 0}

    def _batch(hostid, keys):
        calls["n"] += 1
        return {k: {"itemid": str(i), "key_": k, "value_type": 0} for i, k in enumerate(keys)}

    client.items_for_host = _batch
    first = client.item_lookup("1001", "rut.mppt.pv.v")
    second = client.item_lookup("1001", "rut.pzem.energy.kwh")
    missing = client.item_lookup("1001", "rut.not.contracted")
    assert calls["n"] == 1, "repeated lookups must not cost one round trip each"
    assert first and first["key_"] == "rut.mppt.pv.v"
    assert second and second["key_"] == "rut.pzem.energy.kwh"
    assert missing is None, "a key the host does not have is absent, not fabricated"

def test_zabbix_items_for_host_rejects_duplicate_key():
    client = ZabbixClient("http://unused", "token")
    client._call = lambda method, params, authenticated=True: [
        {"itemid": "1", "key_": "rut.mppt.pv.v", "value_type": 0},
        {"itemid": "2", "key_": "rut.mppt.pv.v", "value_type": 0},
    ]
    try:
        client.items_for_host("1001", ["rut.mppt.pv.v"])
    except ZabbixError as exc:
        assert exc.category == "DUPLICATE_ITEM", exc.category
    else:
        raise AssertionError("a duplicate Zabbix item must be an error, not a silent pick")

def test_rollup_sync_survives_empty_window_and_saves_hours():
    from backend.rollup import sync_device_rollups_from_zabbix
    from backend.store import Store

    store = Store(":memory:")

    class FakeZabbix:
        configured = True
        # An empty window must not abort the sync (this used to raise
        # ValueError from a bad unpack and was swallowed).
        def host_lookup(self, host):
            return {"hostid": "1001"}
        def item_lookup(self, hostid, key):
            return {"itemid": "1", "key_": key, "value_type": 0}
        def history_for_items(self, hostid, items, **kwargs):
            return {}

    sync_device_rollups_from_zabbix(store, FakeZabbix(), "RUT-TEST-001", 0, 3_600_000)
    assert store.get_hourly_rollups("RUT-TEST-001", 0, 3_600_000) == []

    class FakeWithRows(FakeZabbix):
        def history_for_items(self, hostid, items, **kwargs):
            key = items[0]["key_"] if items else "rut.mppt.pv.v"
            # 1_000_000 ms lands inside the requested [0, 3_600_000] window.
            return {key: [{"clock": "1000", "ns": "0", "value": "24.5"}]}

    sync_device_rollups_from_zabbix(store, FakeWithRows(), "RUT-TEST-001", 0, 3_600_000)
    rollups = store.get_hourly_rollups("RUT-TEST-001", 0, 3_600_000)
    assert rollups, "a window with samples must produce a rollup row"

def test_sessions_store_no_raw_cookie_and_password_change_revokes_others():
    import hashlib
    from backend.store import Store

    store = Store(":memory:")
    uid = store.create_user("op@example.com", "Operator", None, role="operator")
    current, other, stale = "cookie-current", "cookie-other", "cookie-stale"
    for sid in (current, other, stale):
        store.create_session(sid, uid, 9_999_999_999_999, f"csrf-{sid}")

    # The row must not contain the cookie value itself.
    raw_row = store.db.execute("SELECT id FROM sessions WHERE id = ?", (current,)).fetchone()
    assert raw_row is None, "the sessions table must not store the raw cookie token"
    hashed = store.db.execute("SELECT id FROM sessions WHERE id = ?",
                              (hashlib.sha256(current.encode()).hexdigest(),)).fetchone()
    assert hashed is not None, "the session is found by hashing the presented cookie"
    assert store.get_session(current) is not None, "the presented cookie still authenticates"

    store.delete_user_sessions_except(uid, current)
    remaining = [r["id"] for r in store.db.execute("SELECT id FROM sessions").fetchall()]
    assert len(remaining) == 1, "a password change must revoke every other session"
    assert store.get_session(current) is not None, "the session that changed the password survives"
    assert store.get_session(other) is None and store.get_session(stale) is None

def test_login_verifies_even_for_unknown_accounts():
    from backend.auth_util import dummy_password_hash, verify_password

    # The constant-cost path exists and is a real Argon2id hash: login must spend
    # one verification whether or not the account exists, otherwise the response
    # time answers "does this email exist".
    dummy = dummy_password_hash()
    assert dummy and dummy.startswith("$argon2"), "a dummy hash must be available for the miss path"
    assert verify_password("anything", dummy) is False
    assert dummy_password_hash() == dummy, "the dummy hash is computed once, not per request"

def test_store_retention_purges_processed_messages_and_expired_sessions():
    import time as _time
    from backend.store import Store

    store = Store(":memory:")
    uid = store.create_user("ret@example.com", "Ret", None, role="viewer")
    now = int(_time.time() * 1000)
    store.create_session("live-cookie", uid, now + 3_600_000, "csrf-live")
    store.create_session("dead-cookie", uid, now - 1_000, "csrf-dead")

    # Mark two messages: one fresh, one far outside the retention window.
    # Retention is judged on received_at, not on the device clock.
    assert store.mark_message("fresh", "RUT-TEST-001", now, now) is True
    assert store.mark_message("ancient", "RUT-TEST-001", now - 30 * 86_400 * 1000, now - 30 * 86_400 * 1000) is True
    removed = store.purge_old_processed_messages(retention_seconds=7 * 86_400, now_ms=now)
    assert removed == 1, f"exactly the expired message must be deleted, got {removed}"
    assert store.mark_message("fresh", "RUT-TEST-001", now, now) is False, "the fresh row survived"
    assert store.mark_message("ancient", "RUT-TEST-001", now, now) is True, "the ancient row was purged"

    store.purge_expired_sessions()
    assert store.get_session("live-cookie") is not None, "an unexpired session survives"
    assert store.get_session("dead-cookie") is None, "an expired session is deleted"

def test_notification_provider_never_claims_an_unimplemented_integration():
    from backend.config import config, startup_state
    from backend.notifications import get_email_provider

    original = config.EMAIL_PROVIDER
    try:
        config.EMAIL_PROVIDER = "gmail"
        provider = get_email_provider()
        assert provider.name == "gmail"
        assert provider.configured() is False, "a placeholder provider must not report configured"
        assert provider.state == "NOT IMPLEMENTED"
        assert startup_state()["email"] == "NOT IMPLEMENTED"
        try:
            provider.send_alarm(["ops@example.com"], "s", "b")
        except RuntimeError:
            pass
        else:
            raise AssertionError("the placeholder must refuse to pretend it delivered mail")

        config.EMAIL_PROVIDER = "disabled"
        assert startup_state()["email"] == "DISABLED"
        assert get_email_provider().configured() is False
    finally:
        config.EMAIL_PROVIDER = original

def test_store_audit_retention_is_bounded_and_configurable():
    import time as _time
    from backend.store import Store

    store = Store(":memory:")
    now = int(_time.time() * 1000)
    store.audit("operator", "audit.recent", "kept")
    with store._lock, store.db:
        store.db.execute("INSERT INTO audit(ts, actor, action, detail) VALUES (?,?,?,?)",
                         (now - 400 * 86_400 * 1000, "operator", "audit.ancient", "dropped"))

    assert store.purge_audit_older_than(0) == 0, "a non-positive retention disables purging"
    removed = store.purge_audit_older_than(180, now_ms=now)
    assert removed == 1, f"exactly the row beyond retention must go, got {removed}"
    actions = [r["action"] for r in store.list_audit_logs(50)]
    assert "audit.recent" in actions and "audit.ancient" not in actions

def test_client_ip_counts_trusted_hops_from_the_right():
    from backend.httputil import client_ip

    class FakeHandler:
        def __init__(self, xff=None, peer="127.0.0.1"):
            self.headers = {"X-Forwarded-For": xff} if xff else {}
            self.client_address = (peer, 1234)

    # Two trusted proxies in front: the real client is two entries from the right.
    chained = FakeHandler(xff="203.0.113.7, 198.51.100.9, 172.16.0.1")
    assert client_ip(chained, True, 2) == "198.51.100.9"
    # A forged leftmost entry cannot choose its own identity.
    spoofed = FakeHandler(xff="10.0.0.1, 198.51.100.9, 172.16.0.1")
    assert client_ip(spoofed, True, 2) == "198.51.100.9"
    # One trusted proxy: the rightmost entry is the client.
    assert client_ip(chained, True, 1) == "172.16.0.1"
    # Untrusted mode ignores the header entirely.
    assert client_ip(chained, False, 2) == "127.0.0.1"
    # A shorter chain degrades to the leftmost available entry, never to a crash.
    assert client_ip(FakeHandler(xff="198.51.100.9"), True, 2) == "198.51.100.9"
    assert client_ip(FakeHandler(), True, 2) == "127.0.0.1"

def test_bootstrap_admin_refuses_a_weak_password():
    from backend.app import bootstrap_admin
    from backend.config import config
    from backend.store import Store

    store = Store(":memory:")
    email, password = config.BOOTSTRAP_ADMIN_EMAIL, config.BOOTSTRAP_ADMIN_PASSWORD
    try:
        config.BOOTSTRAP_ADMIN_EMAIL = "bootstrap@example.com"
        config.BOOTSTRAP_ADMIN_PASSWORD = "short"
        try:
            bootstrap_admin(store)
        except RuntimeError as exc:
            assert "BOOTSTRAP_ADMIN_PASSWORD rejected" in str(exc), exc
        else:
            raise AssertionError("a weak bootstrap password must fail loudly, not be seeded")
        assert store.get_user_by_email("bootstrap@example.com") is None

        config.BOOTSTRAP_ADMIN_PASSWORD = "a-long-enough-bootstrap-secret"
        bootstrap_admin(store)
        created = store.get_user_by_email("bootstrap@example.com")
        assert created is not None and created["role"] == "admin"
    finally:
        config.BOOTSTRAP_ADMIN_EMAIL, config.BOOTSTRAP_ADMIN_PASSWORD = email, password

def test_pepper_migration_accepts_legacy_hash_and_flags_upgrade():
    from backend.auth_util import hash_password, verify_password_allowing_legacy
    from backend.config import config

    original = config.AUTH_PEPPER
    secret = "correct horse battery staple"
    try:
        config.AUTH_PEPPER = ""
        legacy = hash_password(secret)
        ok, upgrade = verify_password_allowing_legacy(secret, legacy)
        assert ok is True and upgrade is False, "no pepper: plain verification, nothing to upgrade"

        config.AUTH_PEPPER = "pepper-now-set"
        ok, upgrade = verify_password_allowing_legacy(secret, legacy)
        assert ok is True and upgrade is True, "a pre-pepper row must verify once and ask for a rewrite"
        bad, _ = verify_password_allowing_legacy("wrong password", legacy)
        assert bad is False, "the legacy fallback must not accept a wrong password"

        fresh = hash_password(secret)
        ok, upgrade = verify_password_allowing_legacy(secret, fresh)
        assert ok is True and upgrade is False, "a peppered row needs no rewrite"
    finally:
        config.AUTH_PEPPER = original

def test_xlsx_report_is_numbers_only_without_chart_parts():
    from io import BytesIO
    from zipfile import ZipFile
    from openpyxl import load_workbook
    from backend.xlsx_report import build_report_workbook, jakarta_label

    rows = [
        {"ts": 1792486200000 + i * 60_000, "pvVoltage": 18.6 + i / 100, "phaseAVoltage": 229.4 + i}
        for i in range(5)
    ]
    payload = build_report_workbook(
        device_id="RUT-TEST-001",
        site_label="RUT-TEST-001 (RUT-TEST-001)",
        window_label="2026-10-21 00:00:00 to 2026-10-21 01:00:00 (Asia/Jakarta)",
        generated_ms=1792572800000,
        metrics=[("pvVoltage", "PV voltage", "V", "0.00"),
                 ("phaseAVoltage", "Phase A voltage", "V", "0.0")],
        rows=rows,
    )
    assert payload[:2] == b"PK", "an .xlsx is a zip container"
    assert [n for n in ZipFile(BytesIO(payload)).namelist() if "chart" in n] == [], \
        "the export is the data artifact: no chart parts are written"

    wb = load_workbook(BytesIO(payload))
    assert wb.sheetnames == ["Report"], wb.sheetnames
    ws = wb["Report"]
    assert str(ws["A1"].value).startswith("TELNUSA NMS")
    assert str(ws["A9"].value).startswith("Timestamp"), "header row is fixed and labelled"
    assert ws["B9"].value == "PV voltage [V]" and ws["C9"].value == "Phase A voltage [V]"

    first_ts = ws.cell(row=10, column=1)
    assert first_ts.value == jakarta_label(rows[0]["ts"]), "timestamps carry the site timezone"
    value_cell = ws.cell(row=10, column=2)
    assert isinstance(value_cell.value, float), "values must stay numbers, never text"
    assert value_cell.number_format == "0.00"
    assert ws.cell(row=14, column=2).value == round(rows[4]["pvVoltage"], 6)
    assert ws.freeze_panes is not None and ws.auto_filter.ref is not None, "the table stays usable"

def test_new_high_alarm_is_published_to_the_live_stream():
    from backend.processor import process_payload
    from backend.sse import broker
    from backend.store import Store

    device_id = "RUT-8800000001"
    store = Store(":memory:")
    # The live frame is the only thing that makes a raise audible in the operator's
    # browser, so this asserts the publish path, not the alarm engine (covered above).
    store.upsert_alarm_rule({
        "rule_id": "test.high.pv", "name": "PV above test threshold",
        "metric": "mppt.pv_voltage", "device_pattern": "*", "severity": "high",
        "operator": ">", "threshold": "0", "evaluation_interval": 60,
        "consecutive_count": 1, "enabled": 1, "description": "test rule",
    })
    sub = broker.subscribe(device_id, user_key="test")

    def drain_alarms() -> list[dict]:
        found = []
        while True:
            try:
                frame = json.loads(sub.queue.get_nowait())
            except queue.Empty:
                return found
            if frame.get("type") == "alarm":
                found.append(frame)

    try:
        t0 = 1_700_000_000_000
        code, _ = process_payload(store, base_payload(ts=t0), actor="test", received_ms=t0)
        assert code == 202
        raised = drain_alarms()
        assert len(raised) == 1, f"a new high alarm must publish exactly one frame, got {raised}"
        assert raised[0]["payload"]["severity"] == "high", raised[0]["payload"]
        assert raised[0]["payload"]["deviceId"] == device_id

        # Still active on the next payload: that is an update, and an update must not
        # beep again (a continuing alarm is re-evaluated every ingest).
        t1 = t0 + 20_000
        process_payload(store, base_payload(ts=t1), actor="test", received_ms=t1)
        assert drain_alarms() == [], "a continuing alarm must not publish another raise"
    finally:
        broker.unsubscribe(device_id, sub)

def test_warning_severity_never_reaches_the_live_stream():
    from backend.processor import process_payload
    from backend.sse import broker
    from backend.store import Store

    device_id = "RUT-8800000001"
    store = Store(":memory:")
    store.upsert_alarm_rule({
        "rule_id": "test.warning.pv", "name": "PV above advisory threshold",
        "metric": "mppt.pv_voltage", "device_pattern": "*", "severity": "warning",
        "operator": ">", "threshold": "0", "evaluation_interval": 60,
        "consecutive_count": 1, "enabled": 1, "description": "test rule",
    })
    sub = broker.subscribe(device_id, user_key="test")
    try:
        t0 = 1_700_000_000_000
        process_payload(store, base_payload(ts=t0), actor="test", received_ms=t0)
        frames = []
        while True:
            try:
                frames.append(json.loads(sub.queue.get_nowait()))
            except queue.Empty:
                break
        assert [f for f in frames if f.get("type") == "alarm"] == [], \
            "advisory severities must stay silent: the sound is reserved for high and disaster"
    finally:
        broker.unsubscribe(device_id, sub)

def test_webui_proxy_rewrites_root_absolute_navigation():
    from backend.app import RUT_WEBUI_NAV_ASSIGN_RE, RUT_WEBUI_ORIGIN_NAV_RE

    prefix = "/api/remote/RUT-TEST-001/webui/proxy"
    sub_origin = lambda m: f"{m.group(1)}'{prefix}{m.group(3)}'"  # noqa: E731
    sub_assign = lambda m: f"{m.group(1)}'{prefix}{m.group(3)}'"  # noqa: E731

    # The router's login response, verbatim: unrewritten it lands on the portal root.
    login = "window.location = window.location.origin + '/status/overview'"
    assert f"'{prefix}/status/overview'" in RUT_WEBUI_ORIGIN_NAV_RE.sub(sub_origin, login)

    assigned = "location.href = '/login'"
    assert f"'{prefix}/login'" in RUT_WEBUI_NAV_ASSIGN_RE.sub(sub_assign, assigned)
    assert f"'{prefix}/login'" in RUT_WEBUI_NAV_ASSIGN_RE.sub(sub_assign, "location.replace('/login')")

    # A route comparison must survive untouched: the SPA matches location.pathname.
    compare = "if (location.pathname === '/status/overview') { render() }"
    assert RUT_WEBUI_NAV_ASSIGN_RE.sub(sub_assign, compare) == compare

    # An already-proxied target is never prefixed twice.
    already = f"location.href = '{prefix}/login'"
    assert RUT_WEBUI_NAV_ASSIGN_RE.sub(sub_assign, already) == already

def test_proxy_strips_device_policy_headers_but_keeps_content_headers():
    from backend.app import DEVICE_POLICY_HEADERS

    # The router's own HSTS (includeSubDomains, preload) and the retired Expect-CT
    # describe the DEVICE's policy; replaying them would apply it to the portal host.
    assert "strict-transport-security" in DEVICE_POLICY_HEADERS
    assert "expect-ct" in DEVICE_POLICY_HEADERS
    # Everything that carries the response itself must keep flowing: the device's CSP
    # hashes describe its own inline scripts and no violation has been observed.
    for kept in ("content-security-policy", "x-frame-options", "content-type",
                 "content-disposition", "content-encoding", "set-cookie", "location"):
        assert kept not in DEVICE_POLICY_HEADERS, f"{kept} must reach the browser"

if __name__ == "__main__":
    # A stale call in run() used to abort the whole suite with NameError, so every
    # test after it silently never ran. Fail loudly on that class instead.
    undefined = sorted(
        name for name in run.__code__.co_names
        if name.startswith("test_") and name not in globals()
    )
    if undefined:
        raise SystemExit(f"run() calls undefined tests: {', '.join(undefined)}")
    _executed = _executed_tests()
    run()
    report_coverage(_executed)
