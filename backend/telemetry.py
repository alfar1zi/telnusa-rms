"""Strict RUT906 MQTT payload validation and normalization."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any

from .zabbix_contract import ZABBIX_KEYS

IDENTITY_FIELDS = {"site_id", "sn", "ts"}

MPPT_MAP = {
    "sps_mppt_pv_v": "pvVoltage",
    "sps_mppt_pv_a": "pvCurrent",
    "sps_mppt_out_v": "outputVoltage",
    "sps_mppt_out_a": "outputCurrent",
}

PZEM_MAP = {
    "a_voltage": "phaseAVoltage",
    "b_voltage": "phaseBVoltage",
    "c_voltage": "phaseCVoltage",
    "a_current": "phaseACurrent",
    "b_current": "phaseBCurrent",
    "c_current": "phaseCCurrent",
    "kwh": "energy",
}

# Solar irradiance (Honde RD-PSR-01, Modbus slave 3). The RUT Lua formatter
# passes the UInt16 register through 1:1 (W/m², sensor range 0-1800); scaling
# stays at the acquisition edge so this contract carries engineering units.
SOLAR_MAP = {
    "solar_radiation": "radiation",
}

# Router health, read on the device by the data-sender formatter from the local
# RutOS API and carried in the same telemetry payload. Numbers and text are
# separate because the contract keeps rssi/temperature numeric and the modem's
# operator/state text.
HEALTH_NUMBER_MAP = {
    "sys_uptime_s": "uptimeSeconds",
    "sys_cores": "cores",
    "sys_load1": "load1",
    "sys_load5": "load5",
    "sys_load15": "load15",
    "sys_ram_pct": "ramPercent",
    "sys_flash_pct": "flashPercent",
    "modem_temperature_c": "temperatureC",
    "modem_signal_dbm": "rssi",
    "modem_rsrp_dbm": "rsrp",
}
HEALTH_TEXT_MAP = {
    "modem_netstate": "networkState",
    "modem_operator": "operator",
    "modem_conntype": "connectionType",
}

APPROVED_FIELDS = (
    IDENTITY_FIELDS
    | set(MPPT_MAP)
    | set(PZEM_MAP)
    | set(SOLAR_MAP)
    | set(HEALTH_NUMBER_MAP)
    | set(HEALTH_TEXT_MAP)
)
HEALTH_TEXT_MAX = 48
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def compute_derived_metrics(mppt: dict[str, float | None]) -> dict[str, float | None]:
    """Derive electrical power (W) when both voltage and current are present and valid."""
    pv_v = mppt.get("pvVoltage")
    pv_a = mppt.get("pvCurrent")
    pv_power: float | None = None
    if pv_v is not None and pv_a is not None and isinstance(pv_v, (int, float)) and isinstance(pv_a, (int, float)):
        if pv_v >= 0 and pv_a >= 0:
            pv_power = round(float(pv_v) * float(pv_a), 2)

    out_v = mppt.get("outputVoltage")
    out_a = mppt.get("outputCurrent")
    out_power: float | None = None
    if out_v is not None and out_a is not None and isinstance(out_v, (int, float)) and isinstance(out_a, (int, float)):
        if out_v >= 0 and out_a >= 0:
            out_power = round(float(out_v) * float(out_a), 2)

    return {
        "pvInputPower": pv_power,
        "outputPower": out_power,
    }


def evaluate_sensor_completeness(
    mppt: dict[str, float | None],
    pzem: dict[str, float | None],
    solar: dict[str, float | None] | None = None,
) -> tuple[str, str, str, str]:
    """Evaluate MPPT (4 fields), PZEM (7 fields), solar (1 field), and overall acquisition completeness.

    Returns (mppt_state, pzem_state, sensor3_state, acquisition_state).
    States: all_ok, partial, no_data.
    Inviolable invariant: PARTIAL != ALL_OK.
    """
    def _state(values: dict[str, float | None], expected: int) -> str:
        valid = sum(1 for v in values.values() if v is not None)
        if valid == expected:
            return "all_ok"
        if valid > 0:
            return "partial"
        return "no_data"

    mppt_state = _state(mppt, 4)
    pzem_state = _state(pzem, 7)
    solar_values = solar if isinstance(solar, dict) else {}
    sensor3_state = _state(solar_values, len(SOLAR_MAP)) if solar_values else "no_data"

    states = (mppt_state, pzem_state, sensor3_state)
    if all(s == "all_ok" for s in states):
        acquisition_state = "all_ok"
    elif all(s == "no_data" for s in states):
        acquisition_state = "no_data"
    else:
        acquisition_state = "partial"

    return mppt_state, pzem_state, sensor3_state, acquisition_state


@dataclass(frozen=True)
class UnknownField:
    field: str
    observed_type: str


@dataclass(frozen=True)
class ValidatedTelemetry:
    message_id: str
    device_id: str
    site_id: str
    serial: str
    source_ts: int
    received_ts: int
    processed_ts: int
    topic: str | None
    mppt: dict[str, float | None]
    pzem: dict[str, float | None]
    solar: dict[str, float | None] | None
    health: dict[str, object]
    raw_values: dict[str, float | None]
    unmapped: list[UnknownField]
    stale: bool
    future: bool
    derived: dict[str, float | None] = None

class ValidationError(ValueError):
    pass


def _validate_id_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if not value or len(value) > 128:
        raise ValidationError(f"{name} length must be between 1 and 128 characters")
    if not SAFE_ID_PATTERN.match(value):
        raise ValidationError(f"{name} contains invalid characters: {value!r}")
    return value


def _validate_topic(topic: str, site_id: str) -> None:
    parts = topic.split("/")
    if len(parts) != 4:
        raise ValidationError(f"Invalid topic segment count (expected 4): {topic}")
    if parts[0] != "entermqtt" or parts[1] != "mitratel" or parts[3] != "telemetry":
        raise ValidationError(f"Invalid topic structure (expected entermqtt/mitratel/<SITE_ID>/telemetry): {topic}")
    topic_site = parts[2]
    if "+" in topic_site or "#" in topic_site:
        raise ValidationError(f"Wildcard topic is not an acceptable incoming packet identity: {topic}")
    if not SAFE_ID_PATTERN.match(topic_site):
        raise ValidationError(f"Topic site ID contains invalid characters: {topic_site}")
    if topic_site != site_id:
        raise ValidationError(f"Topic site ID mismatch: topic={topic_site} payload={site_id}")


def _source_ts_ms(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("ts must be numeric")
    if isinstance(value, (int, float)):
        fval = float(value)
        if not math.isfinite(fval):
            raise ValidationError("ts must be finite")
        if isinstance(value, int) or fval.is_integer():
            ts = int(fval)
        else:
            raise ValidationError("ts must be an integer timestamp")
    else:
        raise ValidationError("ts must be numeric")
    if ts < 10_000_000_000:
        ts *= 1000
    if ts < 1_577_836_800_000:  # 2020-01-01 00:00:00 UTC
        raise ValidationError("ts is too far in the past")
    return ts


def _num(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be numeric or null")
    if isinstance(value, (int, float)):
        fval = float(value)
        if not math.isfinite(fval):
            raise ValidationError(f"{field} must be finite")
        return fval
    raise ValidationError(f"{field} must be numeric or null")


def _health_text(value: Any, field: str) -> str | None:
    """Modem operator/state labels. Not an identifier: commas and spaces are
    expected, control characters are not, and the length is bounded."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text or null")
    text = value.strip()
    if not text:
        return None
    if len(text) > HEALTH_TEXT_MAX:
        raise ValidationError(f"{field} is longer than {HEALTH_TEXT_MAX} characters")
    if any(ord(ch) < 32 for ch in text):
        raise ValidationError(f"{field} contains control characters")
    return text

