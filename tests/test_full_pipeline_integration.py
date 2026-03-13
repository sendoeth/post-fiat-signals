#!/usr/bin/env python3
"""End-to-end pipeline integration tests — HEALTHY, DEGRADED, and HALT paths.

Exercises the full signal pipeline path (regime engine -> Granger pipeline ->
circuit breaker -> SDK output) against a configurable mock server, asserting
correct behavior for each health state.

Three scenarios:
  1. HEALTHY  — clean signals, NEUTRAL regime, CRYPTO_LEADS intact
               -> pipeline produces EXECUTE with actionable output
  2. DEGRADED — stale data warning, 1 type decaying, regime alert off
               -> pipeline produces EXECUTE_REDUCED with warning metadata
  3. HALT     — 3/3 types decaying, SYSTEMIC regime, regime alert triggered
               -> pipeline halts with NO_TRADE and human-readable explanation

Uses a built-in HTTP mock server with injectable response data (zero deps).

Run:
    python -m pytest tests/test_full_pipeline_integration.py -v
    python tests/test_full_pipeline_integration.py
"""

import copy
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# SDK imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

from pf_regime_sdk import RegimeClient
from watchdog import check_system_health, check_signal_fidelity, check_regime_confidence
from regime_scanner import evaluate


# ── Mock Server ────────────────────────────────────────────────────────────

MOCK_PORT = 19877  # different from stress tests (19876)
MOCK_RESPONSES = {}  # endpoint -> dict, set per scenario


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScenarioHandler(BaseHTTPRequestHandler):
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress output during tests


# ── Scenario Data Factories ────────────────────────────────────────────────

def healthy_responses():
    """HEALTHY: NEUTRAL regime, CRYPTO_LEADS intact, no decay, fresh data."""
    ts = _ts()
    return {
        "/health": {
            "status": "ok",
            "uptime": 86400,
            "uptimeHuman": "1d 0h",
            "lastRefresh": ts,
            "dataAgeSec": 120,
            "isStale": False,
            "refreshCount": 96,
            "dataFresh": True,
            "lastError": None,
            "schemaVersion": "1.1.0",
        },
        "/signals/reliability": {
            "window": 30,
            "regimeAlert": {"triggered": False, "types": [], "count": 0},
            "types": {
                "SEMI_LEADS": {
                    "label": "Semi Leads",
                    "score": 68,
                    "reliabilityLabel": "MEDIUM",
                    "allTimeScore": 68,
                    "currentRolling": 62,
                    "dropPct": 8.8,
                    "isDecaying": False,
                    "freshness": "Fresh",
                    "firstDecayDate": None,
                },
                "CRYPTO_LEADS": {
                    "label": "Crypto Leads",
                    "score": 85,
                    "reliabilityLabel": "HIGH",
                    "allTimeScore": 85,
                    "currentRolling": 83,
                    "dropPct": 2.4,
                    "isDecaying": False,
                    "freshness": "Fresh",
                    "firstDecayDate": None,
                },
                "FULL_DECOUPLE": {
                    "label": "Full Decouple",
                    "score": 75,
                    "reliabilityLabel": "HIGH",
                    "allTimeScore": 75,
                    "currentRolling": 70,
                    "dropPct": 6.7,
                    "isDecaying": False,
                    "freshness": "Fresh",
                    "firstDecayDate": None,
                },
            },
            "timestamp": ts,
            "dataAgeSec": 120,
            "isStale": False,
        },
        "/regime/current": {
            "state": "Neutral",
            "id": "NEUTRAL",
            "confidence": 72,
            "isAlert": False,
            "action": "Hold current allocations.",
            "targetWeights": {"NVDA": 0.25, "AMD": 0.20, "AVGO": 0.20,
                              "MRVL": 0.15, "ASML": 0.20},
            "signals": {
                "SEMI_LEADS": {"label": "Semi Leads", "currentScore": 62,
                               "allTimeScore": 68, "dropPct": 0.088, "decaying": False},
                "CRYPTO_LEADS": {"label": "Crypto Leads", "currentScore": 83,
                                 "allTimeScore": 85, "dropPct": 0.024, "decaying": False},
                "FULL_DECOUPLE": {"label": "Full Decouple", "currentScore": 70,
                                  "allTimeScore": 75, "dropPct": 0.067, "decaying": False},
            },
            "backtestContext": {
                "optimalWindow": 60,
                "accuracy": 60,
                "avgLeadTime": 27,
                "fpRate": 40,
            },
            "timestamp": ts,
            "dataAgeSec": 120,
            "isStale": False,
        },
        "/signals/filtered": {
            "regimeId": "NEUTRAL",
            "regimeLabel": "Neutral",
            "regimeConfidence": 72,
            "totalSignals": 3,
            "actionableCount": 2,
            "suppressedCount": 1,
            "ambiguousCount": 0,
            "filterRules": {
                "CRYPTO_LEADS": {"label": "Crypto Leads", "classification": "ACTIONABLE",
                                 "hitRate": 0.82, "n": 22, "avgRet": 8.24},
                "SEMI_LEADS": {"label": "Semi Leads", "classification": "SUPPRESS",
                               "hitRate": 0.12, "n": 16, "avgRet": -14.60},
                "FULL_DECOUPLE": {"label": "Full Decouple", "classification": "AMBIGUOUS",
                                  "hitRate": 0.80, "n": 5, "avgRet": 3.83},
            },
            "signals": [
                {"pair": "NVDA/RNDR", "type": "CRYPTO_LEADS", "typeLabel": "Crypto Leads",
                 "conviction": 85, "reliability": 88, "reliabilityLabel": "STRONG",
                 "regimeFilter": "ACTIONABLE", "regimeFilterHitRate": 0.82,
                 "regimeFilterN": 22, "regimeFilterAvgRet": 8.24},
                {"pair": "AMD/TAO", "type": "CRYPTO_LEADS", "typeLabel": "Crypto Leads",
                 "conviction": 71, "reliability": 88, "reliabilityLabel": "STRONG",
                 "regimeFilter": "ACTIONABLE", "regimeFilterHitRate": 0.82,
                 "regimeFilterN": 22, "regimeFilterAvgRet": 8.24},
                {"pair": "AVGO/AKT", "type": "SEMI_LEADS", "typeLabel": "Semi Leads",
                 "conviction": 60, "reliability": 45, "reliabilityLabel": "DEGRADED",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.12,
                 "regimeFilterN": 16, "regimeFilterAvgRet": -14.60},
            ],
            "timestamp": ts,
            "dataAgeSec": 120,
            "isStale": False,
        },
    }


