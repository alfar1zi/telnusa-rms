"""Server-side Zabbix 7.4 API and sender client. No fake success."""
from __future__ import annotations

import itertools
import json
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .config import config
from .telemetry import ValidatedTelemetry
from .zabbix_contract import ITEM_SPECS, ZABBIX_KEYS, supported_version, technical_host_name


# Zabbix raw history is bounded per item; a wider window is served partially.
MAX_RAW_POINTS = 1000
# Hard ceiling for one raw history request. The window actually requested sets
# the effective limit (a 12 h window at the device's 20 s cadence needs ~2160
# points); this only stops an unbounded query.
RAW_HISTORY_HARD_LIMIT = 6000
# Paged window fetch: rows per history.get page, and how many pages one window
# may cost. The budget covers the raw tier (30 h) at twice the measured ingest
# rate, so a full window is never silently cut short; only a pathological item
# hits the ceiling, and then the response says truncated.
MAX_RAW_PAGE_ROWS = 4000
RAW_WINDOW_MAX_PAGES = 10
# One Zabbix round trip per item per page was the whole cost of a chart request
# (measured 2026-10-21: ~37 sequential calls = 36.9 s for one 24 h window). Items
# are independent, so a small pool fetches them concurrently; the cap keeps the
# Zabbix server from being hit by twelve heavy queries at once.
MAX_WINDOW_FETCH_WORKERS = 4
# Item id/key/type are stable metadata; the TTL only avoids re-reading them on
# every request while still noticing a deleted or re-created item quickly.
ITEM_METADATA_TTL_SECONDS = 60.0

# an empty series is ambiguous on its own: it means either "no Zabbix
# item exists" or "an item exists but sent nothing in this window". These states
# let the API name which one, so the UI never reports "no data in range" about
# a metric that was never wired up at all.
METRIC_NOT_INTEGRATED = "not_integrated"
METRIC_NO_DATA_IN_RANGE = "no_data_in_range"
METRIC_DATA = "data"
METRIC_ERROR = "error"

