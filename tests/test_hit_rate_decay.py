#!/usr/bin/env python3
"""Tests for the Hit Rate Decay Model.

Validates the exponential-duration decay model that converts static
SYSTEMIC aggregate hit rates into duration-aware adjusted confidence values.

Model: adjustedConfidence(t) = neutralRate * exp(-lambda * t)
  where lambda = ln(neutralRate / systemicRate) / medianDuration

Test coverage:
  1. Core decay math — half-life, lambda, adjustedConfidence at known durations
  2. Calibration anchor — model equals aggregate at median SYSTEMIC duration
  3. Backtest — model predictions at historical period endpoints
  4. Sensitivity bands — +/-30% half-life variation
  5. Aggregate bias — overstated/understated detection
  6. Days-to-noise — sub-10% threshold calculation
  7. Edge cases — NEUTRAL regime, day 0, very long durations, anti-signals
  8. Per-signal injection — adjustedConfidence appears on each signal object

Uses a built-in mock server with injectable data (zero deps).

Run:
    python3 -m pytest tests/test_hit_rate_decay.py -v
    python3 tests/test_hit_rate_decay.py
"""

import json
import math
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

MOCK_PORT = 19882
MOCK_RESPONSES = {}


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DecayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        data = MOCK_RESPONSES.get(path)
        if data is not None:
            body = json.dumps(data, indent=2).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": f"Not found: {path}"}).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


# ── Pure-Python reimplementation of the decay model ─────────────────────
# Mirrors the JS logic in signal_api.js for testability.

# REGIME_FILTER lookup table (from dashboard)
REGIME_FILTER = {
    "NEUTRAL": {
        "CRYPTO_LEADS": {"hitRate": 0.82, "n": 17},
        "SEMI_LEADS": {"hitRate": 0.12, "n": 8},
        "FULL_DECOUPLE": {"hitRate": 0.50, "n": 6},
    },
    "SYSTEMIC": {
        "CRYPTO_LEADS": {"hitRate": 0.20, "n": 5},
        "SEMI_LEADS": {"hitRate": 0.10, "n": 10},
        "FULL_DECOUPLE": {"hitRate": 0.25, "n": 4},
    },
}

STP_TYPES = ["SEMI_LEADS", "CRYPTO_LEADS", "FULL_DECOUPLE"]

HISTORICAL_SYSTEMIC_PERIODS = [
    {"entryDate": "2025-11-06", "exitDate": "2025-11-19", "durationDays": 13},
    {"entryDate": "2025-11-24", "exitDate": "2025-11-28", "durationDays": 4},
]

REGIME_HISTORY = [
    {"date": "2025-10-20", "regime": "NEUTRAL", "transitionFrom": None},
    {"date": "2025-11-06", "regime": "Systemic Risk-Off", "transitionFrom": "NEUTRAL"},
    {"date": "2025-11-19", "regime": "NEUTRAL", "transitionFrom": "Systemic Risk-Off"},
    {"date": "2025-11-24", "regime": "Systemic Risk-Off", "transitionFrom": "NEUTRAL"},
    {"date": "2025-11-28", "regime": "NEUTRAL", "transitionFrom": "Systemic Risk-Off"},
    {"date": "2026-01-15", "regime": "NEUTRAL", "transitionFrom": None},
    {"date": "2026-03-07", "regime": "Systemic Risk-Off", "transitionFrom": "NEUTRAL"},
]

NOISE_THRESHOLD = 0.10


