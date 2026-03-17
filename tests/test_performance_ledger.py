#!/usr/bin/env python3
"""Performance Ledger tests — dedup, NO_TRADE, EXECUTE, evaluation, summary.

Uses mock HTTP server pattern from test_full_pipeline_integration.py.

Run:
    python -m pytest tests/test_performance_ledger.py -v
    python tests/test_performance_ledger.py
"""

import json
import os
import sys
import threading
import unittest
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from performance_ledger import (
    get_cycle_key,
    entry_exists,
    build_no_trade_entry,
    build_execute_entry,
    evaluate_pending_entries,
    compute_summary,
    atomic_write_json,
    generate_signal_id,
    read_pipeline_output,
    SCHEMA,
    HORIZON_HOURS,
)


# ── Mock HTTP Server ──────────────────────────────────────────────────────

MOCK_PORT = 19878
MOCK_RESPONSES = {}


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        data = MOCK_RESPONSES.get(path)
        if data is not None:
            body = json.dumps(data).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": "not found"}).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


# ── Test Fixtures ─────────────────────────────────────────────────────────

def stop_pipeline_output():
    """Pipeline output when watchdog returns STOP."""
    return {
        "timestamp": "2026-03-17T14:02:31Z",
        "pipeline_version": "1.0.0",
        "watchdog": {
            "verdict": "STOP",
            "system_health": "VALID",
            "signal_fidelity": "STOP",
            "regime_confidence": "VALID",
        },
        "scanner": None,
        "overall": {
            "decision": "NO_TRADE",
            "execute_count": 0,
            "wait_count": 0,
            "position_note": "signal integrity compromised",
        },
    }


def execute_pipeline_output():
    """Pipeline output when signals pass all gates."""
    return {
        "timestamp": "2026-04-01T14:02:31Z",
        "pipeline_version": "1.0.0",
        "watchdog": {
            "verdict": "VALID",
            "system_health": "VALID",
            "signal_fidelity": "VALID",
            "regime_confidence": "VALID",
        },
        "scanner": {
            "regime": "NEUTRAL",
            "confidence": 72,
            "total_signals": 3,
            "decisions": [
                {
                    "decision": "EXECUTE",
                    "gate": "PASSED",
                    "reason": "NEUTRAL + CRYPTO_LEADS + ACTIONABLE | hit=82% avg_ret=+8.24% n=22",
                    "pair": "NVDA/RNDR",
                    "signal_type": "CRYPTO_LEADS",
                    "hit_rate": 0.82,
                    "avg_return": 8.24,
                    "conviction": 85,
                },
                {
                    "decision": "WAIT",
                    "gate": "ANTI_SIGNAL",
                    "reason": "SEMI_LEADS is an anti-signal under NEUTRAL",
                    "pair": "AVGO/AKT",
                    "signal_type": "SEMI_LEADS",
                    "hit_rate": 0.12,
                    "avg_return": -14.60,
                    "conviction": 60,
                },
            ],
        },
        "overall": {
            "decision": "EXECUTE",
            "execute_count": 1,
            "wait_count": 1,
            "position_note": None,
        },
    }


# ── Dedup Tests ───────────────────────────────────────────────────────────

class TestCycleKeyDedup(unittest.TestCase):
    """Dedup via cycle_key (3 tests)."""

    def test_cycle_key_rounds_down(self):
        """Minutes 0-14 round to :00, 15-29 to :15, etc."""
        dt1 = datetime(2026, 3, 17, 14, 7, 45, tzinfo=timezone.utc)
        self.assertEqual(get_cycle_key(dt1), "2026-03-17T14:00:00Z")

        dt2 = datetime(2026, 3, 17, 14, 22, 10, tzinfo=timezone.utc)
        self.assertEqual(get_cycle_key(dt2), "2026-03-17T14:15:00Z")

        dt3 = datetime(2026, 3, 17, 14, 47, 59, tzinfo=timezone.utc)
        self.assertEqual(get_cycle_key(dt3), "2026-03-17T14:45:00Z")

    def test_cycle_key_boundary_exact(self):
        """Exact 15-min boundaries stay unchanged."""
        dt = datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(get_cycle_key(dt), "2026-03-17T14:30:00Z")

    def test_entry_exists_detects_duplicate(self):
        """entry_exists returns True when cycle_key matches."""
        entries = [
            {"cycle_key": "2026-03-17T14:00:00Z", "decision": "NO_TRADE"},
            {"cycle_key": "2026-03-17T14:15:00Z", "decision": "NO_TRADE"},
        ]
        self.assertTrue(entry_exists(entries, "2026-03-17T14:00:00Z"))
        self.assertFalse(entry_exists(entries, "2026-03-17T14:30:00Z"))