class ZabbixError(RuntimeError):
    def __init__(self, category: str, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.category = category
        self.code = code
        self.data = data

    def public(self) -> dict:
        out: dict[str, Any] = {"category": self.category, "message": str(self)}
        if self.code is not None:
            out["code"] = self.code
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass(frozen=True)
class SenderResult:
    processed: int
    failed: int
    total: int
    response: str

    @property
    def ok(self) -> bool:
        return self.response == "success" and self.failed == 0 and self.processed == self.total


@dataclass(frozen=True)
class ApiHealth:
    state: str
    version: str | None
    latency_ms: int | None
    error: dict | None = None


class ZabbixClient:
    def __init__(self, url: str, token: str):
        self.api_url = normalize_api_url(url)
        self.token = token
        self.last_success: int | None = None
        self.last_error: dict | None = None
        self.version: str | None = None
        self._ids = itertools.count(1)
        # hostid -> (monotonic stamp, key -> item), primed by item_lookup
        self._items_cache: dict[str, tuple[float, dict[str, dict]]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.token)

    def _next_id(self) -> int:
        # Atomic by construction: `self._id += 1` can hand two concurrent requests
        # the same id, and the response-id check then rejects a valid reply.
        return next(self._ids)

    def _opener(self):
        if config.ZABBIX_VERIFY_TLS:
            return urllib.request.build_opener()
        ctx = ssl._create_unverified_context()  # explicit env-only dev switch
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def _record_error(self, exc: ZabbixError) -> None:
        self.last_error = exc.public()

    def _call(self, method: str, params: dict | list, *, authenticated: bool = True) -> dict | list | str:
        if not self.configured:
            raise ZabbixError("NOT_CONFIGURED", "Zabbix API URL/token is not configured")
        req_id = self._next_id()
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json-rpc"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.api_url,
            data=body,
            headers=headers,
        )
        started = time.monotonic()
        try:
            with self._opener().open(req, timeout=config.ZABBIX_TIMEOUT) as res:
                status = res.status
                content_type = res.headers.get("Content-Type", "")
                raw = res.read().decode()
        except urllib.error.HTTPError as exc:
            category = "AUTH_FAILED" if exc.code == 401 else "PERMISSION_DENIED" if exc.code == 403 else "CONNECTION_ERROR"
            err = ZabbixError(category, f"Zabbix API HTTP {exc.code}", code=exc.code)
            self._record_error(err)
            raise err from exc
        except ssl.SSLError as exc:
            err = ZabbixError("TLS_ERROR", "Zabbix API TLS validation failed")
            self._record_error(err)
            raise err from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            err = ZabbixError("CONNECTION_ERROR", "Zabbix API connection failed")
            self._record_error(err)
            raise err from exc
        if status != 200:
            err = ZabbixError("CONNECTION_ERROR", f"Zabbix API HTTP {status}", code=status)
            self._record_error(err)
            raise err
        if "json" not in content_type.lower():
            err = ZabbixError("ZABBIX_API_ERROR", f"Zabbix API returned non-JSON content type: {content_type}")
            self._record_error(err)
            raise err
        try:
            data = json.loads(raw)
        except ValueError as exc:
            err = ZabbixError("ZABBIX_API_ERROR", "Zabbix API returned invalid JSON")
            self._record_error(err)
            raise err from exc
        if data.get("jsonrpc") != "2.0":
            err = ZabbixError("ZABBIX_API_ERROR", "Zabbix API response jsonrpc field is invalid")
            self._record_error(err)
            raise err
        if data.get("id") != req_id:
            err = ZabbixError("ZABBIX_API_ERROR", "Zabbix API response id mismatch")
            self._record_error(err)
            raise err
        has_result = "result" in data
        has_error = "error" in data
        if has_result == has_error:
            err = ZabbixError("ZABBIX_API_ERROR", "Zabbix API response must contain exactly one of result or error")
            self._record_error(err)
            raise err
        if has_error:
            zerr = data["error"] or {}
            category = _map_api_error(zerr)
            err = ZabbixError(category, zerr.get("message") or "Zabbix API error", code=zerr.get("code"), data=zerr.get("data"))
            self._record_error(err)
            raise err
        self.last_success = int(time.time() * 1000)
        self.last_error = None
        metrics = int((time.monotonic() - started) * 1000)
        return data["result"]

    def apiinfo_version(self) -> str:
        # Zabbix 7.x rejects calls to auth-exempt methods that carry an
        # Authorization header, so apiinfo.version goes out unauthenticated.
        version = str(self._call("apiinfo.version", {}, authenticated=False))
        self.version = version
        return version

    def verify_authentication(self) -> None:
        self._call("host.get", {"output": ["hostid"], "limit": 1})

    def health_check(self) -> ApiHealth:
        if not self.configured:
            return ApiHealth("NOT CONFIGURED", None, None)
        started = time.monotonic()
        try:
            version = self.apiinfo_version()
            self.verify_authentication()
            latency = int((time.monotonic() - started) * 1000)
            if not supported_version(version):
                err = ZabbixError("INCOMPATIBLE", f"Unsupported Zabbix API version: {version}")
                self._record_error(err)
                return ApiHealth("INCOMPATIBLE", version, latency, err.public())
            return ApiHealth("AUTHENTICATED", version, latency)
        except ZabbixError as exc:
            state = exc.category if exc.category in {"AUTH_FAILED", "PERMISSION_DENIED", "TLS_ERROR"} else "ERROR"
            return ApiHealth(state, self.version, None, exc.public())

    def host_group_lookup(self, name: str) -> dict | None:
        result = self._call("hostgroup.get", {"filter": {"name": [name]}, "output": ["groupid", "name"]})
        return _exactly_one_or_none(result, "HOST_GROUP_NOT_FOUND", "DUPLICATE_HOST_GROUP", name)

    def template_lookup(self, name: str) -> dict | None:
        result = self._call("template.get", {"filter": {"host": [name]}, "output": ["templateid", "host", "name"]})
        if not result:
            result = self._call("template.get", {"filter": {"name": [name]}, "output": ["templateid", "host", "name"]})
        return _exactly_one_or_none(result, "TEMPLATE_NOT_FOUND", "DUPLICATE_TEMPLATE", name)

    def host_lookup(self, host: str) -> dict | None:
        result = self._call("host.get", {
            "filter": {"host": [host]},
            "output": ["hostid", "host", "name"],
            "selectHostGroups": ["groupid", "name"],
            "selectParentTemplates": ["templateid", "host", "name"],
        })
        return _exactly_one_or_none(result, "HOST_NOT_FOUND", "DUPLICATE_HOST", host)
    def host_create(self, host: str, visible_name: str, templateid: str | None = None) -> dict:
        if not config.ZABBIX_HOST_GROUP:
            raise ZabbixError("NOT_CONFIGURED", "ZABBIX_HOST_GROUP is required for host creation")
        group = self.host_group_lookup(config.ZABBIX_HOST_GROUP)
        if not group:
            raise ZabbixError("HOST_GROUP_NOT_FOUND", f"Zabbix host group not found: {config.ZABBIX_HOST_GROUP}")
        params: dict[str, Any] = {"host": host, "name": visible_name, "groups": [{"groupid": group["groupid"]}], "interfaces": []}
        if templateid:
            params["templates"] = [{"templateid": templateid}]
        return self._call("host.create", params)

    def item_lookup(self, hostid: str, key: str) -> dict | None:
        """One host's items are fetched together and reused briefly.

        Twelve separate lookups cost twelve Zabbix round trips on every chart
        request (part of the measured 36.9 s for a 24 h window). The contract keys
        are stable metadata, so they are primed with a single item.get and kept
        for a short TTL; a duplicate key still raises, a missing key is still None.
        """
        now = time.monotonic()
        cached = self._items_cache.get(hostid)
        if cached is None or (now - cached[0]) > ITEM_METADATA_TTL_SECONDS:
            primed = self.items_for_host(hostid, [spec.zabbix_key for spec in ITEM_SPECS.values()])
            cached = (now, primed)
            self._items_cache[hostid] = cached
        return cached[1].get(key)

    def items_for_host(self, hostid: str, keys: list[str]) -> dict[str, dict]:
        """Every requested contract item of one host in a single item.get.

        Keeps the exactly-one-or-none discipline of item_lookup per key: a
        duplicate key is an error, a missing key is simply absent from the map.
        """
        rows = self._call("item.get", {
            "hostids": hostid,
            "filter": {"key_": list(keys)},
            "output": ["itemid", "key_", "type", "value_type", "units", "name", "status"],
        })
        by_key: dict[str, dict] = {}
        for row in rows or []:
            key = str(row.get("key_"))
            if key in by_key:
                raise ZabbixError("DUPLICATE_ITEM", f"Duplicate Zabbix object for exact lookup: {key}")
            by_key[key] = row
        return by_key

    def item_create_trapper(self, hostid: str, field: str) -> dict:
        spec = ITEM_SPECS[field]
        return self._call("item.create", {
            "hostid": hostid,
            "key_": spec.zabbix_key,
            "name": spec.description,
            "type": spec.item_type,
            "value_type": spec.value_type,
            "units": spec.units,
            "delay": "0",
        })

    def ensure_device_host(self, device: dict, *, allow_create: bool = True) -> str:
        host = technical_host_name(device["device_id"])
        found = self.host_lookup(host)
        templateid = None
        if config.ZABBIX_TEMPLATE:
            template = self.template_lookup(config.ZABBIX_TEMPLATE)
            if not template:
                raise ZabbixError("TEMPLATE_NOT_FOUND", f"Zabbix template not found: {config.ZABBIX_TEMPLATE}")
            templateid = template["templateid"]
        if found:
            if config.ZABBIX_HOST_GROUP:
                group = self.host_group_lookup(config.ZABBIX_HOST_GROUP)
                if not group:
                    raise ZabbixError("HOST_GROUP_NOT_FOUND", f"Zabbix host group not found: {config.ZABBIX_HOST_GROUP}")
                existing_groups = found.get("hostgroups", None)
                if existing_groups is None:
                    existing_groups = found.get("groups", [])
                if not any(
                    str(g.get("groupid")) == str(group["groupid"]) or g.get("name") == config.ZABBIX_HOST_GROUP
                    for g in existing_groups
                ):
                    raise ZabbixError("HOST_GROUP_MISMATCH", f"Zabbix host {host} not in group {config.ZABBIX_HOST_GROUP}")
            if config.ZABBIX_TEMPLATE and templateid:
                existing_templates = found.get("parentTemplates", [])
                if not any(
                    str(t.get("templateid")) == str(templateid)
                    or t.get("host") == config.ZABBIX_TEMPLATE
                    or t.get("name") == config.ZABBIX_TEMPLATE
                    for t in existing_templates
                ):
                    raise ZabbixError("TEMPLATE_MISMATCH", f"Zabbix host {host} missing template {config.ZABBIX_TEMPLATE}")
            return str(found["hostid"])
        if not allow_create:
            raise ZabbixError("HOST_NOT_FOUND", f"Zabbix host not found: {host}")
        created = self.host_create(host, device.get("name") or host, templateid)
        hostids = created.get("hostids") or []
        if not hostids:
            raise ZabbixError("ZABBIX_API_ERROR", "Zabbix host.create returned no hostid")
        return str(hostids[0])
    def item_update_units(self, itemid: str, units: str) -> dict:
        return self._call("item.update", {"itemid": itemid, "units": units})

    def reconcile_items(self, hostid: str, fields: list[str], *, allow_create: bool | None = None) -> None:
        can_create = config.ZABBIX_AUTO_CREATE_ITEMS if allow_create is None else allow_create
        # Reconciliation repairs drift, so it reads the live item set: the cached
        # copy exists to spare chart requests, never to hide a disabled or
        # re-created item from the path whose job is to notice exactly that.
        self._items_cache.pop(hostid, None)
        live = self.items_for_host(hostid, [ITEM_SPECS[f].zabbix_key for f in fields])
        for field in fields:
            spec = ITEM_SPECS[field]
            item = live.get(spec.zabbix_key)
            if not item:
                if not can_create:
                    raise ZabbixError("ITEM_NOT_CONFIGURED", f"Zabbix item not found: {spec.zabbix_key}")
                self.item_create_trapper(hostid, field)
                self._items_cache.pop(hostid, None)  # a create changes the item set
                continue
            # Type/value_type/status decide whether a value can be delivered and
            # stored at all: a mismatch there is unrecoverable by this writer and
            # stays a hard error. Display metadata (units) does not affect the
            # stored value, so an item whose units drifted from the contract is
            # corrected in place instead of dead-lettering every sample.
            if (
                int(item.get("type", -1)) != spec.item_type
                or int(item.get("value_type", -1)) != spec.value_type
                or str(item.get("status", "0")) != "0"
            ):
                raise ZabbixError("ITEM_TYPE_MISMATCH", f"Zabbix item mismatch: {spec.zabbix_key}")
            if str(item.get("units", "")) != spec.units:
                self.item_update_units(str(item.get("itemid")), spec.units)

    # Health series shown by the device health charts. Keys are the Zabbix items
    # the router health payload feeds; a missing item means that metric was
    # never integrated, which is a different fact from "no data in range".
    HEALTH_SERIES = (
        ("rut.sys.load1", "cpu"),
        ("rut.sys.ram.pct", "ram"),
        ("rut.modem.temperature.c", "temperature"),
        ("rut.modem.signal.dbm", "rssi"),
    )

    def trend_rows(self, itemid: str, *, time_from: int | None = None, time_till: int | None = None, limit: int = 1000) -> list[dict]:
        """Hourly pre-aggregated values for one item. Zabbix keeps trends for a
        year even when raw history is shorter, so this is what fills the parts of
        a long window that raw history cannot cover."""
        params: dict[str, object] = {"itemids": [itemid], "output": ["clock", "num", "value_min", "value_avg", "value_max"], "limit": limit}
        if time_from is not None:
            params["time_from"] = time_from
        if time_till is not None:
            params["time_till"] = time_till
        try:
            return list(self._call("trend.get", params))
        except ZabbixError:
            return []

    def health_history(self, device_id: str, *, time_from: int | None = None, time_till: int | None = None, limit: int = 288) -> dict:
        host = technical_host_name(device_id)
        found = self.host_lookup(host)
        if not found:
            raise ZabbixError("HOST_NOT_FOUND", f"Zabbix host not found: {host}")
        hostid = str(found["hostid"])
        series: dict[str, list[dict]] = {"cpu": [], "ram": [], "temperature": [], "rssi": []}
        integrated = 0
        for key, name in self.HEALTH_SERIES:
            item = self.item_lookup(hostid, key)
            if not item:
                continue
            integrated += 1
            try:
                vtype = int(item.get("value_type", 0))
            except (TypeError, ValueError):
                vtype = 0
            rows = self.recent_history(hostid, [str(item.get("itemid"))], history=vtype, time_from=time_from, time_till=time_till, limit=limit)
            points = []
            for row in rows:
                try:
                    ts = int(row.get("clock", 0)) * 1000 + int(int(row.get("ns", 0)) // 1_000_000)
                    points.append({"ts": ts, "value": float(row.get("value"))})
                except (TypeError, ValueError):
                    continue
            points.sort(key=lambda p: p["ts"])
            series[name] = points
        if integrated == 0:
            state = METRIC_NOT_INTEGRATED
        elif any(series.values()):
            state = "available"
        else:
            state = "no_data"
        return {"state": state, "deviceId": device_id, **series}

    def recent_history(self, hostid: str, itemids: list[str], *, history: int = 0, time_from: int | None = None, time_till: int | None = None, limit: int = 288) -> list[dict]:
        params: dict[str, object] = {"hostids": hostid, "itemids": itemids, "history": history, "sortfield": "clock", "sortorder": "DESC", "limit": limit}
        if time_from is not None:
            params["time_from"] = time_from
        if time_till is not None:
            params["time_till"] = time_till
        result = self._call("history.get", params)
        return list(result)

    def history_window(self, hostid: str, itemid: str, *, history: int, time_from: int, time_till: int, page_limit: int = 4000, max_pages: int = 10) -> tuple[list[dict], bool]:
        """Every sample of one item in [time_from, time_till], paged back from the newest.

        A single history.get cannot express "all samples in this window": the API
        caps the row count and orders rows ascending, so a window denser than the
        assumed cadence quietly loses its oldest part (measured 2026-10-21: the
        RUT906 reports ~215 samples/hour, not the 180 a 20 s contract implies, so
        a 24 h window holds 5071 samples while a 4380-row request returned only
        the newest 4380 and the chart lost its first 3.2 hours).

        Returns (rows ascending, complete). `complete` is False only when the page
        budget ran out before reaching time_from, which the caller must report
        instead of passing a partial window off as the requested one.
        """
        rows: list[dict] = []
        till = int(time_till)
        pages = 0
        complete = False
        while pages < max_pages:
            chunk = list(self._call("history.get", {
                "hostids": hostid, "itemids": [itemid], "history": history, "output": "extend",
                "time_from": int(time_from), "time_till": till, "limit": page_limit,
                "sortfield": "clock", "sortorder": "DESC",
            }))
            pages += 1
            if not chunk:
                complete = True
                break
            rows.extend(chunk)
            oldest = min(int(r.get("clock", 0)) for r in chunk)
            if oldest <= int(time_from) or len(chunk) < page_limit:
                complete = True
                break
            till = oldest - 1
        rows.sort(key=lambda r: int(r.get("clock", 0)))
        return rows, complete

    def history_for_items(self, hostid: str, items: list[dict], *, time_from: int | None = None, time_till: int | None = None, limit: int = 288) -> dict[str, list[dict]]:
        """The newest `limit` samples per item: enough to refresh a recent cache.

        Fetched concurrently for the same reason as history_window_for_items: one
        round trip per item, run one after another, is the whole cost of a build.
        """
        if not items:
            return {}

        def fetch(item: dict) -> tuple[str, list[dict]]:
            try:
                vtype = int(item.get("value_type", 0))
            except (TypeError, ValueError):
                vtype = 0
            return (
                str(item.get("key_")),
                self.recent_history(hostid, [str(item.get("itemid"))], history=vtype,
                                    time_from=time_from, time_till=time_till, limit=limit),
            )

        workers = max(1, min(MAX_WINDOW_FETCH_WORKERS, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="zbxrecent") as pool:
            return dict(pool.map(fetch, items))

    def history_window_for_items(self, hostid: str, items: list[dict], *, time_from: int, time_till: int) -> tuple[dict[str, list[dict]], bool]:
        """Every sample per item in [time_from, time_till], plus whether it was complete.

        Charts need the whole window, not the newest N samples: a row cap cannot
        express "all samples between from and to", and a device that reports
        faster than the assumed cadence then loses its oldest hours silently.

        Each item is still paged exactly as before; only the scheduling changed.
        Twelve items one after another cost ~37 s of a page load (measured
        2026-10-21), because every item is one or more Zabbix round trips.
        """
        if not items:
            return {}, True

        def fetch(item: dict) -> tuple[str, list[dict], bool]:
            try:
                vtype = int(item.get("value_type", 0))
            except (TypeError, ValueError):
                vtype = 0
            rows, item_complete = self.history_window(
                hostid, str(item.get("itemid")), history=vtype,
                time_from=int(time_from), time_till=int(time_till),
                page_limit=MAX_RAW_PAGE_ROWS, max_pages=RAW_WINDOW_MAX_PAGES,
            )
            return str(item.get("key_")), rows, item_complete

        out: dict[str, list[dict]] = {}
        complete = True
        workers = max(1, min(MAX_WINDOW_FETCH_WORKERS, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="zbxhist") as pool:
            # map() keeps input order and surfaces the first failure, so a Zabbix
            # error reaches the caller the same way the sequential loop did.
            for key, rows, item_complete in pool.map(fetch, items):
                out[key] = rows
                complete = complete and item_complete
        return out, complete

    def device_history(self, device_id: str, *, time_from: int | None = None, time_till: int | None = None) -> dict:
        from .telemetry import MPPT_MAP, PZEM_MAP, SOLAR_MAP
        host = technical_host_name(device_id)
        found = self.host_lookup(host)
        if not found:
            raise ZabbixError("HOST_NOT_FOUND", f"Zabbix host not found: {host}")
        hostid = str(found["hostid"])
        # item_lookup primes every contract key of this host with one item.get and
        # caches it briefly, so this loop costs one round trip, not twelve.
        items: list[dict] = []
        for field, spec in ITEM_SPECS.items():
            item = self.item_lookup(hostid, spec.zabbix_key)
            if item:
                items.append(item)
        # The window is fetched in full: letting a row cap decide how far back the
        # data reaches is what silently dropped the oldest hours of every window
        # (measured 2026-10-21: a 24 h window holds 5071 samples and the chart was
        # showing only the newest 4380, losing its first 3.2 hours).
        by_key, complete_window = self.history_window_for_items(
            hostid, items,
            time_from=int(time_from) if time_from is not None else 0,
            time_till=int(time_till) if time_till is not None else int(time.time()),
        ) if items else ({}, True)
        truncated = not complete_window
        key_to_raw = {spec.zabbix_key: raw for raw, spec in ITEM_SPECS.items()}
        mppt: dict[str, list[dict]] = {"pvVoltage": [], "pvCurrent": [], "outputVoltage": [], "outputCurrent": []}
        pzem: dict[str, list[dict]] = {"phaseAVoltage": [], "phaseBVoltage": [], "phaseCVoltage": [], "phaseACurrent": [], "phaseBCurrent": [], "phaseCCurrent": [], "energy": []}
        solar: dict[str, list[dict]] = {"radiation": []}
        for zkey, rows in by_key.items():
            raw = key_to_raw.get(zkey)
            if raw is None:
                continue
            if raw in MPPT_MAP:
                target = mppt.get(MPPT_MAP[raw])
            elif raw in PZEM_MAP:
                target = pzem.get(PZEM_MAP[raw])
            elif raw in SOLAR_MAP:
                target = solar.get(SOLAR_MAP[raw])
            else:
                target = None
            if target is None:
                continue
            for r in rows:
                try:
                    ts = int(r.get("clock", 0)) * 1000 + int(int(r.get("ns", 0)) // 1_000_000)
                    value = float(r.get("value"))
                except (TypeError, ValueError):
                    continue
                target.append({"ts": ts, "value": value})
        for series in list(mppt.values()) + list(pzem.values()) + list(solar.values()):
            # Chronological output is part of the contract: the UI charts these
            # directly and must never depend on Zabbix's DESC retrieval order.
            series.sort(key=lambda p: p["ts"])
        # Per-metric state. An item that does not exist in Zabbix is reported as
        # not_integrated, which is a different fact from an item that exists and
        # simply sent nothing in the requested window.
        found_keys = {str(i.get("key_")) for i in items}
        metric_state: dict[str, str] = {}
        for group, mapping, series_map in (("mppt", MPPT_MAP, mppt), ("pzem", PZEM_MAP, pzem), ("solar", SOLAR_MAP, solar)):
            for raw_field, series_name in mapping.items():
                # Compare Zabbix keys, not MQTT field names: item_lookup is keyed
                # by key_, so comparing raw_field would classify every metric as
                # not_integrated regardless of what the host actually has.
                if ITEM_SPECS[raw_field].zabbix_key not in found_keys:
                    metric_state[f"{group}.{series_name}"] = METRIC_NOT_INTEGRATED
                elif series_map.get(series_name):
                    metric_state[f"{group}.{series_name}"] = METRIC_DATA
                else:
                    metric_state[f"{group}.{series_name}"] = METRIC_NO_DATA_IN_RANGE
        return {
            "deviceId": device_id,
            "mppt": mppt,
            "pzem": pzem,
            "solar": solar,
            "metricState": metric_state,
            # Echo the resolved window so the caller can state the range it was
            # actually served instead of guessing it from the data extent. `limit`
            # is the page budget the fetch is allowed to spend, not a row count
            # that can cut a window short.
            "window": {
                "fromMs": time_from * 1000 if time_from is not None else None,
                "toMs": time_till * 1000 if time_till is not None else None,
                "limit": MAX_RAW_PAGE_ROWS * RAW_WINDOW_MAX_PAGES,
                "truncated": truncated,
                "maxPoints": MAX_RAW_POINTS,
            },
        }

    def problems(self, hostids: list[str] | None = None, *, limit: int = 100) -> list[dict]:
        # problem.get in Zabbix 7.4 accepts no host-select parameter (verified against
        # 7.4.14: only selectAcknowledges/selectTags/selectSuppressionData are valid), and
        # rejects sortfield other than "eventid". The owning host is therefore resolved
        # from the trigger that raised the problem, in one extra call per batch.
        params: dict[str, object] = {"output": ["eventid", "clock", "ns", "name", "severity", "r_eventid", "r_clock", "objectid", "source", "object"], "sortfield": ["eventid"], "sortorder": "DESC", "limit": limit, "recent": True}
        if hostids is not None:
            params["hostids"] = hostids
        rows = list(self._call("problem.get", params))
        host_by_trigger: dict[str, str] = {}
        triggerids = sorted({str(r.get("objectid")) for r in rows if str(r.get("object")) == "0"})
        if triggerids:
            for t in self._call("trigger.get", {"triggerids": triggerids, "output": ["triggerid"], "selectHosts": ["hostid", "host"]}):
                hosts = t.get("hosts") or []
                if hosts:
                    host_by_trigger[str(t.get("triggerid"))] = str(hosts[0].get("host") or "")
        for r in rows:
            if str(r.get("object")) == "0" and host_by_trigger.get(str(r.get("objectid"))):
                r["hosts"] = [{"host": host_by_trigger[str(r.get("objectid"))]}]
        return rows

    def scoped_alarms(self, device_id: str | None = None, registered_hosts: list[str] | None = None, *, limit: int = 100, device_lookup=None) -> list[dict]:
        hostids: list[str] | None = None
        if device_id is not None:
            host = technical_host_name(device_id)
            found = self.host_lookup(host)
            if not found:
                return []
            hostids = [str(found["hostid"])]
        elif registered_hosts is not None:
            if not registered_hosts:
                return []
            found_ids: list[str] = []
            for r_host in registered_hosts:
                found = self.host_lookup(technical_host_name(r_host))
                if found:
                    found_ids.append(str(found["hostid"]))
            if not found_ids:
                return []
            hostids = found_ids
        raw = self.problems(hostids, limit=limit)
        return [normalize_problem(p, device_lookup=device_lookup) for p in raw]

    def events(self, hostids: list[str] | None = None, *, limit: int = 100) -> list[dict]:
        params: dict[str, object] = {
            "output": ["eventid", "clock", "ns", "name", "severity", "value", "acknowledged", "r_eventid"],
            "selectHosts": ["hostid", "host", "name"],
            "sortfield": ["clock"],
            "sortorder": "DESC",
            "limit": limit,
        }
        if hostids is not None:
            params["hostids"] = hostids
        result = self._call("event.get", params)
        return list(result)

    def scoped_events(self, device_id: str | None = None, registered_hosts: list[str] | None = None, *, limit: int = 100, device_lookup=None) -> list[dict]:
        hostids: list[str] | None = None
        if device_id is not None:
            host = technical_host_name(device_id)
            found = self.host_lookup(host)
            if not found:
                return []
            hostids = [str(found["hostid"])]
        elif registered_hosts is not None:
            if not registered_hosts:
                return []
            found_ids: list[str] = []
            for r_host in registered_hosts:
                found = self.host_lookup(technical_host_name(r_host))
                if found:
                    found_ids.append(str(found["hostid"]))
            if not found_ids:
                return []
            hostids = found_ids
        raw = self.events(hostids, limit=limit)
        return [normalize_event(e, device_lookup=device_lookup) for e in raw]

    def sender_submit(self, values: list[dict]) -> SenderResult:
        if not config.ZABBIX_SENDER_HOST:
            raise ZabbixError("NOT_CONFIGURED", "ZABBIX_SENDER_HOST is not configured")
        payload = json.dumps({"request": "sender data", "data": values}, separators=(",", ":")).encode()
        packet = b"ZBXD\x01" + struct.pack("<Q", len(payload)) + payload
        started = time.monotonic()
        try:
            raw = self._sender_round_trip(packet)
        except ssl.SSLError as exc:
            err = ZabbixError("TLS_ERROR", "Zabbix sender TLS validation failed")
            self._record_error(err)
            raise err from exc
        except (OSError, TimeoutError) as exc:
            err = ZabbixError("CONNECTION_ERROR", "Zabbix sender request failed")
            self._record_error(err)
            raise err from exc
        try:
            data = json.loads(raw.decode())
            info = str(data.get("info", ""))
            response = str(data.get("response", ""))
        except ValueError as exc:
            raise ZabbixError("ZABBIX_SENDER_ERROR", "Invalid Zabbix sender JSON response") from exc
        processed = _info_int(info, "processed")
        failed = _info_int(info, "failed")
        total = _info_int(info, "total") or len(values)
        result = SenderResult(processed=processed, failed=failed, total=total, response=response)
        if not result.ok:
            err = ZabbixError("ZABBIX_SENDER_ERROR", f"Zabbix sender failed: {info}")
            self._record_error(err)
            raise err
        self.last_success = int(time.time() * 1000)
        self.last_error = None
        _ = int((time.monotonic() - started) * 1000)
        return result

    def _sender_round_trip(self, packet: bytes) -> bytes:
        sock = socket.create_connection((config.ZABBIX_SENDER_HOST, config.ZABBIX_SENDER_PORT), timeout=config.ZABBIX_TIMEOUT)
        with sock:
            if config.ZABBIX_SENDER_TLS:
                ctx = ssl.create_default_context(cafile=config.ZABBIX_CA_FILE or None)
                if not config.ZABBIX_SENDER_VERIFY_TLS:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                with ctx.wrap_socket(sock, server_hostname=config.ZABBIX_SENDER_HOST if config.ZABBIX_SENDER_VERIFY_TLS else None) as tls_sock:
                    return _send_packet(tls_sock, packet)
            return _send_packet(sock, packet)


def normalize_api_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    path = parsed.path.rstrip("/")
    if path.endswith("/api_jsonrpc.php"):
        final_path = path
    elif path.endswith("api_jsonrpc.php"):
        final_path = path
    else:
        final_path = f"{path}/api_jsonrpc.php" if path else "/api_jsonrpc.php"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, final_path, "", ""))


def zabbix_values(vt: ValidatedTelemetry) -> list[dict]:
    clock = vt.source_ts // 1000
    ns = (vt.source_ts % 1000) * 1_000_000
    out = []
    for raw, value in vt.raw_values.items():
        if value is None:
            continue
        key = ZABBIX_KEYS.get(raw)
        # A measured field with no contracted item is not trapped: raw_values
        # also carries values kept for the fingerprint only (e.g. core count).
        if key is None:
            continue
        out.append({"host": technical_host_name(vt.device_id), "key": key, "value": value, "clock": clock, "ns": ns})
    return out


def _recv_exact(sock, length: int) -> bytes:
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ZabbixError("ZABBIX_SENDER_ERROR", f"Connection closed prematurely, received {len(buf)} of {length} bytes")
        buf.extend(chunk)
    return bytes(buf)


def _send_packet(sock, packet: bytes) -> bytes:
    sock.sendall(packet)
    header = _recv_exact(sock, 13)
    if not header.startswith(b"ZBXD\x01"):
        raise ZabbixError("ZABBIX_SENDER_ERROR", "Invalid Zabbix sender response header")
    size = struct.unpack("<Q", header[5:13])[0]
    return _recv_exact(sock, size)

def _info_int(info: str, key: str) -> int:
    marker = f"{key}: "
    if marker not in info:
        return 0
    try:
        return int(info.split(marker, 1)[1].split(";", 1)[0].strip())
    except ValueError:
        return 0


def _exactly_one_or_none(result: Any, missing: str, duplicate: str, name: str) -> dict | None:
    rows = list(result or [])
    if not rows:
        return None
    if len(rows) > 1:
        raise ZabbixError(duplicate, f"Duplicate Zabbix object for exact lookup: {name}")
    return rows[0]


_SEVERITY_MAP = {"0": "information", "1": "information", "2": "warning", "3": "average", "4": "high", "5": "disaster"}
# the canonical event contract. event.get returns both trigger
# states, so the wire can carry more than the old alarm/system pair implied.
CANONICAL_EVENT_TYPES = ("alarm_created", "alarm_resolved", "status_change", "config_change", "reboot", "unknown")

# Legacy wire names, translated explicitly rather than passed through.
# "alarm" was always value=="1". "system" was the everything-else bucket and
# cannot be told apart from a genuine recovery, so it degrades to "unknown"
# rather than asserting a fact the old payload never carried.
LEGACY_EVENT_TYPE_ALIASES = {"alarm": "alarm_created", "system": "unknown"}


def canonical_event_type(raw: object) -> str:
    """Map any event-type token onto the canonical set.

    Total by construction: an unrecognised, empty or malformed token becomes
    "unknown" instead of escaping the contract and rendering as a blank cell
    downstream.
    """
    token = str(raw).strip()
    if token in CANONICAL_EVENT_TYPES:
        return token
    if token in LEGACY_EVENT_TYPE_ALIASES:
        return LEGACY_EVENT_TYPE_ALIASES[token]
    # Zabbix trigger value: "1" = problem raised, "0" = recovered.
    if token == "1":
        return "alarm_created"
    if token == "0":
        return "alarm_resolved"
    return "unknown"




def normalize_problem(problem: dict, *, device_lookup=None) -> dict:
    hosts = problem.get("hosts") or []
    host = hosts[0].get("host") if hosts else None
    device_id = str(host or "")
    site_id: str = ""
    site_name = None
    if device_lookup is not None and device_id:
        try:
            row = device_lookup(device_id)
        except Exception:
            row = None
        if row is not None:
            try:
                site_id = str(row["site_id"] or "")
                site_name = row["site_name"]
            except (KeyError, TypeError, IndexError):
                pass
    try:
        start_ms = int(problem.get("clock", 0)) * 1000 + int(int(problem.get("ns", 0)) // 1_000_000)
    except (TypeError, ValueError):
        start_ms = 0
    r_eventid = str(problem.get("r_eventid") or "0")
    status = "resolved" if r_eventid != "0" else "active"
    end_ms = None
    if status == "resolved":
        # D1: r_clock 0/missing means the recovery timestamp is unknown,
        # emitting endTime 0 would stamp epoch as a real recovery time.
        try:
            r_clock = int(problem.get("r_clock", 0) or 0)
        except (TypeError, ValueError):
            r_clock = 0
        if r_clock > 0:
            end_ms = r_clock * 1000
    alarm: dict[str, object] = {
        "id": str(problem.get("eventid", "")),
        "deviceId": device_id,
        "siteId": site_id,
        "siteName": site_name,
        "problem": str(problem.get("name", "")),
        "severity": _SEVERITY_MAP.get(str(problem.get("severity", "0")), "information"),
        "status": status,
        "startTime": start_ms,
    }
    if end_ms is not None:
        alarm["endTime"] = end_ms
    return alarm

def normalize_event(event: dict, *, device_lookup=None) -> dict:
    hosts = event.get("hosts") or []
    host = hosts[0].get("host") if hosts else None
    device_id = str(host or "")
    site_id: str = ""
    site_name = None
    if device_lookup is not None and device_id:
        try:
            row = device_lookup(device_id)
        except Exception:
            row = None
        if row is not None:
            try:
                site_id = str(row["site_id"] or "")
                site_name = row["site_name"]
            except (KeyError, TypeError, IndexError):
                pass
    try:
        ts_ms = int(event.get("clock", 0)) * 1000 + int(int(event.get("ns", 0)) // 1_000_000)
    except (TypeError, ValueError):
        ts_ms = 0
    return {
        "id": str(event.get("eventid", "")),
        "deviceId": device_id,
        "siteId": site_id,
        "siteName": site_name,
        "type": canonical_event_type(event.get("value", "")),
        "severity": _SEVERITY_MAP.get(str(event.get("severity", "0")), "information"),
        # A partial event must still render as a labelled row, so an absent
        # name becomes an explicit placeholder rather than an empty cell.
        "message": str(event.get("name", "")) or "Unnamed event",
        "timestamp": ts_ms,
    }


def _map_api_error(err: dict) -> str:
    text = f"{err.get('message', '')} {err.get('data', '')}".lower()
    code = err.get("code")
    if code in (401, -32602) and "auth" in text:
        return "AUTH_FAILED"
    if code == 403 or "permission" in text or "denied" in text:
        return "PERMISSION_DENIED"
    if "host" in text and "not" in text:
        return "HOST_NOT_FOUND"
    if "group" in text and "not" in text:
        return "HOST_GROUP_NOT_FOUND"
    if "template" in text and "not" in text:
        return "TEMPLATE_NOT_FOUND"
    if "item" in text and "not" in text:
        return "ITEM_NOT_CONFIGURED"
    return "ZABBIX_API_ERROR"


zabbix = ZabbixClient(config.ZABBIX_URL, config.ZABBIX_TOKEN)