def _observed_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _strict_json_constant(c: str) -> None:
    raise ValueError(f"Non-finite JSON constant rejected: {c}")


def parse_json_payload(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("payload must be valid UTF-8") from exc
    try:
        data = json.loads(text, parse_constant=_strict_json_constant)
    except ValueError as exc:
        raise ValidationError("payload must be valid JSON with finite numeric values") from exc
    if not isinstance(data, dict):
        raise ValidationError("payload must be a JSON object")
    return data


def validate_payload(
    payload: dict[str, Any],
    *,
    now_ms: int | None = None,
    processed_ms: int | None = None,
    stale_after_ms: int = 15 * 60 * 1000,
    future_slop_ms: int = 5 * 60 * 1000,
    topic: str | None = None,
) -> ValidatedTelemetry:
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")

    site_id = _validate_id_text(payload.get("site_id"), "site_id")
    serial = _validate_id_text(payload.get("sn"), "sn")
    source_ts = _source_ts_ms(payload.get("ts"))

    if topic is not None:
        _validate_topic(topic, site_id)

    received_ts = int(now_ms if now_ms is not None else time.time() * 1000)
    processed_ts = int(processed_ms if processed_ms is not None else time.time() * 1000)
    stale = received_ts - source_ts > stale_after_ms
    future = source_ts - received_ts > future_slop_ms
    if future:
        raise ValidationError("ts is too far in the future")

    mppt = {name: None for name in MPPT_MAP.values()}
    pzem = {name: None for name in PZEM_MAP.values()}
    solar = {name: None for name in SOLAR_MAP.values()}
    raw_values: dict[str, float | None] = {}

    for raw, name in MPPT_MAP.items():
        if raw in payload:
            val = _num(payload[raw], raw)
            mppt[name] = val
            raw_values[raw] = val
    for raw, name in PZEM_MAP.items():
        if raw in payload:
            val = _num(payload[raw], raw)
            pzem[name] = val
            raw_values[raw] = val
    for raw, name in SOLAR_MAP.items():
        if raw in payload:
            val = _num(payload[raw], raw)
            solar[name] = val
            raw_values[raw] = val

    # Pre-populated like the sensor sections so an absent field is a recorded
    # null that the merge preserves, never a silently missing key.
    health: dict[str, object] = {name: None for name in HEALTH_NUMBER_MAP.values()}
    health.update({name: None for name in HEALTH_TEXT_MAP.values()})
    for raw, name in HEALTH_NUMBER_MAP.items():
        if raw in payload:
            val = _num(payload[raw], raw)
            health[name] = val
            if val is not None:
                raw_values[raw] = val
    for raw, name in HEALTH_TEXT_MAP.items():
        if raw in payload:
            health[name] = _health_text(payload[raw], raw)

    unmapped = [UnknownField(k, _observed_type(payload[k])) for k in sorted(payload) if k not in APPROVED_FIELDS]
    # Fingerprint covers effectively-measured values only: explicit null and
    # absent both mean "no measurement" and must fingerprint identically so a
    # null/absent re-send of the same ts dedupes instead of forging new state.
    finger_values = {k: v for k, v in raw_values.items() if v is not None}
    normalized = {"site_id": site_id, "sn": serial, "ts": source_ts, **finger_values}
    fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    device_id = f"RUT-{serial}"
    return ValidatedTelemetry(
        message_id=f"{device_id}:{source_ts}:{fingerprint}",
        device_id=device_id,
        site_id=site_id,
        serial=serial,
        source_ts=source_ts,
        received_ts=received_ts,
        processed_ts=processed_ts,
        topic=topic,
        mppt=mppt,
        pzem=pzem,
        solar=solar,
        health=health,
        derived=compute_derived_metrics(mppt),
        raw_values=raw_values,
        unmapped=unmapped,
        stale=stale,
        future=future,
    )


def latest_shape(v: ValidatedTelemetry, freshness: str) -> dict:
    derived = v.derived if v.derived is not None else compute_derived_metrics(v.mppt)
    return {
        "deviceId": v.device_id,
        "timestamp": v.source_ts,
        "receivedAt": v.received_ts,
        "processedAt": v.processed_ts,
        "dataAge": freshness,
        "mppt": v.mppt,
        "pzem": v.pzem,
        "solar": v.solar,
        "health": v.health,
        "derived": derived,
    }

REVERSE_MPPT = {v: k for k, v in MPPT_MAP.items()}
REVERSE_PZEM = {v: k for k, v in PZEM_MAP.items()}
REVERSE_SOLAR = {v: k for k, v in SOLAR_MAP.items()}

def merge_sensor_state(
    prev_payload: dict | None,
    vt: ValidatedTelemetry,
    *,
    equal_ts: bool = False,
) -> tuple[dict[str, float | None], dict[str, float | None], dict[str, float | None], dict[str, object], dict[str, float | None]]:
    """Merge validated sensors over a previous last_payload snapshot.

    Canonical preserve-last-valid-value contract (RC-2):
    - absent field => preserve previous value
    - explicit null => preserve previous value
    - valid field => replace previous value (newer ts)
    - equal-ts conflicting valid values => first-writer-wins, so the outcome
      is deterministic per field instead of last-writer-wins races.
    Returns (mppt, pzem, solar, health, raw_values) keyed for
    latest_shape/zabbix_values. `health` follows the same preserve-last-valid
    rule as the sensor sections: a field the router did not report this cycle
    keeps its previous value instead of blanking the health card.
    """
    merged_mppt = dict(vt.mppt)
    merged_pzem = dict(vt.pzem)
    merged_solar = dict(vt.solar) if isinstance(vt.solar, dict) else {name: None for name in SOLAR_MAP.values()}
    merged_health: dict[str, object] = dict(vt.health) if isinstance(vt.health, dict) else {}
    prev_sections: dict[str, dict] = {}
    if isinstance(prev_payload, dict):
        for section in ("mppt", "pzem", "solar", "health"):
            prev_section = prev_payload.get(section)
            prev_sections[section] = prev_section if isinstance(prev_section, dict) else {}
    for section, merged in (("mppt", merged_mppt), ("pzem", merged_pzem), ("solar", merged_solar), ("health", merged_health)):
        prev_section = prev_sections.get(section, {})
        for name, value in merged.items():
            prev_value = prev_section.get(name)
            if value is None:
                if prev_value is not None:
                    merged[name] = prev_value
            elif equal_ts and prev_value is not None and prev_value != value:
                merged[name] = prev_value
    merged_raw: dict[str, float | None] = {}
    for name, value in merged_mppt.items():
        if value is not None:
            merged_raw[REVERSE_MPPT[name]] = value
    for name, value in merged_pzem.items():
        if value is not None:
            merged_raw[REVERSE_PZEM[name]] = value
    for name, value in merged_solar.items():
        if value is not None:
            merged_raw[REVERSE_SOLAR[name]] = value
    for raw, name in HEALTH_NUMBER_MAP.items():
        value = merged_health.get(name)
        if value is not None:
            merged_raw[raw] = value
    return merged_mppt, merged_pzem, merged_solar, merged_health, merged_raw
