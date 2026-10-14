"""Tests for TELNUSA NMS R3.1 Analytics, Energy Aggregation, and Rollup Pipeline."""
from __future__ import annotations

import tempfile
import unittest

from backend.analytics import (
    HOURLY_EXPECTED_SAMPLES,
    QUALITY_INSUFFICIENT_DATA,
    QUALITY_MISSING_DATA,
    QUALITY_RESET_DETECTED,
    QUALITY_VALID,
    RAW_TIER_MAX_MS,
    SAMPLE_INTERVAL_MS,
    SERIES_TARGET_POINTS,
    aggregate_energy_counter,
    bucket_counter_points,
    bucket_gauge_points,
    compute_gauge_stats,
    compute_hourly_rollup_from_raw,
    compute_power_samples,
    hour_bucket_start,
    jakarta_day_bucket_start,
    resolve_series_interval_ms,
    weighted_average,
)
from backend.rollup import build_telemetry_history_response
from backend.store import Store


class TestEnergyCounterAggregation(unittest.TestCase):
    def test_positive_deltas_accumulated_correctly(self):
        # 3 samples: 100.0 -> 100.5 -> 101.2 (total delta = 1.2 kWh)
        samples = [
            (1700000000000, 100.0),
            (1700000020000, 100.5),
            (1700000040000, 101.2),
        ]
        res = aggregate_energy_counter(samples, expected_count=3)
        self.assertEqual(res["energy_start_kwh"], 100.0)
        self.assertEqual(res["energy_end_kwh"], 101.2)
        self.assertAlmostEqual(res["energy_delta_kwh"], 1.2)
        self.assertEqual(res["resets"], 0)
        self.assertEqual(res["quality"], QUALITY_VALID)
        self.assertEqual(res["sample_count"], 3)
        self.assertEqual(res["coverage_ratio"], 1.0)

    def test_meter_reset_detected_and_no_negative_subtraction(self):
        # Counter reset / meter replacement: 100.0 -> 101.0 -> (reset to 0.5) -> 1.0
        # Delta 1: 101.0 - 100.0 = 1.0
        # Reset: 0.5 < 101.0 -> no negative delta
        # Delta 2: 1.0 - 0.5 = 0.5
        # Total delta = 1.5 kWh
        samples = [
            (1700000000000, 100.0),
            (1700000020000, 101.0),
            (1700000040000, 0.5),
            (1700000060000, 1.0),
        ]
        res = aggregate_energy_counter(samples, expected_count=4)
        self.assertEqual(res["energy_start_kwh"], 100.0)
        self.assertEqual(res["energy_end_kwh"], 1.0)
        self.assertAlmostEqual(res["energy_delta_kwh"], 1.5)
        self.assertEqual(res["resets"], 1)
        self.assertEqual(res["quality"], QUALITY_RESET_DETECTED)

    def test_flat_counter_yields_zero_delta(self):
        samples = [
            (1700000000000, 50.0),
            (1700000020000, 50.0),
            (1700000040000, 50.0),
        ]
        res = aggregate_energy_counter(samples, expected_count=3)
        self.assertEqual(res["energy_delta_kwh"], 0.0)
        self.assertEqual(res["quality"], QUALITY_VALID)

    def test_empty_and_single_samples(self):
        empty_res = aggregate_energy_counter([], expected_count=180)
        self.assertEqual(empty_res["quality"], QUALITY_MISSING_DATA)
        self.assertEqual(empty_res["energy_delta_kwh"], 0.0)
        self.assertIsNone(empty_res["energy_start_kwh"])

        single_res = aggregate_energy_counter([(1700000000000, 42.0)], expected_count=180)
        self.assertEqual(single_res["quality"], QUALITY_INSUFFICIENT_DATA)
        self.assertEqual(single_res["energy_delta_kwh"], 0.0)
        self.assertEqual(single_res["energy_start_kwh"], 42.0)


