"""TELNUSA NMS R3.1: Historical Analytics & Energy Aggregation Engine.

Deterministic time-series rollup, energy cumulative counter delta calculation,
reset detection, and weighted aggregation adhering to Asia/Jakarta boundaries.
"""
from __future__ import annotations

import math
from typing import Any

# Cadence & Timezone Constants
SAMPLE_INTERVAL_SECONDS = 20
SAMPLE_INTERVAL_MS = SAMPLE_INTERVAL_SECONDS * 1000

HOURLY_EXPECTED_SAMPLES = 3600 // SAMPLE_INTERVAL_SECONDS  # 180

JAKARTA_OFFSET_HOURS = 7
JAKARTA_OFFSET_MS = JAKARTA_OFFSET_HOURS * 3600 * 1000

# Energy Quality Flags
QUALITY_VALID = "VALID"
QUALITY_RESET_DETECTED = "RESET_DETECTED"
QUALITY_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
QUALITY_MISSING_DATA = "MISSING_DATA"


# Display density contract. The plot interval follows the requested span so the
# point count tracks the panel width (Grafana's "max data points" rule, whose
# default is the panel's pixel width); the previous fixed raw/hourly/daily matrix
# drew 1140 points for 6 h and 24 points for 24 h on the same panel.
SERIES_TARGET_POINTS = 720
NICE_INTERVALS_MS = (
    20_000, 30_000, 60_000, 120_000, 180_000, 300_000, 600_000, 900_000,
    1_800_000, 3_600_000, 7_200_000, 10_800_000, 21_600_000, 43_200_000, 86_400_000,
)

# Longest span still served from raw history: the 20 s cadence fills it inside
# the Zabbix per-request ceiling (30 h = 5400 samples, ceiling 6000).
RAW_TIER_MAX_MS = 30 * 3_600_000

