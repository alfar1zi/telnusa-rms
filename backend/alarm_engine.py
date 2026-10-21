"""TELNUSA NMS R4: Canonical Alarm Engine & Lifecycle Management

Provides deterministic alarm evaluation, deduplication, state transitions
(OPEN -> ACKNOWLEDGED -> RESOLVED), auto-recovery on telemetry normalization,
and persistent audit history.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import time
from typing import Any

logger = logging.getLogger("telnusa.alarm_engine")

#  DEFAULT SEED RULES
# Default infrastructure / data-quality rules are ENABLED by default.
# Electrical rules (MPPT / PZEM) are DISABLED by default until site engineering limits are approved.
# Solar radiation has no default rule: irradiance limits are site-specific, so
# operators add their own rules against `solar.radiation` when a baseline exists.

DEFAULT_ALARM_RULES = [
    {
        "rule_id": "rule.device.offline",
        "name": "Device Offline",
        "metric": "device.status",
        "device_pattern": "*",
        "severity": "high",
        "operator": "==",
        "threshold": "offline",
        "evaluation_interval": 30,
        "consecutive_count": 1,
        "enabled": 1,
        "description": "RUT906 gateway has exceeded offline timeout with no incoming telemetry.",
    },
    {
        "rule_id": "rule.telemetry.stale",
        "name": "Telemetry Stale",
        "metric": "telemetry.data_age",
        "device_pattern": "*",
        "severity": "warning",
        "operator": "==",
        "threshold": "stale",
        "evaluation_interval": 30,
        "consecutive_count": 1,
        "enabled": 1,
        "description": "Incoming telemetry interval exceeded live freshness threshold.",
    },
    {
        "rule_id": "rule.clock.skew",
        "name": "Device Clock Skew",
        "metric": "device.clock_skew",
        "device_pattern": "*",
        "severity": "warning",
        "operator": "==",
        "threshold": "true",
        "evaluation_interval": 60,
        "consecutive_count": 1,
        "enabled": 1,
        "description": "RUT906 internal clock deviates significantly from NMS server time.",
    },
    {
        "rule_id": "rule.sensor.fault",
        "name": "Sensor Acquisition Fault",
        "metric": "device.acquisition_state",
        "device_pattern": "*",
        "severity": "high",
        "operator": "==",
        "threshold": "sensor_fault",
        "evaluation_interval": 60,
        "consecutive_count": 1,
        "enabled": 1,
        "description": "Connected Modbus sensor(s) reported hardware or communication fault.",
    },
    {
        "rule_id": "rule.mppt.pv_undervoltage",
        "name": "MPPT PV Undervoltage",
        "metric": "mppt.pv_voltage",
        "device_pattern": "*",
        "severity": "warning",
        "operator": "<",
        "threshold": "10.0",
        "evaluation_interval": 60,
        "consecutive_count": 2,
        "enabled": 0,  # Disabled by default until site baseline approved
        "description": "Solar PV array input voltage below operational baseline.",
    },
    {
        "rule_id": "rule.mppt.pv_overvoltage",
        "name": "MPPT PV Overvoltage",
        "metric": "mppt.pv_voltage",
        "device_pattern": "*",
        "severity": "high",
        "operator": ">",
        "threshold": "150.0",
        "evaluation_interval": 60,
        "consecutive_count": 2,
        "enabled": 0,  # Disabled by default until site baseline approved
        "description": "Solar PV array input voltage exceeds safe operational maximum.",
    },
    {
        "rule_id": "rule.pzem.ac_undervoltage",
        "name": "PZEM AC Undervoltage",
        "metric": "pzem.a_voltage",
        "device_pattern": "*",
        "severity": "warning",
        "operator": "<",
        "threshold": "180.0",
        "evaluation_interval": 60,
        "consecutive_count": 2,
        "enabled": 0,  # Disabled by default until site baseline approved
        "description": "AC Line Voltage below nominal operational range.",
    },
    {
        "rule_id": "rule.pzem.ac_overvoltage",
        "name": "PZEM AC Overvoltage",
        "metric": "pzem.a_voltage",
        "device_pattern": "*",
        "severity": "high",
        "operator": ">",
        "threshold": "250.0",
        "evaluation_interval": 60,
        "consecutive_count": 2,
        "enabled": 0,  # Disabled by default until site baseline approved
        "description": "AC Line Voltage exceeds safe operational maximum.",
    },
]


def extract_metric_value(metric: str, device: dict[str, Any], payload: dict[str, Any] | None) -> Any:
    """Extract metric value from device state or telemetry payload."""
    if metric == "device.status":
        return device.get("status")
    if metric == "telemetry.data_age":
        return device.get("data_age")
    if metric == "device.clock_skew":
        return "true" if device.get("clock_skew") else "false"
    if metric == "device.acquisition_state":
        return device.get("acquisition_state") or "no_data"

    # Telemetry metrics from payload
    if not payload or not isinstance(payload, dict):
        return None

    mppt = payload.get("mppt") if isinstance(payload.get("mppt"), dict) else {}
    pzem = payload.get("pzem") if isinstance(payload.get("pzem"), dict) else {}
    solar = payload.get("solar") if isinstance(payload.get("solar"), dict) else {}

    if metric == "mppt.pv_voltage":
        return mppt.get("pv_voltage") or mppt.get("pvVoltage")
    if metric == "mppt.pv_current":
        return mppt.get("pv_current") or mppt.get("pvCurrent")
    if metric == "mppt.output_voltage":
        return mppt.get("output_voltage") or mppt.get("outputVoltage")
    if metric == "mppt.output_current":
        return mppt.get("output_current") or mppt.get("outputCurrent")
    if metric == "pzem.a_voltage":
        return pzem.get("a_voltage") or pzem.get("aVoltage")
    if metric == "pzem.b_voltage":
        return pzem.get("b_voltage") or pzem.get("bVoltage")
    if metric == "pzem.c_voltage":
        return pzem.get("c_voltage") or pzem.get("cVoltage")
    if metric == "pzem.a_current":
        return pzem.get("a_current") or pzem.get("aCurrent")
    if metric == "pzem.b_current":
        return pzem.get("b_current") or pzem.get("bCurrent")
    if metric == "pzem.c_current":
        return pzem.get("c_current") or pzem.get("cCurrent")
    if metric in ("pzem.energy", "pzem.kwh"):
        return pzem.get("kwh") or pzem.get("energy")
    if metric in ("solar.radiation", "solar.irradiance"):
        return solar.get("radiation")

    return None


def evaluate_condition(operator: str, threshold: str | None, value: Any) -> bool:
    """Evaluate comparison operator with safe type handling."""
    if value is None or threshold is None:
        return False

    str_val = str(value).strip().lower()
    str_thresh = str(threshold).strip().lower()

    if operator == "==":
        if str_val == str_thresh:
            return True
        try:
            return abs(float(value) - float(threshold)) < 1e-6
        except (ValueError, TypeError):
            return False

    if operator == "!=":
        if str_val == str_thresh:
            return False
        try:
            return abs(float(value) - float(threshold)) >= 1e-6
        except (ValueError, TypeError):
            return True

    # Numeric comparisons
    try:
        f_val = float(value)
        f_thresh = float(threshold)
    except (ValueError, TypeError):
        return False

    if operator == ">":
        return f_val > f_thresh
    if operator == "<":
        return f_val < f_thresh
    if operator == ">=":
        return f_val >= f_thresh
    if operator == "<=":
        return f_val <= f_thresh

    return False


class AlarmEngine:
    """Canonical Alarm Engine for TELNUSA NMS."""

    def __init__(self, store: Any):
        self.store = store

    def seed_default_rules(self) -> None:
        """Seed default built-in rules if not already present."""
        now_ms = int(time.time() * 1000)
        for rule in DEFAULT_ALARM_RULES:
            existing = self.store.get_alarm_rule(rule["rule_id"])
            if not existing:
                self.store.upsert_alarm_rule({**rule, "created_at": now_ms, "updated_at": now_ms})

    def evaluate_device(
        self,
        device_id: str,
        payload: dict[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Evaluate all active alarm rules against a single device.
        Performs deduplication: existing OPEN/ACKNOWLEDGED alarms are updated (occurrence incremented),
        new violations produce OPEN alarms, and cleared violations trigger auto-recovery (RESOLVED).
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        device = self.store.get_device(device_id, at_ms=now_ms)
        if not device:
            return []

        if payload is None:
            last_p = device.get("last_payload")
            if isinstance(last_p, str):
                try:
                    payload = json.loads(last_p)
                except (ValueError, TypeError):
                    payload = None
            elif isinstance(last_p, dict):
                payload = last_p

        rules = self.store.list_alarm_rules(enabled_only=True)
        results = []

        for rule in rules:
            rule_id = rule["rule_id"]
            metric = rule["metric"]
            pattern = rule.get("device_pattern") or "*"

            # Device pattern filter
            if not fnmatch.fnmatch(device_id, pattern):
                continue

            value = extract_metric_value(metric, device, payload)
            is_violation = evaluate_condition(rule["operator"], rule.get("threshold"), value)

            if is_violation:
                evidence = f"{metric}={value} (rule: {rule['operator']} {rule['threshold']})"
                alarm = self.store.record_alarm_occurrence(
                    device_id=device_id,
                    site_id=device.get("site_id") or device_id,
                    rule_id=rule_id,
                    severity=rule["severity"],
                    title=rule["name"],
                    observed_value=str(value),
                    evidence=evidence,
                    now_ms=now_ms,
                )
                results.append({"rule_id": rule_id, "action": "raised_or_updated", "alarm": alarm})
            else:
                # If there is an active alarm for this device & rule, auto-resolve it
                if value is not None or metric in ("device.status", "telemetry.data_age", "device.clock_skew"):
                    cleared_reason = f"Normal state observed ({metric}={value})"
                    resolved = self.store.resolve_alarm_if_active(
                        device_id=device_id,
                        rule_id=rule_id,
                        reason=cleared_reason,
                        now_ms=now_ms,
                        actor="system",
                    )
                    if resolved:
                        results.append({"rule_id": rule_id, "action": "auto_resolved", "alarm": resolved})

        return results

    def evaluate_all(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        """Evaluate alarm rules across all registered devices (e.g. for offline/stale detection)."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        devices = self.store.list_devices(at_ms=now_ms)
        all_results = []
        for dev in devices:
            res = self.evaluate_device(dev["device_id"], now_ms=now_ms)
            all_results.extend(res)
        return all_results