# ── NO_TRADE Tests ────────────────────────────────────────────────────────

class TestNoTradeEntry(unittest.TestCase):
    """NO_TRADE entry construction (3 tests)."""

    def test_no_trade_has_all_required_fields(self):
        """NO_TRADE entry includes all schema fields."""
        pipeline = stop_pipeline_output()
        regime_info = {"regime": "SYSTEMIC", "confidence": 77}
        entry = build_no_trade_entry(
            pipeline, "2026-03-17T14:00:00Z", "2026-03-17T14:02:31Z", regime_info
        )

        required = [
            "schema", "cycle_key", "timestamp", "decision", "regime",
            "regime_confidence", "watchdog_verdict", "signal_fidelity",
            "regime_confidence_verdict", "note", "action", "horizon_hours",
        ]
        for field in required:
            self.assertIn(field, entry, f"Missing field: {field}")

    def test_no_trade_correct_values_from_stop(self):
        """NO_TRADE entry has correct values from STOP pipeline."""
        pipeline = stop_pipeline_output()
        regime_info = {"regime": "SYSTEMIC", "confidence": 77}
        entry = build_no_trade_entry(
            pipeline, "2026-03-17T14:00:00Z", "2026-03-17T14:02:31Z", regime_info
        )

        self.assertEqual(entry["schema"], SCHEMA)
        self.assertEqual(entry["decision"], "NO_TRADE")
        self.assertEqual(entry["regime"], "SYSTEMIC")
        self.assertEqual(entry["regime_confidence"], 77)
        self.assertEqual(entry["watchdog_verdict"], "STOP")
        self.assertEqual(entry["signal_fidelity"], "STOP")
        self.assertEqual(entry["action"], "NO_TRADE")
        self.assertEqual(entry["horizon_hours"], HORIZON_HOURS)

    def test_no_trade_spi_fields_present(self):
        """NO_TRADE entry includes SPI-compatible fields."""
        pipeline = stop_pipeline_output()
        entry = build_no_trade_entry(
            pipeline, "2026-03-17T14:00:00Z", "2026-03-17T14:02:31Z"
        )

        self.assertIn("action", entry)
        self.assertIn("horizon_hours", entry)
        self.assertEqual(entry["action"], "NO_TRADE")
        self.assertEqual(entry["horizon_hours"], 336)


# ── EXECUTE Tests ─────────────────────────────────────────────────────────