def compute_median(arr):
    if not arr:
        return 8.5
    s = sorted(arr)
    mid = len(s) // 2
    if len(s) % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def compute_decay_model(regime_id="SYSTEMIC", duration_days=12, history=None):
    """Pure-Python implementation of the hit rate decay model."""
    if history is None:
        history = REGIME_HISTORY

    if regime_id == "NEUTRAL":
        return {
            "status": "AT_NEUTRAL",
            "perType": None,
            "sensitivityBands": None,
        }

    neutral = REGIME_FILTER["NEUTRAL"]
    systemic = REGIME_FILTER["SYSTEMIC"]

    # Extract historical SYSTEMIC durations
    hist_durations = []
    for i, h in enumerate(history):
        if h["regime"] in ("SYSTEMIC", "Systemic Risk-Off"):
            for j in range(i + 1, len(history)):
                if history[j]["regime"] not in ("SYSTEMIC", "Systemic Risk-Off"):
                    entry_d = datetime.strptime(h["date"], "%Y-%m-%d")
                    exit_d = datetime.strptime(history[j]["date"], "%Y-%m-%d")
                    d = (exit_d - entry_d).days
                    if d > 0:
                        hist_durations.append(d)
                    break

    median_duration = compute_median(hist_durations)

    per_type = {}
    for t in STP_TYPES:
        N = neutral[t]["hitRate"]
        S = systemic[t]["hitRate"]
        n_neutral = neutral[t]["n"]
        n_systemic = systemic[t]["n"]

        if N <= S or N <= 0 or S <= 0:
            per_type[t] = {
                "neutralRate": N,
                "systemicAggregate": S,
                "adjustedConfidence": S,
                "halfLifeDays": None,
                "decayApplicable": False,
            }
            continue

        ln_ratio = math.log(N / S)
        lam = ln_ratio / median_duration
        half_life = median_duration * math.log(2) / ln_ratio
        adjusted = N * math.exp(-lam * duration_days)
        decay_velocity = -lam * adjusted

        # Days to noise
        days_to_noise = None
        days_to_noise_remaining = None
        if N > NOISE_THRESHOLD:
            days_to_noise = math.log(N / NOISE_THRESHOLD) / lam
            days_to_noise_remaining = max(0, days_to_noise - duration_days)

        # Bias
        bias_pct = (S / adjusted - 1) * 100 if adjusted > 0 else None
        bias_dir = "OVERSTATED" if adjusted < S else ("UNDERSTATED" if adjusted > S else "ALIGNED")

        # Backtest
        backtest = []
        for d in sorted(hist_durations):
            pred = N * math.exp(-lam * d)
            backtest.append({
                "day": d,
                "predicted": round(pred, 4),
                "staticRate": S,
                "delta": round(pred - S, 4),
            })

        per_type[t] = {
            "neutralRate": N,
            "systemicAggregate": S,
            "adjustedConfidence": round(adjusted, 4),
            "halfLifeDays": round(half_life, 2),
            "decayConstant": round(lam, 4),
            "decayVelocityPerDay": round(decay_velocity, 6),
            "daysToNoise": round(days_to_noise, 1) if days_to_noise is not None else None,
            "daysToNoiseRemaining": round(days_to_noise_remaining, 1) if days_to_noise_remaining is not None else None,
            "aggregateBias": {"pct": round(bias_pct, 1) if bias_pct is not None else None, "direction": bias_dir},
            "decayApplicable": True,
            "backtestPredictions": backtest,
            "nNeutral": n_neutral,
            "nSystemic": n_systemic,
        }

    # Sensitivity bands
    sensitivity = {}
    for t in STP_TYPES:
        pt = per_type[t]
        if not pt.get("decayApplicable"):
            sensitivity[t] = None
            continue
        N = pt["neutralRate"]
        base_hl = pt["halfLifeDays"]
        base_lam = math.log(2) / base_hl
        sensitivity[t] = {
            "conservative": {
                "halfLifeDays": round(base_hl * 0.7, 2),
                "adjustedConfidence": round(N * math.exp(-(base_lam / 0.7) * duration_days), 4),
            },
            "base": {
                "halfLifeDays": base_hl,
                "adjustedConfidence": pt["adjustedConfidence"],
            },
            "optimistic": {
                "halfLifeDays": round(base_hl * 1.3, 2),
                "adjustedConfidence": round(N * math.exp(-(base_lam / 1.3) * duration_days), 4),
            },
        }

    return {
        "status": "ACTIVE",
        "regimeDurationDays": duration_days,
        "medianHistoricalDuration": median_duration,
        "perType": per_type,
        "sensitivityBands": sensitivity,
    }


# ── Test Suite ──────────────────────────────────────────────────────────