class TestInstantaneousPowerSampling(unittest.TestCase):
    def test_power_computed_per_sample_not_avg_product(self):
        # Sample 1: V = 100V, I = 0A -> P = 0W
        # Sample 2: V = 0V, I = 10A -> P = 0W
        # True average power = (0 + 0) / 2 = 0W
        # Flawed avg(V) * avg(I) = 50V * 5A = 250W (WRONG!)
        v_samples = [100.0, 0.0]
        a_samples = [0.0, 10.0]
        power_samples = compute_power_samples(v_samples, a_samples)
        self.assertEqual(power_samples, [0.0, 0.0])

        avg_p, _, _, _ = compute_gauge_stats(power_samples)
        self.assertEqual(avg_p, 0.0)
        # Verify it differs from flawed avg(V) * avg(I)
        avg_v, _, _, _ = compute_gauge_stats(v_samples)
        avg_a, _, _, _ = compute_gauge_stats(a_samples)
        flawed_p = avg_v * avg_a
        self.assertEqual(flawed_p, 250.0)
        self.assertNotEqual(avg_p, flawed_p)


class TestTimezoneCalculations(unittest.TestCase):
    def test_jakarta_day_bucket_start(self):
        # 2026-10-09 02:00:00 UTC = 2026-10-09 09:00:00 Asia/Jakarta (WIB)
        # Jakarta midnight of 2026-10-09 is 2026-10-08 17:00:00 UTC
        utc_instant_ms = 1791511200000  # corresponds to some instant
        day_start = jakarta_day_bucket_start(utc_instant_ms)
        # Day start + 7h must be exact UTC multiple of 86400s
        self.assertEqual((day_start + 7 * 3600 * 1000) % 86_400_000, 0)

    def test_hour_bucket_start(self):
        ts = 1791511234567
        hb = hour_bucket_start(ts)
        self.assertEqual(hb % 3_600_000, 0)
        self.assertTrue(hb <= ts < hb + 3_600_000)


class TestRollupAggregationAndStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.store = Store(self.tmp.name)
        self.device_id = "RUT-1234567890"

    def test_hourly_rollup_storage(self):
        hour_start = 1791500400000
        # Generate 180 raw samples for hour 1 (cadence 20s)
        raw_samples = []
        for i in range(180):
            ts = hour_start + i * 20_000
            raw_samples.append({
                "ts": ts,
                "pvVoltage": 20.0,
                "pvCurrent": 5.0,
                "outputVoltage": 24.0,
                "outputCurrent": 4.0,
                "phaseAVoltage": 230.0,
                "phaseBVoltage": 231.0,
                "phaseCVoltage": 229.0,
                "phaseACurrent": 2.0,
                "phaseBCurrent": 2.1,
                "phaseCCurrent": 1.9,
                "energy": 100.0 + i * 0.01,  # 0.01 kWh per 20s = 1.79 kWh delta
                "radiation": 400.0 + i * 0.5,
            })

        h1 = compute_hourly_rollup_from_raw(self.device_id, hour_start, raw_samples)
        self.assertEqual(h1["sample_count"], 180)
        self.assertEqual(h1["coverage_ratio"], 1.0)
        self.assertEqual(h1["pv_v_avg"], 20.0)
        self.assertEqual(h1["pv_power_avg"], 100.0)  # 20V * 5A = 100W
        self.assertAlmostEqual(h1["energy_delta_kwh"], 1.79)
        self.assertEqual(h1["energy_quality"], QUALITY_VALID)
        self.assertAlmostEqual(h1["solar_rad_avg"], 444.75)  # mean of 400..489.5
        self.assertAlmostEqual(h1["solar_rad_min"], 400.0)
        self.assertAlmostEqual(h1["solar_rad_max"], 489.5)

        self.store.save_hourly_rollup(h1)
        fetched_hours = self.store.get_hourly_rollups(self.device_id, hour_start, hour_start + 3600_000)
        self.assertEqual(len(fetched_hours), 1)
        self.assertEqual(fetched_hours[0]["pv_power_avg"], 100.0)

    def test_build_telemetry_history_response_hourly(self):
        hour_start = 1791500400000
        raw_samples = [
            {
                "ts": hour_start + i * 20_000,
                "pvVoltage": 20.0,
                "pvCurrent": 5.0,
                "outputVoltage": 24.0,
                "outputCurrent": 4.0,
                "phaseAVoltage": 230.0,
                "phaseBVoltage": 231.0,
                "phaseCVoltage": 229.0,
                "phaseACurrent": 2.0,
                "phaseBCurrent": 2.1,
                "phaseCCurrent": 1.9,
                "energy": 50.0 + i * 0.01,
                "radiation": 400.0 + i * 0.5,
            }
            for i in range(180)
        ]
        h1 = compute_hourly_rollup_from_raw(self.device_id, hour_start, raw_samples)
        self.store.save_hourly_rollup(h1)

        class MockZabbix:
            pass

        resp = build_telemetry_history_response(
            self.store,
            MockZabbix(),
            self.device_id,
            time_from_ms=hour_start,
            time_till_ms=hour_start + 24 * 3600 * 1000,
            requested_resolution="hourly",
        )
        self.assertEqual(resp["resolution"], "hourly")
        self.assertEqual(resp["deviceId"], self.device_id)
        self.assertEqual(len(resp["solar"]["radiation"]), 1)
        self.assertAlmostEqual(resp["solar"]["radiation"][0]["avg"], 444.75)
        self.assertEqual(resp["metricState"]["solar.radiation"], "data")
        self.assertEqual(len(resp["mppt"]["pvVoltage"]), 1)
        self.assertEqual(resp["mppt"]["pvVoltage"][0]["avg"], 20.0)
        self.assertEqual(resp["mppt"]["pvInputPower"][0]["avg"], 100.0)
        self.assertAlmostEqual(resp["energySummary"]["periodDeltaKwh"], 1.79)