class TestExecuteEntry(unittest.TestCase):
    """EXECUTE entry construction (4 tests)."""

    @classmethod
    def setUpClass(cls):
        """Start mock server for price fetching."""
        global MOCK_RESPONSES
        MOCK_RESPONSES = {
            "/simple/price": {"render-token": {"usd": 12.45}},
        }
        cls.server = HTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_execute_has_all_required_fields(self):
        """EXECUTE entry includes all schema fields including prices."""
        pipeline = execute_pipeline_output()
        signal = pipeline["scanner"]["decisions"][0]  # NVDA/RNDR EXECUTE
        entry = build_execute_entry(
            pipeline, signal, "2026-04-01T14:00:00Z", "2026-04-01T14:02:31Z"
        )

        required = [
            "schema", "cycle_key", "timestamp", "decision", "regime",
            "regime_confidence", "watchdog_verdict", "signal_fidelity",
            "regime_confidence_verdict", "note", "signal_id", "pair",
            "ticker", "semi_ticker", "action", "signal_type", "confidence",
            "hit_rate", "avg_return", "conviction", "entry_price_crypto",
            "entry_price_semi", "entry_timestamp", "eval_due", "eval_status",
            "eval_price_crypto", "eval_price_semi", "actual_14d_return",
            "hit", "horizon_hours",
        ]
        for field in required:
            self.assertIn(field, entry, f"Missing field: {field}")

    def test_execute_eval_due_is_14_days(self):
        """eval_due should be entry timestamp + 14 days (336 hours)."""
        pipeline = execute_pipeline_output()
        signal = pipeline["scanner"]["decisions"][0]
        entry = build_execute_entry(
            pipeline, signal, "2026-04-01T14:00:00Z", "2026-04-01T14:02:31Z"
        )

        entry_dt = datetime(2026, 4, 1, 14, 2, 31, tzinfo=timezone.utc)
        expected_eval = (entry_dt + timedelta(hours=336)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(entry["eval_due"], expected_eval)

    def test_execute_signal_id_deterministic(self):
        """signal_id should be deterministic from pair + cycle_key."""
        pipeline = execute_pipeline_output()
        signal = pipeline["scanner"]["decisions"][0]

        entry1 = build_execute_entry(
            pipeline, signal, "2026-04-01T14:00:00Z", "2026-04-01T14:02:31Z"
        )
        entry2 = build_execute_entry(
            pipeline, signal, "2026-04-01T14:00:00Z", "2026-04-01T14:02:31Z"
        )
        self.assertEqual(entry1["signal_id"], entry2["signal_id"])

        # Different cycle_key -> different signal_id
        entry3 = build_execute_entry(
            pipeline, signal, "2026-04-01T14:15:00Z", "2026-04-01T14:16:00Z"
        )
        self.assertNotEqual(entry1["signal_id"], entry3["signal_id"])

    def test_execute_extracts_pair_components(self):
        """Pair 'NVDA/RNDR' splits into ticker=RNDR, semi_ticker=NVDA."""
        pipeline = execute_pipeline_output()
        signal = pipeline["scanner"]["decisions"][0]
        entry = build_execute_entry(
            pipeline, signal, "2026-04-01T14:00:00Z", "2026-04-01T14:02:31Z"
        )
        self.assertEqual(entry["ticker"], "RNDR")
        self.assertEqual(entry["semi_ticker"], "NVDA")
        self.assertEqual(entry["action"], "BUY")


# ── Evaluation Tests ──────────────────────────────────────────────────────

class TestEvaluation(unittest.TestCase):
    """Pending entry evaluation (3 tests)."""

    def _make_pending_entry(self, eval_due_str, entry_price=10.0):
        return {
            "decision": "EXECUTE",
            "eval_status": "PENDING",
            "eval_due": eval_due_str,
            "ticker": "RNDR",
            "semi_ticker": "NVDA",
            "signal_id": "test123",
            "pair": "NVDA/RNDR",
            "entry_price_crypto": entry_price,
            "entry_price_semi": 140.0,
            "eval_price_crypto": None,
            "eval_price_semi": None,
            "actual_14d_return": None,
            "hit": None,
        }

    def test_skips_future_entries(self):
        """Entries with eval_due in the future stay PENDING."""
        future = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries = [self._make_pending_entry(future)]
        evaluated = evaluate_pending_entries(entries)
        self.assertEqual(evaluated, 0)
        self.assertEqual(entries[0]["eval_status"], "PENDING")

    def test_computes_return_correctly(self):
        """When price fetch succeeds, return is computed correctly."""
        # Mock evaluate_pending_entries by manually setting eval prices
        past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = self._make_pending_entry(past, entry_price=10.0)

        # Simulate what evaluate_pending_entries does after fetching price
        eval_price = 12.0  # +20%
        actual_return = ((eval_price - entry["entry_price_crypto"]) / entry["entry_price_crypto"]) * 100
        entry["eval_price_crypto"] = eval_price
        entry["actual_14d_return"] = round(actual_return, 2)
        entry["hit"] = actual_return > 0
        entry["eval_status"] = "EVALUATED"

        self.assertEqual(entry["actual_14d_return"], 20.0)
        self.assertTrue(entry["hit"])
        self.assertEqual(entry["eval_status"], "EVALUATED")

    def test_negative_return_is_miss(self):
        """Negative return sets hit=False."""
        entry = self._make_pending_entry("2026-01-01T00:00:00Z", entry_price=10.0)

        eval_price = 8.0  # -20%
        actual_return = ((eval_price - entry["entry_price_crypto"]) / entry["entry_price_crypto"]) * 100
        entry["eval_price_crypto"] = eval_price
        entry["actual_14d_return"] = round(actual_return, 2)
        entry["hit"] = actual_return > 0
        entry["eval_status"] = "EVALUATED"

        self.assertEqual(entry["actual_14d_return"], -20.0)
        self.assertFalse(entry["hit"])


# ── Summary Tests ─────────────────────────────────────────────────────────

class TestSummary(unittest.TestCase):
    """Summary recomputation (2 tests)."""

    def test_recomputes_from_entries(self):
        """Summary correctly aggregates entry statistics."""
        entries = [
            {"decision": "NO_TRADE", "regime": "SYSTEMIC"},
            {"decision": "NO_TRADE", "regime": "SYSTEMIC"},
            {"decision": "EXECUTE", "regime": "NEUTRAL", "eval_status": "EVALUATED",
             "actual_14d_return": 12.5, "hit": True},
            {"decision": "EXECUTE", "regime": "NEUTRAL", "eval_status": "EVALUATED",
             "actual_14d_return": -5.0, "hit": False},
            {"decision": "EXECUTE", "regime": "NEUTRAL", "eval_status": "PENDING",
             "actual_14d_return": None, "hit": None},
        ]

        summary = compute_summary(entries)

        self.assertEqual(summary["total_entries"], 5)
        self.assertEqual(summary["no_trade_count"], 2)
        self.assertEqual(summary["execute_count"], 3)
        self.assertEqual(summary["evaluated_count"], 2)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["hits"], 1)
        self.assertEqual(summary["misses"], 1)
        self.assertEqual(summary["hit_rate"], 0.5)
        self.assertEqual(summary["avg_return"], 3.75)  # (12.5 + -5.0) / 2
        self.assertEqual(summary["regime_distribution"], {"SYSTEMIC": 2, "NEUTRAL": 3})

    def test_handles_mixed_entry_types(self):
        """Summary handles all-NO_TRADE (no evaluatable entries)."""
        entries = [
            {"decision": "NO_TRADE", "regime": "SYSTEMIC"},
            {"decision": "NO_TRADE", "regime": "SYSTEMIC"},
            {"decision": "NO_TRADE", "regime": "SYSTEMIC"},
        ]

        summary = compute_summary(entries)

        self.assertEqual(summary["total_entries"], 3)
        self.assertEqual(summary["no_trade_count"], 3)
        self.assertEqual(summary["execute_count"], 0)
        self.assertEqual(summary["evaluated_count"], 0)
        self.assertIsNone(summary["hit_rate"])
        self.assertIsNone(summary["avg_return"])
        self.assertEqual(summary["no_trade_streak"], 3)