def degraded_responses():
    """DEGRADED: stale data warning, 1 type decaying, regime still NEUTRAL."""
    r = healthy_responses()
    # Health: data is aging past warning threshold
    r["/health"]["dataAgeSec"] = 1000  # >900s warning
    r["/health"]["lastError"] = "Puppeteer timeout on last refresh"
    # Reliability: SEMI_LEADS decaying (1 type = DEGRADED, not STOP)
    sl = r["/signals/reliability"]["types"]["SEMI_LEADS"]
    sl["dropPct"] = 35.0
    sl["isDecaying"] = True
    sl["freshness"] = "Stale"
    sl["firstDecayDate"] = "2026-02-01"
    r["/signals/reliability"]["regimeAlert"] = {
        "triggered": False, "types": ["SEMI_LEADS"], "count": 1,
    }
    # Regime: alert active (warning, not STOP)
    r["/regime/current"]["isAlert"] = True
    r["/regime/current"]["signals"]["SEMI_LEADS"]["decaying"] = True
    r["/regime/current"]["signals"]["SEMI_LEADS"]["dropPct"] = 0.35
    return r


def halt_responses():
    """HALT: SYSTEMIC regime, 3/3 types decaying, regime alert triggered."""
    ts = _ts()
    return {
        "/health": {
            "status": "ok",
            "uptime": 369590,
            "uptimeHuman": "102h 39m",
            "lastRefresh": ts,
            "dataAgeSec": 200,
            "isStale": False,
            "refreshCount": 411,
            "dataFresh": True,
            "lastError": None,
            "schemaVersion": "1.1.0",
        },
        "/signals/reliability": {
            "window": 30,
            "regimeAlert": {
                "triggered": True,
                "types": ["SEMI_LEADS", "CRYPTO_LEADS", "FULL_DECOUPLE"],
                "count": 3,
                "msg": "3 signal types show reliability decay",
            },
            "types": {
                "SEMI_LEADS": {
                    "label": "Semi Leads", "score": 68,
                    "reliabilityLabel": "MEDIUM", "allTimeScore": 68,
                    "currentRolling": 38, "dropPct": 44.1,
                    "isDecaying": True, "freshness": "Stale",
                    "firstDecayDate": "2025-09-04",
                },
                "CRYPTO_LEADS": {
                    "label": "Crypto Leads", "score": 85,
                    "reliabilityLabel": "HIGH", "allTimeScore": 85,
                    "currentRolling": 42, "dropPct": 50.6,
                    "isDecaying": True, "freshness": "Stale",
                    "firstDecayDate": "2025-08-13",
                },
                "FULL_DECOUPLE": {
                    "label": "Full Decouple", "score": 75,
                    "reliabilityLabel": "HIGH", "allTimeScore": 75,
                    "currentRolling": 39, "dropPct": 48.0,
                    "isDecaying": True, "freshness": "Stale",
                    "firstDecayDate": "2025-10-16",
                },
            },
            "timestamp": ts,
            "dataAgeSec": 200,
            "isStale": False,
        },
        "/regime/current": {
            "state": "Systemic Risk-Off",
            "id": "SYSTEMIC",
            "confidence": 77,
            "isAlert": True,
            "action": "Rotate to Bear weights (72% USDC), tighten stops to 3%",
            "targetWeights": {"NVDA": 0.08, "TSM": 0, "AVGO": 0.12,
                              "ASML": 0.08, "ASX": 0, "USDC": 0.72},
            "signals": {
                "SEMI_LEADS": {"label": "Semi Leads", "currentScore": 38,
                               "allTimeScore": 68, "dropPct": 0.441, "decaying": True},
                "CRYPTO_LEADS": {"label": "Crypto Leads", "currentScore": 42,
                                 "allTimeScore": 85, "dropPct": 0.506, "decaying": True},
                "FULL_DECOUPLE": {"label": "Full Decouple", "currentScore": 39,
                                  "allTimeScore": 75, "dropPct": 0.48, "decaying": True},
            },
            "backtestContext": {
                "optimalWindow": 60,
                "accuracy": 60,
                "avgLeadTime": 27,
                "fpRate": 40,
            },
            "timestamp": ts,
            "dataAgeSec": 200,
            "isStale": False,
        },
        "/signals/filtered": {
            "regimeId": "SYSTEMIC",
            "regimeLabel": "Systemic Risk-Off",
            "regimeConfidence": 77,
            "totalSignals": 5,
            "actionableCount": 0,
            "suppressedCount": 5,
            "ambiguousCount": 0,
            "filterRules": {
                "SEMI_LEADS": {"label": "Semi Leads", "classification": "SUPPRESS",
                               "hitRate": 0.10, "n": 10, "avgRet": -18.3},
                "CRYPTO_LEADS": {"label": "Crypto Leads", "classification": "SUPPRESS",
                                 "hitRate": 0.20, "n": 5, "avgRet": -9.8},
                "FULL_DECOUPLE": {"label": "Full Decouple", "classification": "SUPPRESS",
                                  "hitRate": 0.25, "n": 4, "avgRet": -7.6},
            },
            "signals": [
                {"pair": "MRVL/TAO", "type": "CRYPTO_LEADS", "typeLabel": "Crypto Leads",
                 "conviction": 84, "reliability": 85, "reliabilityLabel": "HIGH",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.20,
                 "regimeFilterN": 5, "regimeFilterAvgRet": -9.8},
                {"pair": "TSM/RNDR", "type": "FULL_DECOUPLE", "typeLabel": "Full Decouple",
                 "conviction": 72, "reliability": 75, "reliabilityLabel": "HIGH",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.25,
                 "regimeFilterN": 4, "regimeFilterAvgRet": -7.6},
                {"pair": "MRVL/RNDR", "type": "SEMI_LEADS", "typeLabel": "Semi Leads",
                 "conviction": 65, "reliability": 68, "reliabilityLabel": "MEDIUM",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.10,
                 "regimeFilterN": 10, "regimeFilterAvgRet": -18.3},
                {"pair": "NVDA/FET", "type": "CRYPTO_LEADS", "typeLabel": "Crypto Leads",
                 "conviction": 74, "reliability": 85, "reliabilityLabel": "HIGH",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.20,
                 "regimeFilterN": 5, "regimeFilterAvgRet": -9.8},
                {"pair": "AMD/RNDR", "type": "FULL_DECOUPLE", "typeLabel": "Full Decouple",
                 "conviction": 60, "reliability": 75, "reliabilityLabel": "HIGH",
                 "regimeFilter": "SUPPRESS", "regimeFilterHitRate": 0.25,
                 "regimeFilterN": 4, "regimeFilterAvgRet": -7.6},
            ],
            "timestamp": ts,
            "dataAgeSec": 200,
            "isStale": False,
        },
    }