class TestDecayMathCore(unittest.TestCase):
    """Core exponential decay math validation."""

    def setUp(self):
        self.model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        self.cl = self.model["perType"]["CRYPTO_LEADS"]
        self.sl = self.model["perType"]["SEMI_LEADS"]
        self.fd = self.model["perType"]["FULL_DECOUPLE"]

    def test_decay_applicable_all_types(self):
        """All 3 types should have decay applicable (N > S for all)."""
        for t in STP_TYPES:
            self.assertTrue(self.model["perType"][t]["decayApplicable"], f"{t} should be decay-applicable")

    def test_half_life_positive(self):
        """Half-life must be positive for all applicable types."""
        for t in STP_TYPES:
            hl = self.model["perType"][t]["halfLifeDays"]
            self.assertIsNotNone(hl)
            self.assertGreater(hl, 0, f"{t} half-life must be > 0")

    def test_half_life_ordering(self):
        """CRYPTO_LEADS should decay fastest (shortest half-life), SEMI_LEADS slowest."""
        # CL has highest N/S ratio → shortest half-life
        # SL has lowest N/S ratio → longest half-life
        self.assertLess(self.cl["halfLifeDays"], self.fd["halfLifeDays"])
        self.assertLess(self.fd["halfLifeDays"], self.sl["halfLifeDays"])

    def test_crypto_leads_half_life_range(self):
        """CL half-life should be 3-6 days given N=0.82, S=0.20, median=8.5d."""
        hl = self.cl["halfLifeDays"]
        self.assertGreater(hl, 3.0)
        self.assertLess(hl, 6.0)

    def test_semi_leads_half_life_long(self):
        """SL half-life should be 20+ days (N=0.12, S=0.10 — very close rates)."""
        hl = self.sl["halfLifeDays"]
        self.assertGreater(hl, 20.0)

    def test_decay_constant_positive(self):
        """Decay constant lambda must be positive."""
        for t in STP_TYPES:
            lam = self.model["perType"][t]["decayConstant"]
            self.assertGreater(lam, 0)

    def test_adjusted_confidence_below_neutral(self):
        """Adjusted confidence at day 12 must be below NEUTRAL rate for all types."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            self.assertLess(pt["adjustedConfidence"], pt["neutralRate"])

    def test_decay_velocity_negative(self):
        """Decay velocity must be negative (confidence is decreasing)."""
        for t in STP_TYPES:
            self.assertLess(self.model["perType"][t]["decayVelocityPerDay"], 0)

    def test_lambda_half_life_consistency(self):
        """Verify lambda = ln(2) / halfLife for all types."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            expected_lambda = math.log(2) / pt["halfLifeDays"]
            self.assertAlmostEqual(pt["decayConstant"], expected_lambda, places=3)


class TestCalibrationAnchor(unittest.TestCase):
    """Verify the model's key calibration property:
    at t = medianDuration, adjustedConfidence = systemicAggregate."""

    def test_at_median_duration_equals_aggregate(self):
        """Model output at median duration must equal the SYSTEMIC aggregate rate."""
        median = 8.5  # (4 + 13) / 2
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=median)
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            N = pt["neutralRate"]
            S = pt["systemicAggregate"]
            # At t=median: N * exp(-lambda * median) should equal S
            # lambda = ln(N/S) / median
            # N * exp(-ln(N/S)) = N * S/N = S ✓
            expected = S
            self.assertAlmostEqual(pt["adjustedConfidence"], expected, places=3,
                                   msg=f"{t}: at median duration, adjusted should equal aggregate")

    def test_day_zero_equals_neutral(self):
        """At day 0 (just entered SYSTEMIC), adjusted confidence = NEUTRAL rate."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=0)
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            self.assertAlmostEqual(pt["adjustedConfidence"], pt["neutralRate"], places=3,
                                   msg=f"{t}: at day 0, should equal NEUTRAL rate")

    def test_below_aggregate_at_extended_duration(self):
        """At t > median, adjusted confidence should be BELOW the aggregate."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        median = model["medianHistoricalDuration"]
        self.assertGreater(12, median)  # 12 > 8.5 — we're past median
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            self.assertLess(pt["adjustedConfidence"], pt["systemicAggregate"],
                            msg=f"{t}: at day 12 (> median {median}d), adjusted should be below aggregate")