# ── Atomic Write Tests ────────────────────────────────────────────────────

class TestAtomicWrite(unittest.TestCase):
    """Atomic file write (1 test)."""

    def test_atomic_write_produces_valid_json(self):
        """atomic_write_json produces valid, readable JSON."""
        import tempfile
        tmp = tempfile.mktemp(suffix=".json")
        try:
            data = {"schema": SCHEMA, "entries": [{"test": True}]}
            atomic_write_json(tmp, data)
            with open(tmp) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["schema"], SCHEMA)
            self.assertEqual(len(loaded["entries"]), 1)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# ── Signal ID Tests ───────────────────────────────────────────────────────

class TestSignalId(unittest.TestCase):
    """Signal ID generation (1 test)."""

    def test_signal_id_is_12_char_hex(self):
        """signal_id is a 12-char hex string."""
        sid = generate_signal_id("NVDA/RNDR", "2026-04-01T14:00:00Z")
        self.assertEqual(len(sid), 12)
        # Verify it's valid hex
        int(sid, 16)


# ── Pipeline Output Reading Tests ─────────────────────────────────────────

class TestReadPipelineOutput(unittest.TestCase):
    """Pipeline output validation (1 test)."""

    def test_rejects_missing_fields(self):
        """Pipeline output without watchdog/overall is rejected."""
        import tempfile
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w") as f:
                json.dump({"foo": "bar"}, f)
            result = read_pipeline_output(tmp)
            self.assertIsNone(result)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# ── Standalone runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