class TestPlotDensityAndBucketing(unittest.TestCase):
    """Display density contract: the plot interval follows the span so the point
    count tracks the panel width, and every bucket keeps the samples behind it."""

    HOUR_MS = 3_600_000

    def test_interval_per_range_matches_spec(self):
        cases = (
            (15 * 60_000, 20_000),
            (1 * self.HOUR_MS, 20_000),
            (6 * self.HOUR_MS, 30_000),
            (12 * self.HOUR_MS, 60_000),
            (24 * self.HOUR_MS, 120_000),
            (RAW_TIER_MAX_MS, 180_000),
        )
        for span_ms, expected in cases:
            self.assertEqual(resolve_series_interval_ms(span_ms), expected, f"span {span_ms}")

    def test_long_spans_cannot_plot_finer_than_the_trend_tier(self):
        # Zabbix trends are hourly, so a week plots hourly points; a month asks
        # for the same hourly interval, which is what makes it the densest view.
        self.assertEqual(resolve_series_interval_ms(7 * 86_400_000, 3_600_000), 3_600_000)
        self.assertEqual(resolve_series_interval_ms(30 * 86_400_000, 3_600_000), 3_600_000)

    def test_points_track_the_target_across_raw_ranges(self):
        base = 1791500400000
        for span_ms in (6 * self.HOUR_MS, 12 * self.HOUR_MS, 24 * self.HOUR_MS):
            interval = resolve_series_interval_ms(span_ms)
            samples = [{"ts": base + i * SAMPLE_INTERVAL_MS, "value": 5.0} for i in range(span_ms // SAMPLE_INTERVAL_MS)]
            points = bucket_gauge_points(samples, interval)
            self.assertLessEqual(len(points), SERIES_TARGET_POINTS, f"span {span_ms}")
            self.assertGreater(len(points), SERIES_TARGET_POINTS * 0.66, f"span {span_ms}")

    def test_gauge_bucket_reports_average_extremes_and_count(self):
        points = [
            {"ts": 1791500400000, "value": 10.0},
            {"ts": 1791500420000, "value": 20.0},
            {"ts": 1791500440000, "value": 30.0},
        ]
        buckets = bucket_gauge_points(points, 120_000)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["ts"], 1791500400000)
        self.assertEqual(buckets[0]["avg"], 20.0)
        self.assertEqual(buckets[0]["value"], 20.0)
        self.assertEqual(buckets[0]["min"], 10.0)
        self.assertEqual(buckets[0]["max"], 30.0)
        self.assertEqual(buckets[0]["count"], 3)

    def test_gauge_bucket_weights_preaggregated_rows_by_sample_count(self):
        # An hourly row stands for the samples it measured; averaging the row
        # averages unweighted would let a quiet hour outweigh a busy one.
        rows = [
            {"ts": 1791496800000, "value": 10.0, "min": 8.0, "max": 12.0, "count": 180},
            {"ts": 1791500400000, "value": 20.0, "min": 18.0, "max": 22.0, "count": 60},
        ]
        buckets = bucket_gauge_points(rows, 2 * self.HOUR_MS)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["avg"], 12.5)
        self.assertEqual(buckets[0]["min"], 8.0)
        self.assertEqual(buckets[0]["max"], 22.0)
        self.assertEqual(buckets[0]["count"], 240)

    def test_counter_bucket_plots_the_bucket_end_and_sums_consumption(self):
        base = 1791500400000
        samples = [{"ts": base + i * SAMPLE_INTERVAL_MS, "value": 100.0 + i * 0.01} for i in range(12)]
        buckets = bucket_counter_points(samples, 120_000, expected_samples=6)
        self.assertEqual(len(buckets), 2)
        self.assertAlmostEqual(buckets[0]["value"], 100.05)
        self.assertAlmostEqual(buckets[0]["delta"], 0.05)
        self.assertAlmostEqual(buckets[1]["value"], 100.11)
        self.assertAlmostEqual(buckets[1]["delta"], 0.05)
        self.assertEqual(buckets[0]["quality"], QUALITY_VALID)

    def test_counter_reset_inside_a_bucket_is_flagged_not_subtracted(self):
        base = 1791500400000
        samples = [
            {"ts": base, "value": 100.0},
            {"ts": base + 20_000, "value": 0.5},
            {"ts": base + 40_000, "value": 0.6},
        ]
        buckets = bucket_counter_points(samples, 120_000, expected_samples=6)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["value"], 0.6)
        self.assertAlmostEqual(buckets[0]["delta"], 0.1)
        self.assertEqual(buckets[0]["quality"], QUALITY_RESET_DETECTED)

    def test_raw_tier_response_plots_one_point_per_interval(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db")
        store = Store(tmp.name)
        device_id = "RUT-1234567890"
        base = 1791500400000
        span_ms = 24 * self.HOUR_MS
        sample_count = span_ms // SAMPLE_INTERVAL_MS

        class MockZabbix:
            configured = True

            def device_history(self, device_id, time_from=None, time_till=None, limit=288):
                series = [{"ts": base + i * SAMPLE_INTERVAL_MS, "value": 20.0 + (i % 10)} for i in range(sample_count)]
                return {
                    "mppt": {"pvVoltage": series, "pvCurrent": series, "outputVoltage": series, "outputCurrent": series},
                    "pzem": {"phaseAVoltage": series, "phaseBVoltage": series, "phaseCVoltage": series,
                             "phaseACurrent": series, "phaseBCurrent": series, "phaseCCurrent": series,
                             "energy": [{"ts": base + i * SAMPLE_INTERVAL_MS, "value": 100.0 + i * 0.001} for i in range(sample_count)]},
                    "solar": {"radiation": series},
                    "metricState": {},
                    "window": {"truncated": False, "limit": limit},
                }

        resp = build_telemetry_history_response(
            store,
            MockZabbix(),
            device_id,
            time_from_ms=base,
            time_till_ms=base + span_ms,
            max_data_points=SERIES_TARGET_POINTS,
        )
        self.assertEqual(resp["resolution"], "raw")
        self.assertEqual(resp["seriesIntervalMs"], 120_000)
        self.assertEqual(len(resp["mppt"]["pvVoltage"]), SERIES_TARGET_POINTS)
        self.assertEqual(len(resp["pzem"]["energy"]), SERIES_TARGET_POINTS)
        self.assertEqual(resp["window"]["expectedPoints"], SERIES_TARGET_POINTS)
        self.assertFalse(resp["window"]["truncated"])
        for point in resp["mppt"]["pvVoltage"][:5]:
            self.assertLessEqual(point["min"], point["avg"])
            self.assertLessEqual(point["avg"], point["max"])
            self.assertEqual(point["count"], 6)
        self.assertAlmostEqual(resp["energySummary"]["periodDeltaKwh"], (sample_count - 1) * 0.001, places=3)


class TestWindowCoverageAndGaps(unittest.TestCase):
    def test_complete_window_no_gaps(self):
        from backend.analytics import compute_window_coverage
        # 1 hour window with 180 samples every 20s
        from_ms = 1700000000000
        to_ms = from_ms + 3600_000
        timestamps = [from_ms + i * 20_000 for i in range(180)]
        cov = compute_window_coverage(timestamps, from_ms, to_ms, resolution="raw")
        self.assertEqual(cov["requestedFrom"], from_ms)
        self.assertEqual(cov["requestedTo"], to_ms)
        self.assertEqual(cov["requestedDuration"], 3600_000)
        self.assertEqual(cov["availableFrom"], from_ms)
        self.assertEqual(cov["availableTo"], from_ms + 179 * 20_000)
        self.assertEqual(cov["availableDuration"], 179 * 20_000)
        self.assertEqual(cov["observedDuration"], 3600_000)
        self.assertEqual(cov["dataPoints"], 180)
        self.assertEqual(cov["expectedPoints"], 180)
        self.assertEqual(cov["windowTimeCoverageRatio"], 1.0)
        self.assertEqual(cov["observedTimeCoverageRatio"], 1.0)
        self.assertEqual(cov["sampleCoverageRatio"], 1.0)
        self.assertTrue(cov["complete"])
        self.assertFalse(cov["hasGaps"])
        self.assertEqual(cov["gapCount"], 0)
        self.assertFalse(cov["leadingGap"])
        self.assertFalse(cov["trailingGap"])

    def test_partial_window_288_samples_for_24h(self):
        from backend.analytics import compute_window_coverage
        # 24h window requested, but only 288 samples (96m) at the end of the window
        to_ms = 1700086400000
        from_ms = to_ms - 86400_000
        # Telemetry only available in last 96 minutes:
        avail_start = to_ms - 288 * 20_000
        timestamps = [avail_start + i * 20_000 for i in range(288)]
        cov = compute_window_coverage(timestamps, from_ms, to_ms, resolution="raw")
        self.assertEqual(cov["requestedDuration"], 86400_000)
        self.assertEqual(cov["availableDuration"], 287 * 20_000)
        self.assertEqual(cov["observedDuration"], 288 * 20_000)
        self.assertEqual(cov["dataPoints"], 288)
        self.assertEqual(cov["expectedPoints"], 4320)
        self.assertEqual(cov["sampleCoverageRatio"], round(288 / 4320, 4))
        self.assertEqual(cov["windowTimeCoverageRatio"], round((288 * 20_000) / 86400_000, 4))
        self.assertFalse(cov["complete"])
        self.assertTrue(cov["hasGaps"])
        self.assertTrue(cov["leadingGap"])
        self.assertFalse(cov["trailingGap"])

    def test_internal_gap_detection(self):
        from backend.analytics import compute_window_coverage
        from_ms = 1700000000000
        to_ms = from_ms + 3600_000
        # First 30 samples (600s), then 10 minute gap (600,000 ms), then 30 samples (600s)
        ts1 = [from_ms + i * 20_000 for i in range(30)]
        gap_start = from_ms + 29 * 20_000 + 600_000
        ts2 = [gap_start + i * 20_000 for i in range(30)]
        cov = compute_window_coverage(ts1 + ts2, from_ms, to_ms, resolution="raw")
        self.assertEqual(cov["gapCount"], 1)
        self.assertTrue(cov["hasGaps"])
        self.assertFalse(cov["complete"])
        self.assertEqual(cov["observedDuration"], 600_000 + 600_000)  # 20 minutes observed
        self.assertEqual(cov["windowTimeCoverageRatio"], round(1200_000 / 3600_000, 4))
        self.assertEqual(cov["availableDuration"], (gap_start + 29 * 20_000) - from_ms)

    def test_empty_window(self):
        from backend.analytics import compute_window_coverage
        from_ms = 1700000000000
        to_ms = from_ms + 3600_000
        cov = compute_window_coverage([], from_ms, to_ms, resolution="raw")
        self.assertIsNone(cov["availableFrom"])
        self.assertIsNone(cov["availableTo"])
        self.assertEqual(cov["availableDuration"], 0)
        self.assertEqual(cov["observedDuration"], 0)
        self.assertEqual(cov["coverageRatio"], 0.0)
        self.assertEqual(cov["windowTimeCoverageRatio"], 0.0)
        self.assertEqual(cov["sampleCoverageRatio"], 0.0)
        self.assertFalse(cov["complete"])
        self.assertFalse(cov["hasGaps"])

    def test_duplicate_and_out_of_order_timestamps(self):
        from backend.analytics import compute_window_coverage
        from_ms = 1700000000000
        to_ms = from_ms + 60_000
        # Scrambled order with duplicate timestamps
        timestamps = [from_ms + 40_000, from_ms, from_ms + 20_000, from_ms + 40_000, from_ms]
        cov = compute_window_coverage(timestamps, from_ms, to_ms, resolution="raw")
        self.assertEqual(cov["dataPoints"], 3)
        self.assertEqual(cov["availableFrom"], from_ms)
        self.assertEqual(cov["availableTo"], from_ms + 40_000)
        self.assertEqual(cov["observedDuration"], 60_000)
        self.assertEqual(cov["windowTimeCoverageRatio"], 1.0)

    def test_out_of_window_filtering(self):
        from backend.analytics import compute_window_coverage
        from_ms = 1700000000000
        to_ms = from_ms + 60_000
        # Timestamps before and after window
        timestamps = [from_ms - 100_000, from_ms, from_ms + 20_000, to_ms + 500_000]
        cov = compute_window_coverage(timestamps, from_ms, to_ms, resolution="raw")
        self.assertEqual(cov["dataPoints"], 2)
        self.assertEqual(cov["availableFrom"], from_ms)
        self.assertEqual(cov["availableTo"], from_ms + 20_000)
        self.assertEqual(cov["observedDuration"], 40_000)
        self.assertEqual(cov["windowTimeCoverageRatio"], round(40_000 / 60_000, 4))
    def test_capped_window_reporting(self):
        from backend.analytics import compute_window_coverage
        # 6-hour window where 1000 samples are returned but requested window needs 1080
        from_ms = 1700000000000
        to_ms = from_ms + 6 * 3600 * 1000
        # 1000 samples every 13.1s covering ~3.64 hours
        timestamps = [to_ms - (999 - i) * 13_130 for i in range(1000)]
        cov = compute_window_coverage(timestamps, from_ms, to_ms, resolution="raw", truncated=True)
        self.assertTrue(cov["truncated"], "truncated flag must be preserved")
        self.assertFalse(cov["complete"], "capped window cannot be marked complete")
        self.assertEqual(cov["dataPoints"], 1000)
        self.assertEqual(cov["expectedPoints"], 1080)
        self.assertAlmostEqual(cov["sampleCoverageRatio"], round(1000 / 1080, 4))

if __name__ == "__main__":
    unittest.main()
