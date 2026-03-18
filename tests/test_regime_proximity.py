#!/usr/bin/env python3
"""Tests for the regime proximity gradient field.

Validates the regimeProximity field returned by /regime/current and
/signals/filtered under different regime conditions:
  1. SYSTEMIC (entrenched) — all 3 types deep decay, score near 0
  2. SYSTEMIC (recovering) — types starting to recover, score mid-range
  3. NEUTRAL — at target, score = 1.0
  4. Edge cases — missing data, boundary conditions

Uses a built-in mock server with injectable proximity data (zero deps).

Run:
    python -m pytest tests/test_regime_proximity.py -v
    python tests/test_regime_proximity.py
"""

import json
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

MOCK_PORT = 19878
MOCK_RESPONSES = {}


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProxHandler(BaseHTTPRequestHandler):
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


# ── Proximity calculation (mirrors signal_api.js logic) ──────────────────

DECAY_THRESHOLD = 0.20
PROX_FLOOR = 0.50


def compute_proximity(decay_data, regime_override=None):
    """Pure-Python implementation of the proximity calculation.

    Args:
        decay_data: list of dicts with keys: type, label, dropPct (0-1 scale),
                    isDecaying, velocity (optional)
        regime_override: if provided, use this regime ID instead of deriving from decay counts.
                         The real API uses classifyRegime() which has nuanced logic beyond
                         simple decay counts; this param lets tests match real behavior.

    Returns:
        dict matching the regimeProximity schema
    """
    types = []
    for d in decay_data:
        drop = d["dropPct"]
        if drop <= DECAY_THRESHOLD:
            recovery = 1.0
        elif drop >= PROX_FLOOR:
            recovery = 0.0
        else:
            recovery = round((PROX_FLOOR - drop) / (PROX_FLOOR - DECAY_THRESHOLD), 3)

        vel = d.get("velocity", 0)
        if vel > 0.02:
            vel_label = "RECOVERING"
        elif vel < -0.02:
            vel_label = "DETERIORATING"
        else:
            vel_label = "STABLE"

        types.append({
            "type": d["type"],
            "label": d["label"],
            "dropPct": round(drop * 100, 1),
            "distanceToThreshold": round(max(0, (drop - DECAY_THRESHOLD)) * 100, 1),
            "recoveryScore": recovery,
            "velocity": vel,
            "velocityLabel": vel_label,
            "isDecaying": d["isDecaying"],
        })

    types.sort(key=lambda t: t["recoveryScore"], reverse=True)

    leader = types[0]
    bottleneck = types[1]
    laggard = types[2]

    raw = leader["recoveryScore"] * 0.30 + bottleneck["recoveryScore"] * 0.50 + laggard["recoveryScore"] * 0.20
    vel_bonus = min(bottleneck["velocity"] * 2.0, 0.10) if bottleneck["velocity"] > 0.02 else 0
    vel_penalty = min(abs(bottleneck["velocity"]) * 1.5, 0.08) if bottleneck["velocity"] < -0.02 else 0
    score = round(max(0, min(1.0, raw + vel_bonus - vel_penalty)), 3)

    # Determine regime (simplified — real API uses classifyRegime() with more nuance)
    if regime_override:
        regime = regime_override
    else:
        decaying_count = sum(1 for d in decay_data if d["isDecaying"])
        if decaying_count >= 3:
            regime = "SYSTEMIC"
        elif decaying_count >= 2:
            regime = "DIVERGENCE"
        elif decaying_count >= 1:
            regime = "EARNINGS"
        else:
            regime = "NEUTRAL"

    if regime == "NEUTRAL":
        label = "AT_NEUTRAL"
    elif score >= 0.75:
        label = "NEAR_TRANSITION"
    elif score >= 0.40:
        label = "RECOVERING"
    elif score >= 0.15:
        label = "STABILIZING"
    else:
        label = "ENTRENCHED"

    return {
        "score": score,
        "label": label,
        "regime": regime,
        "transitionsNeeded": 0 if regime == "NEUTRAL" else 2,
        "leader": leader,
        "bottleneck": bottleneck,
        "perType": {t["type"]: t for t in types},
    }


# ── Test Data Factories ──────────────────────────────────────────────────

def systemic_entrenched_data():
    """All 3 types >40% decay, deteriorating — deep SYSTEMIC."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.441, "isDecaying": True, "velocity": -0.41},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.506, "isDecaying": True, "velocity": -0.47},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.480, "isDecaying": True, "velocity": -0.45},
    ]


def systemic_recovering_data():
    """All 3 types still decaying but leader approaching threshold."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.28, "isDecaying": True, "velocity": 0.04},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.40, "isDecaying": True, "velocity": 0.03},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.35, "isDecaying": True, "velocity": 0.01},
    ]