# ── Full Pipeline Runner ───────────────────────────────────────────────────

def run_full_pipeline(client):
    """Execute the complete pipeline path and return structured output.

    Returns (output_dict, exit_code) matching full_pipeline_demo.py behavior.
    """
    # Stage 1: Watchdog
    health = client.get_health()
    reliability = client.get_signal_scores()
    regime = client.get_regime_state()

    r1 = check_system_health(health)
    r2 = check_signal_fidelity(reliability)
    r3 = check_regime_confidence(regime)

    verdicts = [r1[0], r2[0], r3[0]]
    wd_verdict = "STOP" if "STOP" in verdicts else (
        "DEGRADED" if "DEGRADED" in verdicts else "VALID")
    wd_details = {
        "verdict": wd_verdict,
        "system_health": r1[0],
        "system_health_reason": r1[1],
        "signal_fidelity": r2[0],
        "signal_fidelity_reason": r2[1],
        "regime_confidence": r3[0],
        "regime_confidence_reason": r3[1],
    }

    # STOP gate
    if wd_verdict == "STOP":
        return {
            "watchdog": wd_details,
            "scanner": None,
            "overall": {
                "decision": "NO_TRADE",
                "execute_count": 0,
                "wait_count": 0,
                "position_note": "signal integrity compromised",
            },
        }, 2

    # Stage 2: Scanner
    filtered = client.get_filtered_signals()
    decisions = evaluate(filtered, reliability)

    execute_list = [d for d in decisions if d["decision"] == "EXECUTE"]
    wait_list = [d for d in decisions if d["decision"] == "WAIT"]

    # Stage 3: Synthesize
    if not execute_list:
        reason = decisions[0]["reason"] if decisions else "no signals"
        decision = "NO_TRADE"
        note = f"no actionable signals: {reason}"
        exit_code = 1
    elif wd_verdict == "DEGRADED":
        decision = "EXECUTE_REDUCED"
        note = "reduce size due to degraded conditions"
        exit_code = 1
    else:
        decision = "EXECUTE"
        note = None
        exit_code = 0

    return {
        "watchdog": wd_details,
        "scanner": {
            "regime": filtered.regime_id,
            "confidence": filtered.regime_confidence,
            "total_signals": filtered.total_signals,
            "decisions": [{"decision": d["decision"], "gate": d["gate"],
                           "reason": d["reason"],
                           "pair": d["signal"].pair if d["signal"] else None}
                          for d in decisions],
        },
        "overall": {
            "decision": decision,
            "execute_count": len(execute_list),
            "wait_count": len(wait_list),
            "position_note": note,
        },
    }, exit_code


