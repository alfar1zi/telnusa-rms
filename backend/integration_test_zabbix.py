"""Explicit real-service Zabbix 7.4 integration harness.

NON-DESTRUCTIVE by default. Requires real env; prints NOT CONFIGURED when missing.
"""
from __future__ import annotations

import os
import sys
import time

from .config import config
from .zabbix_client import ZabbixError, zabbix
from .zabbix_contract import ITEM_SPECS, technical_host_name

TEST_DEVICE_ID = "RUT-TEST-INTEGRATION"
TEST_HOST = technical_host_name(TEST_DEVICE_ID)
TEST_FIELD = "sps_mppt_pv_v"
TEST_VALUE = 12.345


def require_env() -> bool:
    required = ["ZABBIX_URL", "ZABBIX_TOKEN", "ZABBIX_SENDER_HOST", "ZABBIX_HOST_GROUP", "ZABBIX_TEMPLATE"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"NOT CONFIGURED: missing {', '.join(missing)}")
        return False
    return True


def main() -> int:
    if not require_env():
        return 0
    allow_create = os.environ.get("ZABBIX_TEST_ALLOW_CREATE", "0") == "1"
    verify_history = os.environ.get("ZABBIX_TEST_VERIFY_HISTORY", "0") == "1"
    print("INTEGRATION TEST ONLY: using RUT-TEST-INTEGRATION synthetic payload")

    health = zabbix.health_check()
    print(f"Zabbix API state={health.state} version={health.version} latency_ms={health.latency_ms}")
    if health.state != "AUTHENTICATED":
        print(f"ERROR: {health.error}")
        return 1

    group = zabbix.host_group_lookup(config.ZABBIX_HOST_GROUP)
    if not group:
        print("HOST_GROUP_NOT_FOUND")
        return 1
    print(f"host group ok: {group['name']}")

    template = zabbix.template_lookup(config.ZABBIX_TEMPLATE)
    if not template:
        print("TEMPLATE_NOT_FOUND")
        return 1
    print(f"template ok: {template.get('host') or template.get('name')}")

    try:
        hostid = zabbix.ensure_device_host({"device_id": TEST_DEVICE_ID, "name": "INTEGRATION TEST ONLY - RUT906"}, allow_create=allow_create)
    except ZabbixError as exc:
        print(exc.category)
        if exc.category == "HOST_NOT_FOUND" and not allow_create:
            print("Set ZABBIX_TEST_ALLOW_CREATE=1 to create only the controlled test host.")
        return 1
    print(f"host ok: {TEST_HOST} hostid={hostid}")

    try:
        zabbix.reconcile_items(hostid, [TEST_FIELD], allow_create=allow_create)
    except ZabbixError as exc:
        print(exc.category)
        if exc.category == "ITEM_NOT_CONFIGURED" and not allow_create:
            print("Set ZABBIX_TEST_ALLOW_CREATE=1 to create only approved controlled test item definitions.")
        return 1
    spec = ITEM_SPECS[TEST_FIELD]
    print(f"item ok: {spec.zabbix_key}")

    now_ms = int(time.time() * 1000)
    result = zabbix.sender_submit([{
        "host": TEST_HOST,
        "key": spec.zabbix_key,
        "value": TEST_VALUE,
        "clock": now_ms // 1000,
        "ns": (now_ms % 1000) * 1_000_000,
    }])
    print(f"sender processed={result.processed} failed={result.failed} total={result.total}")
    if not result.ok:
        return 1

    if verify_history:
        item = zabbix.item_lookup(hostid, spec.zabbix_key)
        rows = zabbix.recent_history(hostid, [item["itemid"]]) if item else []
        if not any(str(r.get("value")) == str(TEST_VALUE) for r in rows):
            print("HISTORY_VALUE_NOT_FOUND")
            return 1
        print("history ok")
    else:
        print("history verification skipped: set ZABBIX_TEST_VERIFY_HISTORY=1")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ZabbixError as exc:
        print(f"{exc.category}: {exc}")
        raise SystemExit(1)