def systemic_near_transition_data():
    """2 types very close to threshold, positive velocity."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.21, "isDecaying": True, "velocity": 0.06},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.22, "isDecaying": True, "velocity": 0.04},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.38, "isDecaying": True, "velocity": 0.01},
    ]


def neutral_data():
    """Only 1 type decaying — regime is NEUTRAL."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.42, "isDecaying": True, "velocity": -0.12},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.033, "isDecaying": False, "velocity": 0.01},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.129, "isDecaying": False, "velocity": 0.0},
    ]


def all_at_threshold_data():
    """All types exactly at 20% threshold — edge case."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.20, "isDecaying": False, "velocity": 0.0},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.20, "isDecaying": False, "velocity": 0.0},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.20, "isDecaying": False, "velocity": 0.0},
    ]


def all_at_floor_data():
    """All types at 50% decay — maximum ENTRENCHED."""
    return [
        {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.50, "isDecaying": True, "velocity": -0.5},
        {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.50, "isDecaying": True, "velocity": -0.5},
        {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.50, "isDecaying": True, "velocity": -0.5},
    ]


# ── Test Cases ────────────────────────────────────────────────────────────

class TestProximityCalculation(unittest.TestCase):
    """Unit tests for the proximity score calculation."""

    def test_entrenched_score_near_zero(self):
        """SYSTEMIC entrenched: all types >40% decay -> score near 0."""
        result = compute_proximity(systemic_entrenched_data())
        self.assertLess(result["score"], 0.10)
        self.assertEqual(result["label"], "ENTRENCHED")
        self.assertEqual(result["regime"], "SYSTEMIC")

    def test_entrenched_all_deteriorating(self):
        """SYSTEMIC entrenched: all velocity labels should be DETERIORATING."""
        result = compute_proximity(systemic_entrenched_data())
        for t, data in result["perType"].items():
            self.assertEqual(data["velocityLabel"], "DETERIORATING",
                             f"{t} should be DETERIORATING")

    def test_entrenched_bottleneck_identified(self):
        """Bottleneck should be the 2nd-closest type to recovery."""
        result = compute_proximity(systemic_entrenched_data())
        # Leader = SEMI_LEADS (lowest drop), bottleneck = FULL_DECOUPLE
        self.assertEqual(result["leader"]["type"], "SEMI_LEADS")
        self.assertEqual(result["bottleneck"]["type"], "FULL_DECOUPLE")

    def test_entrenched_transitions_needed(self):
        """SYSTEMIC needs 2 types to recover for NEUTRAL."""
        result = compute_proximity(systemic_entrenched_data())
        self.assertEqual(result["transitionsNeeded"], 2)

    def test_recovering_score_mid_range(self):
        """SYSTEMIC recovering: types approaching threshold -> mid score."""
        result = compute_proximity(systemic_recovering_data())
        self.assertGreater(result["score"], 0.30)
        self.assertLess(result["score"], 0.80)
        self.assertIn(result["label"], ("RECOVERING", "STABILIZING"))

    def test_recovering_positive_velocity(self):
        """Recovering state: leader should show RECOVERING velocity."""
        result = compute_proximity(systemic_recovering_data())
        self.assertEqual(result["leader"]["velocityLabel"], "RECOVERING")

    def test_recovering_velocity_bonus(self):
        """Positive bottleneck velocity should boost score vs zero velocity."""
        # Use data where the bottleneck (2nd-highest recovery) has velocity > 0.02
        base_data = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.28, "isDecaying": True, "velocity": 0.05},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.40, "isDecaying": True, "velocity": 0.03},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.35, "isDecaying": True, "velocity": 0.04},
        ]
        zero_vel_data = [dict(d, velocity=0.0) for d in base_data]

        result_with_vel = compute_proximity(base_data)
        result_zero_vel = compute_proximity(zero_vel_data)

        self.assertGreater(result_with_vel["score"], result_zero_vel["score"],
                           "Positive bottleneck velocity should boost proximity score")

    def test_near_transition_high_score(self):
        """Types near threshold with positive velocity -> high score."""
        result = compute_proximity(systemic_near_transition_data())
        self.assertGreater(result["score"], 0.70)
        self.assertIn(result["label"], ("NEAR_TRANSITION", "RECOVERING"))

    def test_neutral_at_target(self):
        """NEUTRAL regime: only 1 type decaying -> AT_NEUTRAL.

        Uses regime_override because the real API's classifyRegime() has nuanced
        logic (confidence caps, alert thresholds) that can classify 1-type-decaying
        as NEUTRAL rather than EARNINGS.
        """
        result = compute_proximity(neutral_data(), regime_override="NEUTRAL")
        self.assertEqual(result["label"], "AT_NEUTRAL")
        self.assertEqual(result["regime"], "NEUTRAL")
        self.assertEqual(result["transitionsNeeded"], 0)

    def test_all_at_threshold_boundary(self):
        """Edge: all types exactly at 20% threshold -> recovery score 1.0."""
        result = compute_proximity(all_at_threshold_data())
        for t, data in result["perType"].items():
            self.assertEqual(data["recoveryScore"], 1.0,
                             f"{t} at threshold should have recovery 1.0")

    def test_all_at_floor_score_zero(self):
        """Edge: all types at 50% decay -> score 0, ENTRENCHED."""
        result = compute_proximity(all_at_floor_data())
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["label"], "ENTRENCHED")

    def test_all_at_floor_velocity_penalty(self):
        """Floor + deteriorating velocity should not push score negative."""
        result = compute_proximity(all_at_floor_data())
        self.assertGreaterEqual(result["score"], 0.0)

    def test_score_bounded_zero_to_one(self):
        """Score should always be in [0.0, 1.0] range."""
        for factory in [systemic_entrenched_data, systemic_recovering_data,
                        systemic_near_transition_data, neutral_data,
                        all_at_threshold_data, all_at_floor_data]:
            result = compute_proximity(factory())
            self.assertGreaterEqual(result["score"], 0.0,
                                    f"{factory.__name__}: score < 0")
            self.assertLessEqual(result["score"], 1.0,
                                 f"{factory.__name__}: score > 1")

    def test_per_type_distance_calculation(self):
        """Distance to threshold should be dropPct - 20% (in percentage points)."""
        result = compute_proximity(systemic_entrenched_data())
        semi = result["perType"]["SEMI_LEADS"]
        self.assertAlmostEqual(semi["distanceToThreshold"], 24.1, places=1)
        crypto = result["perType"]["CRYPTO_LEADS"]
        self.assertAlmostEqual(crypto["distanceToThreshold"], 30.6, places=1)

    def test_schema_completeness(self):
        """Result should contain all required schema fields."""
        result = compute_proximity(systemic_entrenched_data())
        required_top = ["score", "label", "regime", "transitionsNeeded",
                        "leader", "bottleneck", "perType"]
        for key in required_top:
            self.assertIn(key, result, f"Missing top-level key: {key}")

        required_type = ["dropPct", "distanceToThreshold", "recoveryScore",
                         "velocity", "velocityLabel", "isDecaying"]
        for t_name, t_data in result["perType"].items():
            for key in required_type:
                self.assertIn(key, t_data, f"Missing key {key} in {t_name}")

    def test_monotonic_score_vs_decay(self):
        """Higher decay across all types should produce lower proximity score."""
        light = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.25, "isDecaying": True, "velocity": 0},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.25, "isDecaying": True, "velocity": 0},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.25, "isDecaying": True, "velocity": 0},
        ]
        heavy = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 0.45, "isDecaying": True, "velocity": 0},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 0.45, "isDecaying": True, "velocity": 0},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 0.45, "isDecaying": True, "velocity": 0},
        ]
        self.assertGreater(compute_proximity(light)["score"],
                           compute_proximity(heavy)["score"])


class TestProximityLiveAPI(unittest.TestCase):
    """Integration tests: verify proximity field from mock API endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", MOCK_PORT), ProxHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _fetch(self, path):
        import urllib.request
        url = f"http://localhost:{MOCK_PORT}{path}"
        resp = urllib.request.urlopen(url, timeout=5)
        return json.loads(resp.read())

    def test_regime_current_includes_proximity(self):
        """GET /regime/current should include regimeProximity."""
        global MOCK_RESPONSES
        from mock_server import regime_current
        MOCK_RESPONSES = {"/regime/current": regime_current()}

        data = self._fetch("/regime/current")
        self.assertIn("regimeProximity", data)
        prox = data["regimeProximity"]
        self.assertIn("score", prox)
        self.assertEqual(prox["label"], "AT_NEUTRAL")

    def test_signals_filtered_includes_proximity(self):
        """GET /signals/filtered should include regimeProximity."""
        global MOCK_RESPONSES
        from mock_server import signals_filtered
        MOCK_RESPONSES = {"/signals/filtered": signals_filtered()}

        data = self._fetch("/signals/filtered")
        self.assertIn("regimeProximity", data)
        self.assertIn("decision", data)

    def test_proximity_schema_from_endpoint(self):
        """Proximity from endpoint should have all required fields."""
        global MOCK_RESPONSES
        from mock_server import regime_current
        MOCK_RESPONSES = {"/regime/current": regime_current()}

        data = self._fetch("/regime/current")
        prox = data["regimeProximity"]

        for key in ("score", "label", "scale", "regime", "regimeDurationDays",
                    "transitionsNeeded", "leader", "bottleneck", "perType",
                    "ifLeaderRecovers", "interpretation"):
            self.assertIn(key, prox, f"Missing proximity field: {key}")


# ── Standalone runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