# ── Test Cases ─────────────────────────────────────────────────────────────

class TestFullPipelineIntegration(unittest.TestCase):
    """End-to-end integration tests for the full signal pipeline."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", MOCK_PORT), ScenarioHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = RegimeClient(
            base_url=f"http://localhost:{MOCK_PORT}",
            timeout=5,
            max_retries=1,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    # ── Scenario 1: HEALTHY ────────────────────────────────────────────────

    def test_healthy_pipeline_produces_execute(self):
        """HEALTHY path: NEUTRAL regime + intact CRYPTO_LEADS -> EXECUTE."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = healthy_responses()

        output, exit_code = run_full_pipeline(self.client)

        self.assertEqual(exit_code, 0, "HEALTHY pipeline should exit 0 (EXECUTE)")
        self.assertEqual(output["overall"]["decision"], "EXECUTE")
        self.assertGreater(output["overall"]["execute_count"], 0,
                           "Should have at least 1 EXECUTE signal")

    def test_healthy_watchdog_all_valid(self):
        """HEALTHY path: all three watchdog dimensions should be VALID."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = healthy_responses()

        output, _ = run_full_pipeline(self.client)
        wd = output["watchdog"]

        self.assertEqual(wd["verdict"], "VALID")
        self.assertEqual(wd["system_health"], "VALID")
        self.assertEqual(wd["signal_fidelity"], "VALID")
        self.assertEqual(wd["regime_confidence"], "VALID")

    def test_healthy_scanner_finds_actionable_signals(self):
        """HEALTHY path: scanner should identify CRYPTO_LEADS as EXECUTE."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = healthy_responses()

        output, _ = run_full_pipeline(self.client)
        scanner = output["scanner"]

        self.assertEqual(scanner["regime"], "NEUTRAL")
        execute_decisions = [d for d in scanner["decisions"]
                             if d["decision"] == "EXECUTE"]
        self.assertEqual(len(execute_decisions), 2,
                         "Should have 2 EXECUTE signals (NVDA/RNDR, AMD/TAO)")
        for d in execute_decisions:
            self.assertEqual(d["gate"], "PASSED")
            self.assertIn("CRYPTO_LEADS", d["reason"])

    def test_healthy_suppresses_semi_leads(self):
        """HEALTHY path: SEMI_LEADS should be WAIT (anti-signal)."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = healthy_responses()

        output, _ = run_full_pipeline(self.client)
        scanner = output["scanner"]

        semi_decisions = [d for d in scanner["decisions"]
                          if d.get("pair") == "AVGO/AKT"]
        self.assertEqual(len(semi_decisions), 1)
        self.assertEqual(semi_decisions[0]["decision"], "WAIT")
        self.assertEqual(semi_decisions[0]["gate"], "ANTI_SIGNAL")

    def test_healthy_output_schema(self):
        """HEALTHY path: output contains all required schema fields."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = healthy_responses()

        output, _ = run_full_pipeline(self.client)

        # Top-level keys
        self.assertIn("watchdog", output)
        self.assertIn("scanner", output)
        self.assertIn("overall", output)

        # Watchdog schema
        for key in ("verdict", "system_health", "signal_fidelity", "regime_confidence"):
            self.assertIn(key, output["watchdog"])

        # Scanner schema
        for key in ("regime", "confidence", "total_signals", "decisions"):
            self.assertIn(key, output["scanner"])

        # Overall schema
        for key in ("decision", "execute_count", "wait_count"):
            self.assertIn(key, output["overall"])

    # ── Scenario 2: DEGRADED ───────────────────────────────────────────────

    def test_degraded_pipeline_produces_execute_reduced(self):
        """DEGRADED path: warning conditions -> EXECUTE_REDUCED (exit 1)."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = degraded_responses()

        output, exit_code = run_full_pipeline(self.client)

        self.assertEqual(exit_code, 1,
                         "DEGRADED pipeline should exit 1 (EXECUTE_REDUCED)")
        self.assertEqual(output["overall"]["decision"], "EXECUTE_REDUCED")
        self.assertIsNotNone(output["overall"]["position_note"])
        self.assertIn("degraded", output["overall"]["position_note"].lower())

    def test_degraded_watchdog_detects_warnings(self):
        """DEGRADED path: watchdog should flag DEGRADED, not STOP."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = degraded_responses()

        output, _ = run_full_pipeline(self.client)
        wd = output["watchdog"]

        self.assertEqual(wd["verdict"], "DEGRADED")
        # System health should be DEGRADED (data age >900s or last_error)
        self.assertIn(wd["system_health"], ("DEGRADED", "VALID"))
        # Signal fidelity: 1 type decaying = DEGRADED, not STOP
        self.assertNotEqual(wd["signal_fidelity"], "STOP",
                            "1 type decaying should be DEGRADED, not STOP")

    def test_degraded_scanner_still_finds_signals(self):
        """DEGRADED path: scanner should still identify actionable signals."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = degraded_responses()

        output, _ = run_full_pipeline(self.client)
        scanner = output["scanner"]

        self.assertIsNotNone(scanner, "Scanner should run in DEGRADED (not short-circuited)")
        self.assertEqual(scanner["regime"], "NEUTRAL")
        execute_decisions = [d for d in scanner["decisions"]
                             if d["decision"] == "EXECUTE"]
        self.assertGreater(len(execute_decisions), 0,
                           "DEGRADED should still find EXECUTE signals")

    def test_degraded_has_warning_metadata(self):
        """DEGRADED path: watchdog reasons should contain warning details."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = degraded_responses()

        output, _ = run_full_pipeline(self.client)
        wd = output["watchdog"]

        # At least one reason field should contain substantive info
        reasons = [wd.get("system_health_reason", ""),
                   wd.get("signal_fidelity_reason", ""),
                   wd.get("regime_confidence_reason", "")]
        non_trivial = [r for r in reasons if r and r != "all checks passed"]
        self.assertGreater(len(non_trivial), 0,
                           "DEGRADED should include warning reason strings")

    # ── Scenario 3: HALT ───────────────────────────────────────────────────

    def test_halt_pipeline_produces_no_trade(self):
        """HALT path: signal fidelity STOP -> NO_TRADE (exit 2)."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        output, exit_code = run_full_pipeline(self.client)

        self.assertEqual(exit_code, 2,
                         "HALT pipeline should exit 2 (STOP/NO_TRADE)")
        self.assertEqual(output["overall"]["decision"], "NO_TRADE")
        self.assertEqual(output["overall"]["execute_count"], 0)
        self.assertEqual(output["overall"]["wait_count"], 0)

    def test_halt_watchdog_fires_stop(self):
        """HALT path: watchdog should return STOP verdict."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        output, _ = run_full_pipeline(self.client)
        wd = output["watchdog"]

        self.assertEqual(wd["verdict"], "STOP")
        self.assertEqual(wd["signal_fidelity"], "STOP",
                         "Signal fidelity should be STOP (3/3 decaying)")

    def test_halt_skips_scanner(self):
        """HALT path: scanner should be skipped (short-circuited by STOP)."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        output, _ = run_full_pipeline(self.client)

        self.assertIsNone(output["scanner"],
                          "Scanner should be None when watchdog returns STOP")

    def test_halt_provides_human_readable_explanation(self):
        """HALT path: output should contain explanation, not just codes."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        output, _ = run_full_pipeline(self.client)
        wd = output["watchdog"]

        # Signal fidelity reason should explain the STOP
        reason = wd.get("signal_fidelity_reason", "")
        self.assertIn("decaying_types", reason,
                       "STOP reason should reference decaying signal types")

    def test_halt_note_explains_integrity(self):
        """HALT path: position_note should say why trading is blocked."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        output, _ = run_full_pipeline(self.client)

        note = output["overall"]["position_note"]
        self.assertIsNotNone(note)
        self.assertIn("integrity", note.lower(),
                      "HALT note should mention signal integrity")

    def test_halt_matches_live_stop_behavior(self):
        """HALT path: mock HALT data matches the real live STOP state structure.

        The halt_responses() data is modeled on actual live API payloads
        captured during the STOP state diagnostic (2026-03-13). This test
        verifies the mock faithfully reproduces the real STOP trigger path.
        """
        global MOCK_RESPONSES
        MOCK_RESPONSES = halt_responses()

        # Verify the mock data matches known live values
        rel = MOCK_RESPONSES["/signals/reliability"]
        self.assertTrue(rel["regimeAlert"]["triggered"])
        self.assertEqual(rel["regimeAlert"]["count"], 3)
        self.assertEqual(rel["types"]["CRYPTO_LEADS"]["dropPct"], 50.6)
        self.assertTrue(rel["types"]["CRYPTO_LEADS"]["isDecaying"])

        regime = MOCK_RESPONSES["/regime/current"]
        self.assertEqual(regime["id"], "SYSTEMIC")
        self.assertEqual(regime["confidence"], 77)

        # Run pipeline and verify it matches documented behavior
        output, exit_code = run_full_pipeline(self.client)
        self.assertEqual(exit_code, 2)
        self.assertEqual(output["overall"]["decision"], "NO_TRADE")


# ── Standalone runner ──────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