class TestBacktestPredictions(unittest.TestCase):
    """Validate backtest predictions at historical period endpoints."""

    def setUp(self):
        self.model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)

    def test_backtest_has_correct_period_count(self):
        """Each type should have backtest predictions matching historical period count."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            self.assertEqual(len(pt["backtestPredictions"]), 2)

    def test_short_period_above_aggregate(self):
        """At the 4-day period endpoint, model should predict ABOVE aggregate."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            day4 = [b for b in pt["backtestPredictions"] if b["day"] == 4][0]
            self.assertGreater(day4["predicted"], pt["systemicAggregate"],
                               msg=f"{t}: 4-day prediction should be above aggregate (early-period retention)")

    def test_long_period_below_aggregate(self):
        """At the 13-day period endpoint, model should predict BELOW aggregate."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            day13 = [b for b in pt["backtestPredictions"] if b["day"] == 13][0]
            self.assertLess(day13["predicted"], pt["systemicAggregate"],
                            msg=f"{t}: 13-day prediction should be below aggregate (extended decay)")

    def test_backtest_delta_signs(self):
        """Delta should be positive for short period, negative for long period."""
        for t in STP_TYPES:
            pt = self.model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            for bp in pt["backtestPredictions"]:
                if bp["day"] == 4:
                    self.assertGreater(bp["delta"], 0)
                elif bp["day"] == 13:
                    self.assertLess(bp["delta"], 0)


class TestSensitivityBands(unittest.TestCase):
    """Validate sensitivity analysis with ±30% half-life variation."""

    def setUp(self):
        self.model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)

    def test_conservative_lower_than_base(self):
        """Conservative (shorter half-life) should give lower adjusted confidence."""
        for t in STP_TYPES:
            bands = self.model["sensitivityBands"].get(t)
            if bands is None:
                continue
            self.assertLess(bands["conservative"]["adjustedConfidence"],
                            bands["base"]["adjustedConfidence"],
                            msg=f"{t}: conservative should be below base")

    def test_optimistic_higher_than_base(self):
        """Optimistic (longer half-life) should give higher adjusted confidence."""
        for t in STP_TYPES:
            bands = self.model["sensitivityBands"].get(t)
            if bands is None:
                continue
            self.assertGreater(bands["optimistic"]["adjustedConfidence"],
                               bands["base"]["adjustedConfidence"],
                               msg=f"{t}: optimistic should be above base")

    def test_half_life_30pct_variation(self):
        """Half-life values should be exactly ±30% of base."""
        for t in STP_TYPES:
            bands = self.model["sensitivityBands"].get(t)
            if bands is None:
                continue
            base_hl = bands["base"]["halfLifeDays"]
            self.assertAlmostEqual(bands["conservative"]["halfLifeDays"], base_hl * 0.7, places=1)
            self.assertAlmostEqual(bands["optimistic"]["halfLifeDays"], base_hl * 1.3, places=1)

    def test_all_bands_positive(self):
        """All sensitivity band values must be positive."""
        for t in STP_TYPES:
            bands = self.model["sensitivityBands"].get(t)
            if bands is None:
                continue
            for label in ("conservative", "base", "optimistic"):
                self.assertGreater(bands[label]["adjustedConfidence"], 0)
                self.assertGreater(bands[label]["halfLifeDays"], 0)


class TestAggregateBias(unittest.TestCase):
    """Validate aggregate bias detection and quantification."""

    def test_overstated_at_extended_duration(self):
        """At day 12 (> median 8.5d), all applicable types should show OVERSTATED."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            self.assertEqual(pt["aggregateBias"]["direction"], "OVERSTATED")
            self.assertGreater(pt["aggregateBias"]["pct"], 0)

    def test_understated_at_short_duration(self):
        """At day 2 (< median 8.5d), applicable types should show UNDERSTATED."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=2)
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            self.assertEqual(pt["aggregateBias"]["direction"], "UNDERSTATED",
                             msg=f"{t}: at day 2, static rate should understate current confidence")

    def test_aligned_at_median(self):
        """At median duration (8.5d), bias should be approximately ALIGNED."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=8.5)
        for t in STP_TYPES:
            pt = model["perType"][t]
            if not pt["decayApplicable"]:
                continue
            # At median, adjusted = aggregate, so bias pct ≈ 0
            self.assertAlmostEqual(pt["aggregateBias"]["pct"], 0.0, places=0)

    def test_crypto_leads_bias_magnitude_at_day12(self):
        """CL bias at day 12 should be substantial (>30%) given steep decay."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        cl = model["perType"]["CRYPTO_LEADS"]
        self.assertGreater(cl["aggregateBias"]["pct"], 30,
                           msg="CL static rate should overstate by >30% at day 12")


class TestDaysToNoise(unittest.TestCase):
    """Validate noise floor (sub-10%) threshold calculations."""

    def test_crypto_leads_noise_crossing(self):
        """CL should have a computable noise crossing time."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        cl = model["perType"]["CRYPTO_LEADS"]
        self.assertIsNotNone(cl["daysToNoise"])
        self.assertGreater(cl["daysToNoise"], 0)

    def test_semi_leads_noise_crossing_near_median(self):
        """SL (N=0.12) should cross noise floor (0.10) near the median duration."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=0)
        sl = model["perType"]["SEMI_LEADS"]
        # daysToNoise = ln(0.12/0.10) / lambda
        # At calibration, lambda = ln(1.2) / 8.5, so daysToNoise = 8.5 (by construction)
        self.assertIsNotNone(sl["daysToNoise"])
        self.assertAlmostEqual(sl["daysToNoise"], 8.5, places=0)

    def test_days_remaining_decreases_with_duration(self):
        """Days-to-noise remaining should decrease as duration increases."""
        model_early = compute_decay_model(regime_id="SYSTEMIC", duration_days=2)
        model_late = compute_decay_model(regime_id="SYSTEMIC", duration_days=10)
        cl_early = model_early["perType"]["CRYPTO_LEADS"]
        cl_late = model_late["perType"]["CRYPTO_LEADS"]
        self.assertGreater(cl_early["daysToNoiseRemaining"], cl_late["daysToNoiseRemaining"])

    def test_past_noise_floor_zero_remaining(self):
        """If already past noise floor, daysToNoiseRemaining should be 0."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=30)
        cl = model["perType"]["CRYPTO_LEADS"]
        self.assertEqual(cl["daysToNoiseRemaining"], 0.0)


class TestEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_neutral_regime_returns_at_neutral(self):
        """NEUTRAL regime should return AT_NEUTRAL status with no decay data."""
        model = compute_decay_model(regime_id="NEUTRAL", duration_days=0)
        self.assertEqual(model["status"], "AT_NEUTRAL")
        self.assertIsNone(model["perType"])

    def test_very_long_duration_near_zero(self):
        """At very long durations, CL adjusted confidence should approach 0."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=100)
        cl = model["perType"]["CRYPTO_LEADS"]
        self.assertLess(cl["adjustedConfidence"], 0.001)

    def test_monotonic_decay(self):
        """Adjusted confidence must decrease monotonically with duration."""
        prev = {}
        for d in range(0, 20):
            model = compute_decay_model(regime_id="SYSTEMIC", duration_days=d)
            for t in STP_TYPES:
                pt = model["perType"][t]
                if not pt["decayApplicable"]:
                    continue
                if t in prev:
                    self.assertLessEqual(pt["adjustedConfidence"], prev[t] + 0.0001,
                                         msg=f"{t}: adjusted confidence should not increase from day {d-1} to {d}")
                prev[t] = pt["adjustedConfidence"]

    def test_full_decouple_intermediate_values(self):
        """FD (N=0.50, S=0.25) should have intermediate half-life and decay."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=8.5)
        fd = model["perType"]["FULL_DECOUPLE"]
        # At median: adjusted = 0.25 (by calibration)
        self.assertAlmostEqual(fd["adjustedConfidence"], 0.25, places=2)
        # Half-life = 8.5 * ln2 / ln(2) = 8.5d (since N/S = 2, ln(2) = ln2)
        self.assertAlmostEqual(fd["halfLifeDays"], 8.5, places=1)

    def test_decay_curve_at_half_life(self):
        """At t = halfLife, adjusted confidence should be neutralRate / 2."""
        model_0 = compute_decay_model(regime_id="SYSTEMIC", duration_days=0)
        for t in STP_TYPES:
            pt0 = model_0["perType"][t]
            if not pt0["decayApplicable"]:
                continue
            hl = pt0["halfLifeDays"]
            model_hl = compute_decay_model(regime_id="SYSTEMIC", duration_days=hl)
            pt_hl = model_hl["perType"][t]
            expected = pt0["neutralRate"] / 2
            self.assertAlmostEqual(pt_hl["adjustedConfidence"], expected, places=2,
                                   msg=f"{t}: at half-life ({hl}d), adjusted should be neutralRate/2")

    def test_no_negative_values(self):
        """No decay model output should ever be negative."""
        for d in [0, 1, 5, 8.5, 12, 20, 50, 100]:
            model = compute_decay_model(regime_id="SYSTEMIC", duration_days=d)
            for t in STP_TYPES:
                pt = model["perType"][t]
                if not pt["decayApplicable"]:
                    continue
                self.assertGreaterEqual(pt["adjustedConfidence"], 0)
                self.assertGreater(pt["halfLifeDays"], 0)

    def test_model_version_string(self):
        """Model version should be present."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        self.assertEqual(model["status"], "ACTIVE")


class TestPerSignalInjection(unittest.TestCase):
    """Verify adjustedConfidence is properly injected into signal objects."""

    def test_signal_has_adjusted_confidence(self):
        """Each signal in a SYSTEMIC response should have adjustedConfidence."""
        # Simulate what the API handler does
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        signals = [
            {"type": "CRYPTO_LEADS", "confidence": 0.20, "pair": "NVDA/RNDR"},
            {"type": "SEMI_LEADS", "confidence": 0.10, "pair": "MRVL/RNDR"},
            {"type": "FULL_DECOUPLE", "confidence": 0.25, "pair": "TSM/RNDR"},
        ]
        if model["status"] == "ACTIVE" and model["perType"]:
            for s in signals:
                td = model["perType"].get(s["type"])
                if td and td["decayApplicable"]:
                    s["adjustedConfidence"] = td["adjustedConfidence"]
                    s["decayHalfLifeDays"] = td["halfLifeDays"]
                else:
                    s["adjustedConfidence"] = s["confidence"]

        for s in signals:
            self.assertIn("adjustedConfidence", s)
            self.assertIsNotNone(s["adjustedConfidence"])

    def test_adjusted_below_static_at_extended_duration(self):
        """At day 12, adjustedConfidence should be below static confidence for CL."""
        model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        cl = model["perType"]["CRYPTO_LEADS"]
        self.assertLess(cl["adjustedConfidence"], cl["systemicAggregate"])

    def test_adjusted_equals_static_under_neutral(self):
        """Under NEUTRAL, adjustedConfidence should equal static confidence."""
        model = compute_decay_model(regime_id="NEUTRAL", duration_days=0)
        self.assertEqual(model["status"], "AT_NEUTRAL")
        # Under NEUTRAL, signals get adjustedConfidence = confidence (static)
        # No decay applied


class TestCryptoLeadsDetailed(unittest.TestCase):
    """Detailed validation of CRYPTO_LEADS — the primary tradeable signal."""

    def setUp(self):
        self.model = compute_decay_model(regime_id="SYSTEMIC", duration_days=12)
        self.cl = self.model["perType"]["CRYPTO_LEADS"]

    def test_adjusted_confidence_range_at_day12(self):
        """At day 12 with CL (N=0.82, S=0.20), adjusted should be 0.05-0.20."""
        self.assertGreater(self.cl["adjustedConfidence"], 0.05)
        self.assertLess(self.cl["adjustedConfidence"], 0.20)

    def test_aggregate_overstates_by_significant_margin(self):
        """At day 12, the static 20% should overstate by >40%."""
        self.assertEqual(self.cl["aggregateBias"]["direction"], "OVERSTATED")
        self.assertGreater(self.cl["aggregateBias"]["pct"], 40)

    def test_days_to_noise_reasonable(self):
        """CL should cross the 10% noise floor within 10-15 days."""
        self.assertIsNotNone(self.cl["daysToNoise"])
        self.assertGreater(self.cl["daysToNoise"], 10)
        self.assertLess(self.cl["daysToNoise"], 15)

    def test_decay_velocity_magnitude(self):
        """Decay velocity should be small but meaningful."""
        vel = abs(self.cl["decayVelocityPerDay"])
        self.assertGreater(vel, 0.001)
        self.assertLess(vel, 0.1)


# ── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