def hour_bucket_start(ts_ms: int) -> int:
    """Return the epoch millisecond of the top of the clock hour for ts_ms."""
    return (int(ts_ms) // 3_600_000) * 3_600_000

def resolve_series_interval_ms(span_ms: int, source_interval_ms: int = SAMPLE_INTERVAL_MS, target_points: int = SERIES_TARGET_POINTS) -> int:
    """Smallest nice plot interval whose point count stays within target_points.

    `source_interval_ms` is the floor the tier can deliver: raw samples arrive
    every 20 s, Zabbix hourly trends every hour. A 7 d window therefore plots
    hourly points however fine the requested target is.
    """
    if span_ms <= 0:
        return max(source_interval_ms, SAMPLE_INTERVAL_MS)
    needed_ms = span_ms / max(1, int(target_points))
    for nice_ms in NICE_INTERVALS_MS:
        if nice_ms >= needed_ms:
            return max(nice_ms, source_interval_ms)
    return max(NICE_INTERVALS_MS[-1], source_interval_ms)

def bucket_start(ts_ms: int, interval_ms: int) -> int:
    """Epoch-aligned bucket start; stable across requests so ranges line up."""
    if interval_ms <= 0:
        return int(ts_ms)
    return (int(ts_ms) // interval_ms) * interval_ms


def jakarta_day_bucket_start(ts_ms: int) -> int:
    """Return the epoch millisecond of 00:00:00 Asia/Jakarta for ts_ms."""
    shifted = int(ts_ms) + JAKARTA_OFFSET_MS
    day_shifted = (shifted // 86_400_000) * 86_400_000
    return day_shifted - JAKARTA_OFFSET_MS


def aggregate_energy_counter(samples: list[tuple[int, float | None]], expected_count: int = HOURLY_EXPECTED_SAMPLES) -> dict[str, Any]:
    """Compute cumulative energy counter deltas over sequential chronological samples.

    Algorithm:
    - delta = current_counter - previous_counter
    - if delta >= 0: energy_delta += delta
    - if delta < 0: reset detected; do not subtract; establish new baseline
    - returns { energy_start, energy_end, energy_delta, resets, sample_count, coverage_ratio, quality }
    """
    valid_samples = [(ts, float(val)) for ts, val in samples if val is not None and not math.isnan(float(val))]
    if not valid_samples:
        return {
            "energy_start_kwh": None,
            "energy_end_kwh": None,
            "energy_delta_kwh": 0.0,
            "resets": 0,
            "sample_count": 0,
            "expected_count": expected_count,
            "coverage_ratio": 0.0,
            "quality": QUALITY_MISSING_DATA,
        }

    valid_samples.sort(key=lambda s: s[0])
    count = len(valid_samples)
    start_kwh = valid_samples[0][1]
    end_kwh = valid_samples[-1][1]

    total_delta = 0.0
    resets = 0
    prev = start_kwh

    for _, curr in valid_samples[1:]:
        delta = curr - prev
        if delta >= 0:
            total_delta += delta
        else:
            # Meter reset / rollover / meter replacement / counter decrease
            resets += 1
            # Baseline is reset to curr; no negative subtraction
        prev = curr

    coverage = round(min(1.0, count / max(1, expected_count)), 4)
    if resets > 0:
        quality = QUALITY_RESET_DETECTED
    elif count < 2 and expected_count > 1:
        quality = QUALITY_INSUFFICIENT_DATA
    else:
        quality = QUALITY_VALID

    return {
        "energy_start_kwh": round(start_kwh, 4),
        "energy_end_kwh": round(end_kwh, 4),
        "energy_delta_kwh": round(max(0.0, total_delta), 4),
        "resets": resets,
        "sample_count": count,
        "expected_count": expected_count,
        "coverage_ratio": coverage,
        "quality": quality,
    }


def bucket_gauge_points(points: list[dict], interval_ms: int) -> list[dict]:
    """Aggregate gauge points into fixed buckets.

    Accepts raw samples (`ts`/`value`) and already-aggregated rows (`value` plus
    `min`/`max`/`count`). Each bucket reports the sample-weighted average that is
    plotted, the extremes behind it and the sample count, so an aggregated view
    can never be mistaken for raw samples.
    """
    buckets: dict[int, dict[str, float | int]] = {}
    for point in points:
        try:
            ts = int(point["ts"])
            value = float(point["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        count = int(point.get("count") or 1)
        low = point.get("min")
        high = point.get("max")
        low = float(low) if low is not None else value
        high = float(high) if high is not None else value
        slot = buckets.setdefault(bucket_start(ts, interval_ms), {"weighted": 0.0, "count": 0, "min": low, "max": high})
        slot["weighted"] = float(slot["weighted"]) + value * count
        slot["count"] = int(slot["count"]) + count
        slot["min"] = min(float(slot["min"]), low)
        slot["max"] = max(float(slot["max"]), high)

    out: list[dict] = []
    for ts in sorted(buckets):
        slot = buckets[ts]
        count = int(slot["count"])
        avg = round(float(slot["weighted"]) / count, 4) if count else None
        if avg is None:
            continue
        out.append({
            "ts": ts,
            "value": avg,
            "avg": avg,
            "min": round(float(slot["min"]), 4),
            "max": round(float(slot["max"]), 4),
            "count": count,
        })
    return out

def bucket_counter_points(points: list[dict], interval_ms: int, expected_samples: int = 1) -> list[dict]:
    """Aggregate cumulative counter samples (kWh) into fixed buckets.

    A counter is only meaningful at sample time, so a bucket carries its last
    reading (plotted) plus the consumption inside it, with resets detected by the
    same rule as the window summary.
    """
    buckets: dict[int, list[tuple[int, float]]] = {}
    for point in points:
        try:
            ts = int(point["ts"])
            value = float(point["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        slot = buckets.setdefault(bucket_start(ts, interval_ms), [])
        low = point.get("min")
        high = point.get("max")
        if low is not None and high is not None:
            # An aggregated row knows its own bounds; the average would fake a
            # reading that was never taken.
            slot.append((ts, float(low)))
            slot.append((ts + 1, float(high)))
        else:
            slot.append((ts, value))

    out: list[dict] = []
    for ts in sorted(buckets):
        agg = aggregate_energy_counter(buckets[ts], expected_count=expected_samples)
        if agg["energy_end_kwh"] is None:
            continue
        out.append({
            "ts": ts,
            "value": agg["energy_end_kwh"],
            "delta": agg["energy_delta_kwh"],
            "quality": agg["quality"],
        })
    return out

def compute_window_coverage(
    timestamps: list[int],
    requested_from_ms: int,
    requested_to_ms: int,
    resolution: str = "raw",
    truncated: bool = False,
    quality_override: str | None = None,
    bucket_interval_ms: int | None = None,
) -> dict[str, Any]:
    """Compute explicit window metadata distinguishing requested window, available span, and observed time coverage."""
    req_dur = max(0, requested_to_ms - requested_from_ms)

    if resolution == "hourly":
        cadence_ms = 3600_000
        gap_threshold_ms = 2 * 3600_000
    elif resolution == "daily":
        cadence_ms = 86_400_000
        gap_threshold_ms = 2 * 86_400_000
    else:  # raw
        cadence_ms = SAMPLE_INTERVAL_MS  # 20_000
        gap_threshold_ms = 3 * SAMPLE_INTERVAL_MS  # 60_000
    if bucket_interval_ms and bucket_interval_ms > 0:
        # The chart plots buckets, so coverage is measured against the bucket
        # grid: three empty buckets in a row are a gap on screen whatever the
        # source cadence behind them was.
        cadence_ms = int(bucket_interval_ms)
        gap_threshold_ms = 3 * int(bucket_interval_ms)

    expected_points = max(1, math.ceil(req_dur / cadence_ms)) if req_dur > 0 else 1
    valid_ts = sorted(list({int(t) for t in timestamps if t is not None and requested_from_ms <= int(t) <= requested_to_ms}))
    pts_count = len(valid_ts)

    if pts_count == 0:
        return {
            "fromMs": requested_from_ms,
            "toMs": requested_to_ms,
            "requestedFrom": requested_from_ms,
            "requestedTo": requested_to_ms,
            "requestedDuration": req_dur,
            "availableFrom": None,
            "availableTo": None,
            "availableDuration": 0,
            "observedDuration": 0,
            "resolution": resolution,
            "truncated": truncated,
            "dataPoints": 0,
            "expectedPoints": expected_points,
            "windowTimeCoverageRatio": 0.0,
            "observedTimeCoverageRatio": 0.0,
            "availableSpanRatio": 0.0,
            "sampleCoverageRatio": 0.0,
            "coverageRatio": 0.0,
            "complete": False,
            "hasGaps": False,
            "gapCount": 0,
            "leadingGap": True if req_dur > 0 else False,
            "trailingGap": True if req_dur > 0 else False,
            "quality": quality_override or QUALITY_MISSING_DATA,
        }

    avail_from = valid_ts[0]
    avail_to = valid_ts[-1]
    avail_dur = max(0, avail_to - avail_from)
    avail_span_ratio = round(min(1.0, avail_dur / req_dur), 4) if req_dur > 0 else 0.0

    gap_count = 0
    total_observed_ms = 0

    curr_start = avail_from
    curr_end = min(requested_to_ms, avail_from + cadence_ms)

    for i in range(len(valid_ts) - 1):
        dt = valid_ts[i + 1] - valid_ts[i]
        if dt > gap_threshold_ms:
            gap_count += 1
            block_clipped_start = max(requested_from_ms, curr_start)
            block_clipped_end = min(requested_to_ms, curr_end)
            if block_clipped_end > block_clipped_start:
                total_observed_ms += (block_clipped_end - block_clipped_start)
            curr_start = valid_ts[i + 1]
            curr_end = min(requested_to_ms, max(curr_end, valid_ts[i + 1] + cadence_ms))
        else:
            curr_end = min(requested_to_ms, max(curr_end, valid_ts[i + 1] + cadence_ms))

    block_clipped_start = max(requested_from_ms, curr_start)
    block_clipped_end = min(requested_to_ms, curr_end)
    if block_clipped_end > block_clipped_start:
        total_observed_ms += (block_clipped_end - block_clipped_start)

    leading_gap = (avail_from - requested_from_ms) > gap_threshold_ms
    trailing_gap = (requested_to_ms - avail_to) > gap_threshold_ms
    has_gaps = gap_count > 0 or leading_gap or trailing_gap

    observed_time_cov = round(min(1.0, total_observed_ms / req_dur), 4) if req_dur > 0 else 0.0
    sample_coverage = round(min(1.0, pts_count / expected_points), 4) if expected_points > 0 else 0.0
    complete = (observed_time_cov >= 0.95) and (not truncated) and (not leading_gap) and (not trailing_gap) and (gap_count == 0)
    quality = quality_override or (QUALITY_VALID if pts_count >= 2 else QUALITY_INSUFFICIENT_DATA)

    return {
        "fromMs": requested_from_ms,
        "toMs": requested_to_ms,
        "requestedFrom": requested_from_ms,
        "requestedTo": requested_to_ms,
        "requestedDuration": req_dur,
        "availableFrom": avail_from,
        "availableTo": avail_to,
        "availableDuration": avail_dur,
        "observedDuration": total_observed_ms,
        "resolution": resolution,
        "truncated": truncated,
        "dataPoints": pts_count,
        "expectedPoints": expected_points,
        "windowTimeCoverageRatio": observed_time_cov,
        "observedTimeCoverageRatio": observed_time_cov,
        "availableSpanRatio": avail_span_ratio,
        "sampleCoverageRatio": sample_coverage,
        "coverageRatio": observed_time_cov,
        "complete": complete,
        "hasGaps": has_gaps,
        "gapCount": gap_count,
        "leadingGap": leading_gap,
        "trailingGap": trailing_gap,
        "quality": quality,
    }

def compute_gauge_stats(values: list[float | None]) -> tuple[float | None, float | None, float | None, int]:
    """Compute (avg, min, max, valid_count) for a list of scalar gauge values."""
    valid = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not valid:
        return None, None, None, 0
    return round(sum(valid) / len(valid), 2), round(min(valid), 2), round(max(valid), 2), len(valid)


def compute_power_samples(v_samples: list[float | None], a_samples: list[float | None]) -> list[float]:
    """Compute instantaneous power samples V * A when both V and A are valid and >= 0.

    Ensures power is aggregated from actual power samples, NOT avg(V) * avg(I).
    """
    out: list[float] = []
    for v, a in zip(v_samples, a_samples):
        if v is not None and a is not None and not math.isnan(float(v)) and not math.isnan(float(a)):
            vf, af = float(v), float(a)
            if vf >= 0 and af >= 0:
                out.append(round(vf * af, 2))
    return out


def weighted_average(bucket_averages: list[float | None], sample_counts: list[int]) -> float | None:
    """Compute sample-weighted average: sum(avg * count) / sum(count)."""
    total_weighted = 0.0
    total_samples = 0
    for avg, count in zip(bucket_averages, sample_counts):
        if avg is not None and count > 0:
            total_weighted += float(avg) * int(count)
            total_samples += int(count)
    if total_samples == 0:
        return None
    return round(total_weighted / total_samples, 2)


def compute_hourly_rollup_from_raw(device_id: str, bucket_start: int, raw_samples: list[dict]) -> dict[str, Any]:
    """Aggregate raw telemetry samples in an hourly bucket [bucket_start, bucket_start + 3600_000).

    raw_samples is a list of normalized dicts with keys:
    ts, pvVoltage, pvCurrent, outputVoltage, outputCurrent,
    phaseAVoltage, phaseBVoltage, phaseCVoltage,
    phaseACurrent, phaseBCurrent, phaseCCurrent, energy, radiation
    """
    bucket_end = bucket_start + 3_600_000
    in_bucket = [s for s in raw_samples if bucket_start <= s.get("ts", 0) < bucket_end]
    in_bucket.sort(key=lambda s: s.get("ts", 0))

    sample_count = len(in_bucket)
    coverage_ratio = round(min(1.0, sample_count / HOURLY_EXPECTED_SAMPLES), 4)

    # Energy aggregation
    energy_pairs = [(s.get("ts", 0), s.get("energy")) for s in in_bucket]
    energy_agg = aggregate_energy_counter(energy_pairs, expected_count=HOURLY_EXPECTED_SAMPLES)

    # MPPT Gauges
    pv_v_list = [s.get("pvVoltage") for s in in_bucket]
    pv_a_list = [s.get("pvCurrent") for s in in_bucket]
    out_v_list = [s.get("outputVoltage") for s in in_bucket]
    out_a_list = [s.get("outputCurrent") for s in in_bucket]

    pv_v_avg, pv_v_min, pv_v_max, _ = compute_gauge_stats(pv_v_list)
    pv_a_avg, pv_a_min, pv_a_max, _ = compute_gauge_stats(pv_a_list)
    out_v_avg, out_v_min, out_v_max, _ = compute_gauge_stats(out_v_list)
    out_a_avg, out_a_min, out_a_max, _ = compute_gauge_stats(out_a_list)

    # Instantaneous power samples
    pv_p_samples = compute_power_samples(pv_v_list, pv_a_list)
    out_p_samples = compute_power_samples(out_v_list, out_a_list)
    pv_p_avg, pv_p_min, pv_p_max, _ = compute_gauge_stats(pv_p_samples)
    out_p_avg, out_p_min, out_p_max, _ = compute_gauge_stats(out_p_samples)

    # PZEM Gauges
    va_list = [s.get("phaseAVoltage") for s in in_bucket]
    vb_list = [s.get("phaseBVoltage") for s in in_bucket]
    vc_list = [s.get("phaseCVoltage") for s in in_bucket]
    ia_list = [s.get("phaseACurrent") for s in in_bucket]
    ib_list = [s.get("phaseBCurrent") for s in in_bucket]
    ic_list = [s.get("phaseCCurrent") for s in in_bucket]

    va_avg, va_min, va_max, _ = compute_gauge_stats(va_list)
    vb_avg, vb_min, vb_max, _ = compute_gauge_stats(vb_list)
    vc_avg, vc_min, vc_max, _ = compute_gauge_stats(vc_list)
    ia_avg, ia_min, ia_max, _ = compute_gauge_stats(ia_list)
    ib_avg, ib_min, ib_max, _ = compute_gauge_stats(ib_list)
    ic_avg, ic_min, ic_max, _ = compute_gauge_stats(ic_list)

    # Solar irradiance (W/m²): single gauge field
    rad_list = [s.get("radiation") for s in in_bucket]
    rad_avg, rad_min, rad_max, _ = compute_gauge_stats(rad_list)

    return {
        "device_id": device_id,
        "bucket_start": bucket_start,
        "bucket_end": bucket_end,
        "sample_count": sample_count,
        "expected_count": HOURLY_EXPECTED_SAMPLES,
        "coverage_ratio": coverage_ratio,
        "energy_start_kwh": energy_agg["energy_start_kwh"],
        "energy_end_kwh": energy_agg["energy_end_kwh"],
        "energy_delta_kwh": energy_agg["energy_delta_kwh"],
        "energy_quality": energy_agg["quality"],
        "pv_v_avg": pv_v_avg, "pv_v_min": pv_v_min, "pv_v_max": pv_v_max,
        "pv_a_avg": pv_a_avg, "pv_a_min": pv_a_min, "pv_a_max": pv_a_max,
        "pv_power_avg": pv_p_avg, "pv_power_min": pv_p_min, "pv_power_max": pv_p_max,
        "out_v_avg": out_v_avg, "out_v_min": out_v_min, "out_v_max": out_v_max,
        "out_a_avg": out_a_avg, "out_a_min": out_a_min, "out_a_max": out_a_max,
        "out_power_avg": out_p_avg, "out_power_min": out_p_min, "out_power_max": out_p_max,
        "pzem_va_avg": va_avg, "pzem_va_min": va_min, "pzem_va_max": va_max,
        "pzem_vb_avg": vb_avg, "pzem_vb_min": vb_min, "pzem_vb_max": vb_max,
        "pzem_vc_avg": vc_avg, "pzem_vc_min": vc_min, "pzem_vc_max": vc_max,
        "pzem_ia_avg": ia_avg, "pzem_ia_min": ia_min, "pzem_ia_max": ia_max,
        "pzem_ib_avg": ib_avg, "pzem_ib_min": ib_min, "pzem_ib_max": ib_max,
        "pzem_ic_avg": ic_avg, "pzem_ic_min": ic_min, "pzem_ic_max": ic_max,
        "solar_rad_avg": rad_avg, "solar_rad_min": rad_min, "solar_rad_max": rad_max,
    }

