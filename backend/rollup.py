"""TELNUSA NMS R3.1: Rollup Management & Multi-Resolution Query Service.

Orchestrates raw sample fetching from Zabbix, on-demand hourly aggregation into
SQLite, and the display series the history API returns.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .analytics import (
    HOURLY_EXPECTED_SAMPLES,
    QUALITY_MISSING_DATA,
    QUALITY_RESET_DETECTED,
    QUALITY_VALID,
    RAW_TIER_MAX_MS,
    SAMPLE_INTERVAL_MS,
    SERIES_TARGET_POINTS,
    aggregate_energy_counter,
    bucket_counter_points,
    bucket_gauge_points,
    bucket_start,
    compute_hourly_rollup_from_raw,
    compute_window_coverage,
    hour_bucket_start,
    jakarta_day_bucket_start,
    resolve_series_interval_ms,
)
from .config import config
from .store import Store
from .telemetry import MPPT_MAP, PZEM_MAP, SOLAR_MAP
from .zabbix_client import ITEM_SPECS, METRIC_DATA, METRIC_ERROR, METRIC_NOT_INTEGRATED, METRIC_NO_DATA_IN_RANGE, ZabbixClient, ZabbixError, technical_host_name
logger = logging.getLogger("telnusa.rollup")

def format_raw_telemetry_samples(by_key: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Convert Zabbix raw history by-item mapping into timestamp-aligned sample dicts."""
    key_to_field = {spec.zabbix_key: raw for raw, spec in ITEM_SPECS.items()}
    samples_by_ts: dict[int, dict[str, Any]] = {}

    for zkey, rows in by_key.items():
        raw_field = key_to_field.get(zkey)
        if not raw_field:
            continue
        if raw_field in MPPT_MAP:
            mapped_name = MPPT_MAP[raw_field]
        elif raw_field in PZEM_MAP:
            mapped_name = PZEM_MAP[raw_field]
        elif raw_field in SOLAR_MAP:
            mapped_name = SOLAR_MAP[raw_field]
        else:
            continue

        for r in rows:
            try:
                ts = int(r.get("clock", 0)) * 1000 + int(int(r.get("ns", 0)) // 1_000_000)
                val = float(r.get("value"))
            except (TypeError, ValueError):
                continue

            if ts not in samples_by_ts:
                samples_by_ts[ts] = {"ts": ts}
            samples_by_ts[ts][mapped_name] = val

    sample_list = list(samples_by_ts.values())
    sample_list.sort(key=lambda s: s["ts"])
    return sample_list


# The raw sync is the most expensive part of a history build (one history query
# per sensor item). A short TTL keeps a page load plus a range switch from paying
# for it twice; it only gates re-computation, never the freshness of what is
# returned, because every response is rebuilt from Zabbix data either way.
_SYNC_TTL_SECONDS = 45.0
_last_sync: dict[str, tuple[float, int]] = {}


def sync_device_rollups_from_zabbix(
    store: Store,
    zabbix: ZabbixClient,
    device_id: str,
    time_from_ms: int,
    time_till_ms: int,
) -> None:
    """Fetch raw history across the requested range from Zabbix and compute/cache rollups."""
    if not getattr(zabbix, "configured", False) or not hasattr(zabbix, "host_lookup") or not hasattr(zabbix, "history_for_items"):
        return
    done = _last_sync.get(device_id)
    if done is not None and (time.monotonic() - done[0]) < _SYNC_TTL_SECONDS and done[1] <= time_from_ms:
        return
    try:
        host = technical_host_name(device_id)
        found = zabbix.host_lookup(host)
        if not found:
            return
        hostid = str(found["hostid"])
        items: list[dict] = []
        for raw, spec in ITEM_SPECS.items():
            # Rollups exist for the sensor contract only; pulling the router
            # health items here would cost 7 more history queries per build for
            # data nothing aggregates.
            if raw not in MPPT_MAP and raw not in PZEM_MAP and raw not in SOLAR_MAP:
                continue
            it = zabbix.item_lookup(hostid, spec.zabbix_key)
            if it:
                items.append(it)
        if not items:
            return

        # Fetch raw points from Zabbix in window
        time_from_sec = int(time_from_ms // 1000)
        time_till_sec = int((time_till_ms + 999) // 1000)
        # history_for_items returns {zabbix_key: rows}, not a (rows, complete)
        # pair: unpacking it raised ValueError on an empty window, which aborted
        # the whole rollup sync and left long windows without rollups.
        by_key = zabbix.history_for_items(hostid, items, time_from=time_from_sec, time_till=time_till_sec, limit=config.ZABBIX_ROLLUP_RAW_LIMIT)
        samples = format_raw_telemetry_samples(by_key)
        if not samples:
            return

        # Group samples by hour bucket
        hourly_buckets: dict[int, list[dict]] = {}
        for s in samples:
            hb = hour_bucket_start(s["ts"])
            hourly_buckets.setdefault(hb, []).append(s)

        # Compute and save hourly rollups
        for hb, bucket_samples in hourly_buckets.items():
            rollup = compute_hourly_rollup_from_raw(device_id, hb, bucket_samples)
            store.save_hourly_rollup(rollup)

        _last_sync[device_id] = (time.monotonic(), time_from_ms)
    except Exception as exc:
        # The caller decides whether cached data is sufficient, but a failed sync
        # must be visible: otherwise long windows quietly lose their rollups.
        logger.warning("rollup sync failed for %s: %r", device_id, exc)
        return


TREND_DERIVED_QUALITY = "TREND_DERIVED"

def _hour_buckets(time_from_ms: int, time_till_ms: int) -> set[int]:
    start = hour_bucket_start(time_from_ms)
    return {start + i * 3_600_000 for i in range(int((time_till_ms - start) // 3_600_000) + 1)}

# Raw-tier response cache. A 24 h window is 4320 samples per item and 12 items,
# so re-fetching it on every range switch is the whole cost of the dense raw
# view. The key is quantized: the window slides with every request, so an exact
# key would never match and the cache would never hit.
_RAW_TIER_TTL_SECONDS = 30.0
_RAW_TIER_KEY_QUANTUM_SECONDS = 30
_raw_tier_cache: dict[tuple[str, int, int], tuple[float, dict]] = {}
# Single flight: concurrent requests for the same quantised window share one
# Zabbix fetch. A dense 24 h window is a large dict per request, and the unit was
# measured at a 3.3 GB peak before this; two operators opening the same chart used
# to pay for it twice, in time and in memory.
_raw_tier_locks: dict[tuple[str, int, int], threading.Lock] = {}
_raw_tier_locks_guard = threading.Lock()

def _raw_tier_history(zabbix: ZabbixClient, device_id: str, time_from_sec: int, time_till_sec: int) -> dict:
    """Raw Zabbix history for the window, reused across requests inside the TTL."""
    q = _RAW_TIER_KEY_QUANTUM_SECONDS
    key = (device_id, time_from_sec // q, (time_till_sec - time_from_sec) // q)
    hit = _raw_tier_cache.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < _RAW_TIER_TTL_SECONDS:
        return hit[1]
    with _raw_tier_locks_guard:
        lock = _raw_tier_locks.get(key)
        if lock is None:
            if len(_raw_tier_locks) > 64:
                for stale in [k for k, held in _raw_tier_locks.items() if not held.locked()][:32]:
                    _raw_tier_locks.pop(stale, None)
            lock = _raw_tier_locks.setdefault(key, threading.Lock())
    with lock:
        hit = _raw_tier_cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < _RAW_TIER_TTL_SECONDS:
            return hit[1]
        payload = zabbix.device_history(device_id, time_from=time_from_sec, time_till=time_till_sec)
        _raw_tier_cache.clear()
        _raw_tier_cache[key] = (time.monotonic(), payload)
        return payload

def _newest_covered_ms(*series_maps: dict) -> int | None:
    """Newest bucket timestamp present across a set of series maps, or None."""
    newest: int | None = None
    for series_map in series_maps:
        for points in series_map.values():
            for point in points:
                ts = point.get("ts")
                if ts is not None and (newest is None or ts > newest):
                    newest = int(ts)
    return newest

def _bucket_counter_rows(rows: list[dict], interval_ms: int) -> list[dict]:
    """Bucket hourly counter rows: bucket end reading, summed consumption, worst quality."""
    buckets: dict[int, dict] = {}
    for row in rows:
        try:
            ts = int(row["ts"])
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        start = bucket_start(ts, interval_ms)
        slot = buckets.setdefault(start, {"ts": start, "value": value, "at": ts, "delta": 0.0, "quality": QUALITY_VALID, "source": None})
        if ts >= slot["at"]:
            slot["at"] = ts
            slot["value"] = value
        slot["delta"] = round(slot["delta"] + float(row.get("delta") or 0.0), 4)
        quality = row.get("quality")
        if quality == QUALITY_RESET_DETECTED:
            slot["quality"] = QUALITY_RESET_DETECTED
        elif quality and quality != QUALITY_VALID and slot["quality"] == QUALITY_VALID:
            slot["quality"] = quality
        if row.get("source"):
            slot["source"] = row["source"]
    out: list[dict] = []
    for start in sorted(buckets):
        slot = buckets[start]
        slot.pop("at", None)
        if slot.get("source") is None:
            slot.pop("source", None)
        out.append(slot)
    return out

def _fill_missing_buckets(zabbix, device_id: str, time_from_ms: int, time_till_ms: int, *, bucket_of, buckets_in_window, covered: set[int], series_maps) -> None:
    """Complete the series with Zabbix hourly trends for buckets the local
    raw-derived rollups do not cover.

    Raw history cannot be requested far enough back to fill a 7d/30d window in
    one call, so those buckets used to be silently absent and the chart showed a
    partially empty window. Trends are real pre-aggregated measurements from the
    same Zabbix items, so the chart is complete without inventing anything.
    """
    missing = buckets_in_window() - covered
    if not missing:
        return
    fill = _trend_buckets(zabbix, device_id, missing, bucket_of, time_from_ms, time_till_ms)
    for group, series_map in series_maps:
        for name, points in (fill.get(group) or {}).items():
            series = series_map.get(name)
            if series is None:
                continue
            series.extend(points.values())
            series.sort(key=lambda p: p.get("ts", 0))
    energy_fill = _energy_trend_points(zabbix, device_id, missing, bucket_of, time_from_ms, time_till_ms)
    if energy_fill and series_maps:
        for group, series_map in series_maps:
            if group != "pzem":
                continue
            energy_series = series_map.get("energy")
            if energy_series is None:
                continue
            for bucket, vals in sorted(energy_fill.items()):
                delta = round(max(0.0, vals["end"] - vals["start"]), 4)
                energy_series.append({"ts": bucket, "value": vals["end"], "delta": delta,
                                      "quality": TREND_DERIVED_QUALITY, "source": "zabbix_trends"})
            energy_series.sort(key=lambda p: p.get("ts", 0))




def _trend_buckets(zabbix, device_id: str, buckets_needed: set[int], bucket_of, time_from_ms: int, time_till_ms: int) -> dict:
    """Hourly Zabbix trend aggregates for the sensor items, bucketed for the caller.

    Raw history is capped per request, so a long window (7d/30d) can never be
    covered from raw alone; Zabbix keeps hourly trends for a year, which is what
    this fills the uncovered buckets with. Buckets the local raw-derived rollups
    already cover are left untouched.
    """
    result: dict[str, dict[str, dict]] = {"mppt": {}, "pzem": {}, "solar": {}}
    if not buckets_needed or not getattr(zabbix, "configured", False) or not hasattr(zabbix, "trend_rows"):
        return result
    try:
        host = technical_host_name(device_id)
        found = zabbix.host_lookup(host)
    except (ZabbixError, AttributeError):
        return result
    if not found:
        return result
    hostid = str(found["hostid"])
    t_from = int(time_from_ms // 1000)
    t_till = int((time_till_ms + 999) // 1000)
    for raw, spec in ITEM_SPECS.items():
        if raw in MPPT_MAP:
            group, series_name = "mppt", MPPT_MAP[raw]
        elif raw in PZEM_MAP:
            group, series_name = "pzem", PZEM_MAP[raw]
        elif raw in SOLAR_MAP:
            group, series_name = "solar", SOLAR_MAP[raw]
        else:
            continue
        item = zabbix.item_lookup(hostid, spec.zabbix_key)
        if not item:
            continue
        for row in zabbix.trend_rows(str(item["itemid"]), time_from=t_from, time_till=t_till, limit=2000):
            try:
                clock = int(row["clock"])
                avg = float(row["value_avg"])
                low = float(row.get("value_min"))
                high = float(row.get("value_max"))
                count = int(row.get("num") or 0)
            except (TypeError, ValueError, KeyError):
                continue
            bucket = bucket_of(clock * 1000)
            if bucket not in buckets_needed:
                continue
            slot = result[group].setdefault(series_name, {})
            existing = slot.get(bucket)
            if existing is None:
                slot[bucket] = {"ts": bucket, "value": avg, "avg": avg, "min": low, "max": high, "count": count}
            else:
                total = existing["count"] + count
                existing["avg"] = round((existing["avg"] * existing["count"] + avg * count) / total, 4) if total else avg
                existing["value"] = existing["avg"]
                existing["min"] = min(existing["min"], low)
                existing["max"] = max(existing["max"], high)
                existing["count"] = total
    # Instantaneous power is derived per bucket exactly like the rollup path.
    pv_v = result["mppt"].get("pvVoltage", {})
    pv_a = result["mppt"].get("pvCurrent", {})
    out_v = result["mppt"].get("outputVoltage", {})
    out_a = result["mppt"].get("outputCurrent", {})
    power = {}
    for bucket, point in pv_v.items():
        other = pv_a.get(bucket)
        if other:
            power[bucket] = {"ts": bucket, "value": round(point["avg"] * other["avg"], 2),
                             "avg": round(point["avg"] * other["avg"], 2),
                             "min": round(point["min"] * other["min"], 2),
                             "max": round(point["max"] * other["max"], 2), "count": point["count"]}
    if power:
        result["mppt"]["pvInputPower"] = power
    opower = {}
    for bucket, point in out_v.items():
        other = out_a.get(bucket)
        if other:
            opower[bucket] = {"ts": bucket, "value": round(point["avg"] * other["avg"], 2),
                              "avg": round(point["avg"] * other["avg"], 2),
                              "min": round(point["min"] * other["min"], 2),
                              "max": round(point["max"] * other["max"], 2), "count": point["count"]}
    if opower:
        result["mppt"]["outputPower"] = opower
    return result


def _energy_trend_points(zabbix, device_id: str, buckets_needed: set[int], bucket_of, time_from_ms: int, time_till_ms: int) -> dict[int, dict]:
    """Counter samples for buckets without raw coverage: hourly trend max/min."""
    out: dict[int, dict] = {}
    if not buckets_needed or not getattr(zabbix, "configured", False) or not hasattr(zabbix, "trend_rows"):
        return out
    try:
        found = zabbix.host_lookup(technical_host_name(device_id))
        if not found:
            return out
        item = zabbix.item_lookup(str(found["hostid"]), ITEM_SPECS["kwh"].zabbix_key)
        if not item:
            return out
        for row in zabbix.trend_rows(str(item["itemid"]), time_from=int(time_from_ms // 1000), time_till=int((time_till_ms + 999) // 1000), limit=2000):
            try:
                clock = int(row["clock"])
                low = float(row.get("value_min"))
                high = float(row.get("value_max"))
            except (TypeError, ValueError, KeyError):
                continue
            bucket = bucket_of(clock * 1000)
            if bucket not in buckets_needed:
                continue
            prev = out.get(bucket)
            if prev is None:
                out[bucket] = {"start": low, "end": high, "count": 0}
            else:
                prev["end"] = high
                prev["start"] = min(prev["start"], low)
    except (ZabbixError, AttributeError):
        return out
    return out

def build_telemetry_history_response(
    store: Store,
    zabbix: ZabbixClient,
    device_id: str,
    *,
    time_from_ms: int | None = None,
    time_till_ms: int | None = None,
    z_from: int | None = None,
    z_till: int | None = None,
    requested_range: str | None = None,
    requested_resolution: str | None = None,
    limit: int = 288,
    max_data_points: int | None = None,
) -> dict[str, Any]:
    """Build multi-resolution historical telemetry response conforming to R3.1 contract."""
    now_ms = int(time.time() * 1000)
    today_start_ms = jakarta_day_bucket_start(now_ms)

    if time_till_ms is None:
        time_till_ms = now_ms
    if time_from_ms is None:
        range_durations = {
            "15m": 15 * 60_000,
            "1h": 3600_000,
            "6h": 6 * 3600_000,
            "12h": 12 * 3600_000,
            "24h": 24 * 3600_000,
            "7d": 7 * 86_400_000,
            "30d": 30 * 86_400_000,
        }
        dur = range_durations.get(requested_range or "24h", 24 * 3600_000)
        time_from_ms = time_till_ms - dur

    span_ms = time_till_ms - time_from_ms

    # Tier and plot interval. Raw history serves everything up to 30 h; longer
    # spans come from the hourly aggregates Zabbix keeps for a year. The plot
    # interval then follows the span so the point count tracks the panel width
    # (Grafana's max-data-points rule) instead of a fixed per-range resolution.
    target_points = max(50, min(int(max_data_points or SERIES_TARGET_POINTS), 5000))
    if requested_resolution in ("raw", "hourly"):
        resolution = requested_resolution
    else:
        resolution = "raw" if span_ms <= RAW_TIER_MAX_MS else "hourly"
    series_interval_ms = resolve_series_interval_ms(
        span_ms,
        SAMPLE_INTERVAL_MS if resolution == "raw" else 3_600_000,
        target_points,
    )
    def _get_fallback_metric_state() -> dict[str, str]:
        found_keys: set[str] = set()
        lookup_failed = False
        if getattr(zabbix, "configured", False) and hasattr(zabbix, "host_lookup"):
            try:
                host = technical_host_name(device_id)
                found_host = zabbix.host_lookup(host)
                if found_host:
                    hostid = str(found_host["hostid"])
                    for raw, spec in ITEM_SPECS.items():
                        if hasattr(zabbix, "item_lookup") and zabbix.item_lookup(hostid, spec.zabbix_key):
                            found_keys.add(spec.zabbix_key)
            except Exception as exc:
                # A failed lookup is not evidence that a metric was never wired up.
                # Reporting not_integrated here would assert a fact we failed to read.
                logger.warning("metric state lookup failed for %s: %r", device_id, exc)
                lookup_failed = True
        state: dict[str, str] = {}
        configured = getattr(zabbix, "configured", False)
        for group, mapping in (("mppt", MPPT_MAP), ("pzem", PZEM_MAP), ("solar", SOLAR_MAP)):
            for raw_field, series_name in mapping.items():
                zkey = ITEM_SPECS[raw_field].zabbix_key
                if lookup_failed or not configured:
                    state[f"{group}.{series_name}"] = METRIC_ERROR
                elif zkey not in found_keys:
                    state[f"{group}.{series_name}"] = METRIC_NOT_INTEGRATED
                else:
                    state[f"{group}.{series_name}"] = METRIC_NO_DATA_IN_RANGE
        return state
    metric_state = _get_fallback_metric_state()

    if resolution == "raw":
        time_from_sec = z_from if z_from is not None else (time_from_ms // 1000 if time_from_ms is not None else None)
        time_till_sec = z_till if z_till is not None else (time_till_ms // 1000 if time_till_ms is not None else None)
        # The fetcher pages until the window is covered, so neither the caller's
        # `limit` nor an assumed cadence can shrink what the chart sees.
        raw_res = _raw_tier_history(zabbix, device_id, time_from_sec, time_till_sec)
        mppt = raw_res.get("mppt") or {}
        pzem = raw_res.get("pzem") or {}
        solar = raw_res.get("solar") or {"radiation": []}
        metric_state = raw_res.get("metricState") or metric_state

        pv_v_pts = {p["ts"]: p["value"] for p in mppt.get("pvVoltage", []) if "ts" in p and "value" in p}
        pv_a_pts = {p["ts"]: p["value"] for p in mppt.get("pvCurrent", []) if "ts" in p and "value" in p}
        out_v_pts = {p["ts"]: p["value"] for p in mppt.get("outputVoltage", []) if "ts" in p and "value" in p}
        out_a_pts = {p["ts"]: p["value"] for p in mppt.get("outputCurrent", []) if "ts" in p and "value" in p}

        pv_power_pts = []
        for ts, v in pv_v_pts.items():
            a = pv_a_pts.get(ts)
            if a is not None and v >= 0 and a >= 0:
                pv_power_pts.append({"ts": ts, "value": round(v * a, 2)})
        pv_power_pts.sort(key=lambda p: p["ts"])

        out_power_pts = []
        for ts, v in out_v_pts.items():
            a = out_a_pts.get(ts)
            if a is not None and v >= 0 and a >= 0:
                out_power_pts.append({"ts": ts, "value": round(v * a, 2)})
        out_power_pts.sort(key=lambda p: p["ts"])

        mppt["pvInputPower"] = pv_power_pts
        mppt["outputPower"] = out_power_pts

        energy_series = pzem.get("energy", [])
        energy_pairs = [(p["ts"], p["value"]) for p in energy_series if "ts" in p and "value" in p]
        expected_points = max(1, (span_ms // (20 * 1000))) if span_ms > 0 else limit
        energy_agg = aggregate_energy_counter(energy_pairs, expected_count=expected_points)

        today_pairs = [p for p in energy_pairs if p[0] >= today_start_ms]
        today_agg = aggregate_energy_counter(today_pairs, expected_count=max(1, (now_ms - today_start_ms) // (20 * 1000)))

        latest_accum = energy_series[-1]["value"] if (energy_series and "value" in energy_series[-1]) else None

        # The summaries above are computed from raw samples; from here the
        # response carries the display series: one bucket per plot point, each
        # keeping the min/max/count behind its average so a tooltip never passes
        # an aggregate off as a raw sample.
        for group in (mppt, pzem, solar):
            for name in list(group.keys()):
                if name == "energy":
                    continue
                group[name] = bucket_gauge_points(group[name], series_interval_ms)
        pzem["energy"] = bucket_counter_points(
            energy_series,
            series_interval_ms,
            expected_samples=max(1, series_interval_ms // SAMPLE_INTERVAL_MS),
        )

        all_sample_ts = sorted(list({
            p["ts"] for series in list(mppt.values()) + list(pzem.values()) for p in series if "ts" in p
        }))
        window_echo = raw_res.get("window", {})
        win_cov = compute_window_coverage(
            all_sample_ts,
            time_from_ms,
            time_till_ms,
            resolution="raw",
            truncated=window_echo.get("truncated", False),
            quality_override=energy_agg["quality"],
            bucket_interval_ms=series_interval_ms,
        )
        win_cov["range"] = requested_range
        win_cov["limit"] = window_echo.get("limit", limit)

        return {
            "deviceId": device_id,
            "resolution": "raw",
            "seriesIntervalMs": series_interval_ms,
            "window": win_cov,
            "mppt": mppt,
            "pzem": pzem,
            "energySummary": {
                "accumulatedKwh": latest_accum,
                "periodDeltaKwh": energy_agg["energy_delta_kwh"],
                "todayKwh": today_agg["energy_delta_kwh"],
                "quality": energy_agg["quality"],
                "coverageRatio": win_cov["windowTimeCoverageRatio"],
                "windowTimeCoverageRatio": win_cov["windowTimeCoverageRatio"],
                "sampleCoverageRatio": win_cov["sampleCoverageRatio"],
            },
            "solar": solar,
            "metricState": metric_state,
        }

    elif resolution == "hourly":
        # Ensure rollups are synchronized from Zabbix
        sync_device_rollups_from_zabbix(store, zabbix, device_id, time_from_ms, time_till_ms)
        hourly_rows = store.get_hourly_rollups(device_id, time_from_ms, time_till_ms)

        mppt = {
            "pvVoltage": [], "pvCurrent": [], "outputVoltage": [], "outputCurrent": [],
            "pvInputPower": [], "outputPower": []
        }
        pzem = {
            "phaseAVoltage": [], "phaseBVoltage": [], "phaseCVoltage": [],
            "phaseACurrent": [], "phaseBCurrent": [], "phaseCCurrent": [],
            "energy": []
        }
        solar = {"radiation": []}

        total_delta_kwh = 0.0
        latest_accum = None
        has_reset = False
        total_samples = 0
        expected_total = max(1, (span_ms // 3600_000) * HOURLY_EXPECTED_SAMPLES)

        for h in hourly_rows:
            ts = h["bucket_start"]
            cnt = h["sample_count"]
            total_samples += cnt
            total_delta_kwh += h.get("energy_delta_kwh", 0.0)
            if h.get("energy_end_kwh") is not None:
                latest_accum = h["energy_end_kwh"]
            if h.get("energy_quality") == QUALITY_RESET_DETECTED:
                has_reset = True

            # MPPT series with min/avg/max
            if h.get("pv_v_avg") is not None:
                mppt["pvVoltage"].append({"ts": ts, "value": h["pv_v_avg"], "avg": h["pv_v_avg"], "min": h["pv_v_min"], "max": h["pv_v_max"], "count": cnt})
            if h.get("pv_a_avg") is not None:
                mppt["pvCurrent"].append({"ts": ts, "value": h["pv_a_avg"], "avg": h["pv_a_avg"], "min": h["pv_a_min"], "max": h["pv_a_max"], "count": cnt})
            if h.get("out_v_avg") is not None:
                mppt["outputVoltage"].append({"ts": ts, "value": h["out_v_avg"], "avg": h["out_v_avg"], "min": h["out_v_min"], "max": h["out_v_max"], "count": cnt})
            if h.get("out_a_avg") is not None:
                mppt["outputCurrent"].append({"ts": ts, "value": h["out_a_avg"], "avg": h["out_a_avg"], "min": h["out_a_min"], "max": h["out_a_max"], "count": cnt})
            if h.get("pv_power_avg") is not None:
                mppt["pvInputPower"].append({"ts": ts, "value": h["pv_power_avg"], "avg": h["pv_power_avg"], "min": h["pv_power_min"], "max": h["pv_power_max"], "count": cnt})
            if h.get("out_power_avg") is not None:
                mppt["outputPower"].append({"ts": ts, "value": h["out_power_avg"], "avg": h["out_power_avg"], "min": h["out_power_min"], "max": h["out_power_max"], "count": cnt})

            # PZEM series
            if h.get("pzem_va_avg") is not None:
                pzem["phaseAVoltage"].append({"ts": ts, "value": h["pzem_va_avg"], "avg": h["pzem_va_avg"], "min": h["pzem_va_min"], "max": h["pzem_va_max"], "count": cnt})
            if h.get("pzem_vb_avg") is not None:
                pzem["phaseBVoltage"].append({"ts": ts, "value": h["pzem_vb_avg"], "avg": h["pzem_vb_avg"], "min": h["pzem_vb_min"], "max": h["pzem_vb_max"], "count": cnt})
            if h.get("pzem_vc_avg") is not None:
                pzem["phaseCVoltage"].append({"ts": ts, "value": h["pzem_vc_avg"], "avg": h["pzem_vc_avg"], "min": h["pzem_vc_min"], "max": h["pzem_vc_max"], "count": cnt})
            if h.get("pzem_ia_avg") is not None:
                pzem["phaseACurrent"].append({"ts": ts, "value": h["pzem_ia_avg"], "avg": h["pzem_ia_avg"], "min": h["pzem_ia_min"], "max": h["pzem_ia_max"], "count": cnt})
            if h.get("pzem_ib_avg") is not None:
                pzem["phaseBCurrent"].append({"ts": ts, "value": h["pzem_ib_avg"], "avg": h["pzem_ib_avg"], "min": h["pzem_ib_min"], "max": h["pzem_ib_max"], "count": cnt})
            if h.get("pzem_ic_avg") is not None:
                pzem["phaseCCurrent"].append({"ts": ts, "value": h["pzem_ic_avg"], "avg": h["pzem_ic_avg"], "min": h["pzem_ic_min"], "max": h["pzem_ic_max"], "count": cnt})
            if h.get("energy_end_kwh") is not None:
                pzem["energy"].append({"ts": ts, "value": h["energy_end_kwh"], "delta": h["energy_delta_kwh"], "quality": h["energy_quality"]})

            if h.get("solar_rad_avg") is not None:
                solar["radiation"].append({"ts": ts, "value": h["solar_rad_avg"], "avg": h["solar_rad_avg"], "min": h["solar_rad_min"], "max": h["solar_rad_max"], "count": cnt})

        _fill_missing_buckets(
            zabbix, device_id, time_from_ms, time_till_ms,
            bucket_of=hour_bucket_start,
            buckets_in_window=lambda: _hour_buckets(time_from_ms, time_till_ms),
            covered={h["bucket_start"] for h in hourly_rows},
            series_maps=(("mppt", mppt), ("pzem", pzem), ("solar", solar)),
        )

        # Zabbix writes a trend row once the hour has values but the newest hour is
        # always behind wall clock, so a long window used to stop an hour or two
        # short of "now" whenever the rollup cache had not been refreshed. Read the
        # remaining tail from raw history and bucket it onto the same grid: real
        # samples, no interpolation.
        tail_from_ms = _newest_covered_ms(mppt, pzem, solar)
        tail_from_ms = tail_from_ms + series_interval_ms if tail_from_ms is not None else time_from_ms
        if tail_from_ms < time_till_ms and hasattr(zabbix, "device_history"):
            tail_res = _raw_tier_history(zabbix, device_id, int(tail_from_ms // 1000), int((time_till_ms + 999) // 1000))
            tail_mppt = tail_res.get("mppt") or {}
            tail_pzem = tail_res.get("pzem") or {}
            tail_solar = tail_res.get("solar") or {}
            for group, tail_group in ((mppt, tail_mppt), (pzem, tail_pzem), (solar, tail_solar)):
                for name in list(group.keys()):
                    if name == "energy":
                        continue
                    tail_points = tail_group.get(name) or []
                    if tail_points:
                        group[name].extend(bucket_gauge_points(tail_points, series_interval_ms))
                        group[name].sort(key=lambda p: p["ts"])
            tail_energy = tail_pzem.get("energy") or []
            if tail_energy:
                tail_rows = bucket_counter_points(tail_energy, series_interval_ms, expected_samples=max(1, series_interval_ms // SAMPLE_INTERVAL_MS))
                if tail_rows:
                    total_delta_kwh += sum(float(row.get("delta") or 0.0) for row in tail_rows)
                    # The newest counter reading is the tail's own last row; the
                    # accumulated meter value must follow it even when the older
                    # tier contributed no energy rows at all.
                    latest_accum = tail_rows[-1]["value"]
                    pzem["energy"].extend(tail_rows)
                    pzem["energy"].sort(key=lambda p: p["ts"])

        # Collapse the hourly aggregates onto the plot grid. The tier cannot go
        # finer than the hour (that is what Zabbix keeps for a year), and a very
        # long window coarsens it further; the response states which interval was
        # actually plotted.
        for group in (mppt, pzem, solar):
            for name in list(group.keys()):
                if name == "energy":
                    continue
                group[name] = bucket_gauge_points(group[name], series_interval_ms)
        pzem["energy"] = _bucket_counter_rows(pzem["energy"], series_interval_ms)

        # Update metric states
        for group, series_map in (("mppt", mppt), ("pzem", pzem), ("solar", solar)):
            for sname, sdata in series_map.items():
                k = f"{group}.{sname}"
                if metric_state.get(k) != METRIC_NOT_INTEGRATED:
                    metric_state[k] = METRIC_DATA if sdata else METRIC_NO_DATA_IN_RANGE

        # Today energy
        today_hours = store.get_hourly_rollups(device_id, today_start_ms, now_ms)
        today_delta = round(sum(h.get("energy_delta_kwh", 0.0) for h in today_hours), 4)

        overall_quality = QUALITY_RESET_DETECTED if has_reset else (QUALITY_VALID if total_samples > 0 else QUALITY_MISSING_DATA)
        bucket_ts = sorted({
            p["ts"]
            for series in list(mppt.values()) + list(pzem.values()) + list(solar.values())
            for p in series if "ts" in p
        })
        win_cov = compute_window_coverage(
            bucket_ts,
            time_from_ms,
            time_till_ms,
            resolution="hourly",
            truncated=False,
            quality_override=overall_quality,
            bucket_interval_ms=series_interval_ms,
        )
        win_cov["range"] = requested_range
        win_cov["limit"] = limit

        return {
            "deviceId": device_id,
            "resolution": "hourly",
            "seriesIntervalMs": series_interval_ms,
            "window": win_cov,
            "mppt": mppt,
            "pzem": pzem,
            "energySummary": {
                "accumulatedKwh": latest_accum,
                "periodDeltaKwh": round(total_delta_kwh, 4),
                "todayKwh": today_delta,
                "quality": overall_quality,
                "coverageRatio": win_cov["windowTimeCoverageRatio"],
                "windowTimeCoverageRatio": win_cov["windowTimeCoverageRatio"],
                "sampleCoverageRatio": win_cov["sampleCoverageRatio"],
            },
            "solar": solar,
            "metricState": metric_state,
        }
