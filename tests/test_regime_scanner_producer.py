"""Tests for the RegimeScannerProducer — b1e55ed integration.

Mocks b1e55ed's framework interfaces so tests run standalone in our repo
without importing their codebase. Uses the same mock HTTP server pattern
as test_stress.py.

Run:  python3 -m unittest tests/test_regime_scanner_producer.py -v
  or: python3 tests/test_regime_scanner_producer.py
"""

import json
import os
import sys
import threading
import time
import unittest
from dataclasses import dataclass, field
from enum import Enum
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Mock b1e55ed framework ────────────────────────────────────────────────────
# Minimal stubs so the producer module can import and run without b1e55ed.

class EventType(Enum):
    SIGNAL_TRADFI_V1 = "SIGNAL_TRADFI_V1"
    FORECAST_V1 = "FORECAST_V1"


@dataclass
class TradFiSignalPayload:
    symbol: str = ""
    source_symbol: str = ""
    direction: str = ""
    confidence: float = 0.0
    signal_reason: str = ""
    pair: str = ""
    conviction: int = 0
    reliability: int = 0
    reliability_label: str = ""


@dataclass
class ForecastPayload:
    action: str = ""
    confidence: float = 0.0
    signal_reason: str = ""


@dataclass
class RegimeMatrix:
    pass


@dataclass
class Event:
    event_type: EventType = EventType.SIGNAL_TRADFI_V1
    producer: str = ""
    payload: Any = None
    dedupe_key: str = ""
    timestamp: str = ""


class Interpreter:
    name: str = ""
    regime_matrix: dict = None


class BaseProducer:
    name: str = ""
    schedule: str = ""
    interpreter_cls = None
    configurable_fields: dict = {}

    def __init__(self, ctx):
        self.ctx = ctx
        self._health_status = "HEALTHY"
        self._health_msg = ""

    def set_health(self, status, msg=""):
        self._health_status = status
        self._health_msg = msg


class ProducerContext:
    def __init__(self, client=None, config=None):
        self.client = client or MagicMock()
        self._config = config or {}
        self.published: List[Event] = []

    def get(self, key, default=None):
        return self._config.get(key, default)

    @property
    def config(self):
        return self._config

    def publish(self, event: Event):
        self.published.append(event)


def register(name, domain=""):
    """No-op decorator that mimics b1e55ed's registry."""
    def decorator(cls):
        cls._registered_name = name
        cls._registered_domain = domain
        return cls
    return decorator


# Patch the b1e55ed modules before importing the producer
import types

engine_mod = types.ModuleType("engine")
base_mod = types.ModuleType("engine.base")
events_mod = types.ModuleType("engine.events")
interp_mod = types.ModuleType("engine.interpreters")
reg_mod = types.ModuleType("engine.registry")
types_mod = types.ModuleType("engine.types")

base_mod.BaseProducer = BaseProducer
base_mod.ProducerContext = ProducerContext
events_mod.Event = Event
events_mod.EventType = EventType
interp_mod.Interpreter = Interpreter
reg_mod.register = register
types_mod.ForecastPayload = ForecastPayload
types_mod.RegimeMatrix = RegimeMatrix
types_mod.TradFiSignalPayload = TradFiSignalPayload

sys.modules["engine"] = engine_mod
sys.modules["engine.base"] = base_mod
sys.modules["engine.events"] = events_mod
sys.modules["engine.interpreters"] = interp_mod
sys.modules["engine.registry"] = reg_mod
sys.modules["engine.types"] = types_mod

# Now import the producer
from integration.regime_scanner_producer import (
    RegimeScannerProducer,
    RegimeScannerInterpreter,
    REGIME_MAP,
    REGIME_MATRIX,
)


# ── Mock HTTP server ──────────────────────────────────────────────────────────

MOCK_PORT = 19879  # different from test_stress.py (19876) to avoid collision
MOCK_MODE = {"mode": "valid_json", "body": None, "status": 200, "delay": 0}


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _filtered_response(regime_id="NEUTRAL", signals=None):
    """Build a realistic /signals/filtered response."""
    if signals is None:
        signals = [
            {
                "pair": "NVDA/RNDR",
                "type": "CRYPTO_LEADS",
                "typeLabel": "Crypto Leads Semi",
                "conviction": 85,
                "reliability": 88,
                "reliabilityLabel": "STRONG",
                "regimeFilter": "ACTIONABLE",
                "regimeFilterHitRate": 0.82,
                "regimeFilterN": 22,
                "regimeFilterAvgRet": 8.24,
            },
            {
                "pair": "AMD/TAO",
                "type": "CRYPTO_LEADS",
                "typeLabel": "Crypto Leads Semi",
                "conviction": 71,
                "reliability": 88,
                "reliabilityLabel": "STRONG",
                "regimeFilter": "ACTIONABLE",
                "regimeFilterHitRate": 0.82,
                "regimeFilterN": 22,
                "regimeFilterAvgRet": 8.24,
            },
            {
                "pair": "AVGO/AKT",
                "type": "SEMI_LEADS",
                "typeLabel": "Semi Leads Crypto",
                "conviction": 60,
                "reliability": 45,
                "reliabilityLabel": "DEGRADED",
                "regimeFilter": "SUPPRESS",
                "regimeFilterHitRate": 0.12,
                "regimeFilterN": 16,
                "regimeFilterAvgRet": -14.60,
            },
            {
                "pair": "ASML/RNDR",
                "type": "FULL_DECOUPLE",
                "typeLabel": "Full Decoupling",
                "conviction": 40,
                "reliability": 61,
                "reliabilityLabel": "MODERATE",
                "regimeFilter": "AMBIGUOUS",
                "regimeFilterHitRate": 0.80,
                "regimeFilterN": 5,
                "regimeFilterAvgRet": 3.83,
            },
        ]

    return {
        "regimeId": regime_id,
        "regimeLabel": f"{regime_id} regime",
        "regimeConfidence": 72,
        "totalSignals": len(signals),
        "actionableCount": sum(1 for s in signals if s.get("regimeFilter") == "ACTIONABLE"),
        "suppressedCount": sum(1 for s in signals if s.get("regimeFilter") == "SUPPRESS"),
        "ambiguousCount": sum(1 for s in signals if s.get("regimeFilter") == "AMBIGUOUS"),
        "filterRules": {},
        "signals": signals,
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        mode = MOCK_MODE["mode"]
        delay = MOCK_MODE.get("delay", 0)

        if delay:
            time.sleep(delay)

        if mode == "valid_json":
            body = MOCK_MODE.get("body")
            if body is None:
                body = _filtered_response()
            self._respond(200, json.dumps(body))
        elif mode == "connection_error":
            self.connection.close()
            return
        elif mode == "timeout":
            time.sleep(15)
            self._respond(200, "{}")
        elif mode == "http_500":
            self._respond(500, json.dumps({"error": "Internal Server Error"}))
        elif mode == "empty_signals":
            resp = _filtered_response(signals=[])
            self._respond(200, json.dumps(resp))
        else:
            self._respond(200, json.dumps(_filtered_response()))

    def _respond(self, code, body_str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body_str.encode())

    def log_message(self, fmt, *args):
        pass  # suppress logs during tests


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _start_mock_server():
    server = _ReusableServer(("127.0.0.1", MOCK_PORT), MockHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ── Mock HTTP client for ProducerContext ──────────────────────────────────────

class MockHTTPClient:
    """Mimics b1e55ed's ctx.client with a request_json method."""

    def __init__(self, base_url):
        self.base_url = base_url

    def request_json(self, url, timeout=10):
        import urllib.request
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRegimeScannerProducer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = _start_mock_server()
        time.sleep(0.1)  # let server bind

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self._set_mode("valid_json")
        self.api_url = f"http://127.0.0.1:{MOCK_PORT}"
        os.environ["PF_REGIME_API_URL"] = self.api_url

        client = MockHTTPClient(self.api_url)
        self.ctx = ProducerContext(
            client=client,
            config={"universe": {"symbols": ["RNDR", "TAO", "AKT", "FET"]}},
        )
        self.producer = RegimeScannerProducer(self.ctx)

    def tearDown(self):
        os.environ.pop("PF_REGIME_API_URL", None)
        os.environ.pop("PF_REGIME_API_TIMEOUT", None)

    def _set_mode(self, mode, **kw):
        MOCK_MODE.clear()
        MOCK_MODE["mode"] = mode
        MOCK_MODE.update(kw)

    # ── collect tests ─────────────────────────────────────────────────────────

    def test_collect_returns_signals(self):
        """collect() returns a list of signal dicts from /signals/filtered."""
        signals = self.producer.collect()
        self.assertIsInstance(signals, list)
        self.assertEqual(len(signals), 4)
        self.assertEqual(signals[0]["pair"], "NVDA/RNDR")
        self.assertEqual(signals[0]["type"], "CRYPTO_LEADS")
        # Verify regime info was attached
        self.assertEqual(signals[0]["_regime_id"], "NEUTRAL")

    def test_collect_handles_connection_error(self):
        """collect() returns [] and sets DEGRADED on connection failure."""
        os.environ["PF_REGIME_API_URL"] = "http://127.0.0.1:1"  # nothing listening
        producer = RegimeScannerProducer(self.ctx)
        producer.api_url = "http://127.0.0.1:1"
        signals = producer.collect()
        self.assertEqual(signals, [])
        self.assertIn(producer._health_status, ("DEGRADED", "ERROR"))

    def test_collect_handles_timeout(self):
        """collect() returns [] on timeout."""
        self._set_mode("timeout")
        os.environ["PF_REGIME_API_TIMEOUT"] = "1"
        producer = RegimeScannerProducer(self.ctx)
        producer.timeout = 1
        signals = producer.collect()
        self.assertEqual(signals, [])
        self.assertIn(producer._health_status, ("DEGRADED", "ERROR"))

    def test_collect_empty_signals(self):
        """collect() handles response with empty signals list."""
        self._set_mode("empty_signals")
        signals = self.producer.collect()
        self.assertEqual(signals, [])

    # ── normalize tests ───────────────────────────────────────────────────────

    def test_normalize_crypto_leads(self):
        """CRYPTO_LEADS + ACTIONABLE normalizes to direction=long."""
        signals = self.producer.collect()
        events = self.producer.normalize(signals)

        # First signal is NVDA/RNDR CRYPTO_LEADS
        rndr_event = events[0]
        self.assertEqual(rndr_event.event_type, EventType.SIGNAL_TRADFI_V1)
        self.assertEqual(rndr_event.producer, "regime-scanner")
        self.assertEqual(rndr_event.payload.symbol, "RNDR")
        self.assertEqual(rndr_event.payload.source_symbol, "NVDA")
        self.assertEqual(rndr_event.payload.direction, "long")
        self.assertAlmostEqual(rndr_event.payload.confidence, 0.82, places=2)

    def test_normalize_semi_leads(self):
        """SEMI_LEADS + SUPPRESS normalizes to direction=flat."""
        signals = self.producer.collect()
        events = self.producer.normalize(signals)

        # Third signal is AVGO/AKT SEMI_LEADS
        akt_event = events[2]
        self.assertEqual(akt_event.payload.symbol, "AKT")
        self.assertEqual(akt_event.payload.source_symbol, "AVGO")
        self.assertEqual(akt_event.payload.direction, "flat")
        self.assertAlmostEqual(akt_event.payload.confidence, 0.0, places=2)

    def test_normalize_extracts_crypto_symbol(self):
        """Pair 'NVDA/RNDR' extracts symbol='RNDR', source_symbol='NVDA'."""
        signals = self.producer.collect()
        events = self.producer.normalize(signals)

        self.assertEqual(events[0].payload.symbol, "RNDR")
        self.assertEqual(events[0].payload.source_symbol, "NVDA")
        self.assertEqual(events[1].payload.symbol, "TAO")
        self.assertEqual(events[1].payload.source_symbol, "AMD")

    def test_normalize_dedupe_key(self):
        """Dedupe key follows format signal.tradfi.v1:regime-scanner:{symbol}:{ts}."""
        signals = self.producer.collect()
        events = self.producer.normalize(signals)

        key = events[0].dedupe_key
        self.assertTrue(key.startswith("signal.tradfi.v1:regime-scanner:RNDR:"))
        parts = key.split(":")
        self.assertEqual(len(parts), 4)
        # Last part should be an integer timestamp
        int(parts[3])  # raises ValueError if not an int

    # ── interpreter tests ─────────────────────────────────────────────────────

    def test_interpreter_execute_neutral_crypto_leads(self):
        """NEUTRAL regime + CRYPTO_LEADS ACTIONABLE -> long forecast."""
        interp = RegimeScannerInterpreter()
        signals = [
            {
                "crypto_symbol": "RNDR",
                "type": "CRYPTO_LEADS",
                "regime_filter": "ACTIONABLE",
                "hit_rate": 0.82,
                "avg_ret": 8.24,
                "n": 22,
                "pair": "NVDA/RNDR",
                "regime_id": "NEUTRAL",
            }
        ]

        result = interp.interpret("RNDR", signals, "BULL")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "long")
        # BULL multiplier = 1.1 -> 0.82 * 1.1 = 0.902
        self.assertAlmostEqual(result.confidence, 0.902, places=3)
        self.assertIn("NVDA/RNDR", result.signal_reason)
        self.assertIn("82%", result.signal_reason)

    def test_interpreter_suppress_semi_leads(self):
        """SEMI_LEADS signals produce abstention with REGIME_FILTERED reason."""
        interp = RegimeScannerInterpreter()
        signals = [
            {
                "crypto_symbol": "AKT",
                "type": "SEMI_LEADS",
                "regime_filter": "SUPPRESS",
                "hit_rate": 0.12,
                "avg_ret": -14.60,
                "n": 16,
                "pair": "AVGO/AKT",
                "regime_id": "NEUTRAL",
            }
        ]

        result = interp.interpret("AKT", signals, "BULL")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "abstain")
        self.assertIn("REGIME_FILTERED", result.signal_reason)
        self.assertIn("anti-signal", result.signal_reason)

    def test_interpreter_suppress_systemic(self):
        """SYSTEMIC regime (mapped to CRISIS) causes abstention via regime matrix."""
        interp = RegimeScannerInterpreter()
        signals = [
            {
                "crypto_symbol": "RNDR",
                "type": "CRYPTO_LEADS",
                "regime_filter": "ACTIONABLE",
                "hit_rate": 0.82,
                "avg_ret": 8.24,
                "n": 22,
                "pair": "NVDA/RNDR",
                "regime_id": "SYSTEMIC",
            }
        ]

        # SYSTEMIC maps to CRISIS which has abstain=True
        result = interp.interpret("RNDR", signals, "CRISIS")
        self.assertIsNone(result)

    def test_interpreter_full_decouple_abstain(self):
        """FULL_DECOUPLE signals produce abstention with INSUFFICIENT_DATA."""
        interp = RegimeScannerInterpreter()
        signals = [
            {
                "crypto_symbol": "RNDR",
                "type": "FULL_DECOUPLE",
                "regime_filter": "AMBIGUOUS",
                "hit_rate": 0.80,
                "avg_ret": 3.83,
                "n": 5,
                "pair": "ASML/RNDR",
                "regime_id": "NEUTRAL",
            }
        ]

        result = interp.interpret("RNDR", signals, "BULL")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "abstain")
        self.assertIn("INSUFFICIENT_DATA", result.signal_reason)

    # ── regime mapping tests ──────────────────────────────────────────────────

    def test_regime_mapping(self):
        """Our regime states map to b1e55ed regime tags correctly."""
        self.assertEqual(REGIME_MAP["NEUTRAL"], "BULL")
        self.assertEqual(REGIME_MAP["SYSTEMIC"], "CRISIS")
        self.assertEqual(REGIME_MAP["EARNINGS"], "TRANSITION")
        self.assertEqual(REGIME_MAP["DIVERGENCE"], "TRANSITION")

    def test_regime_matrix_values(self):
        """Regime matrix has correct multipliers and flags."""
        self.assertAlmostEqual(REGIME_MATRIX["BULL"].confidence_multiplier, 1.1)
        self.assertAlmostEqual(REGIME_MATRIX["BEAR"].confidence_multiplier, 0.7)
        self.assertAlmostEqual(REGIME_MATRIX["BEAR"].min_confidence, 0.5)
        self.assertTrue(REGIME_MATRIX["CRISIS"].abstain)
        self.assertAlmostEqual(REGIME_MATRIX["TRANSITION"].confidence_multiplier, 0.85)

    # ── full cycle integration test ───────────────────────────────────────────

    def test_run_full_cycle(self):
        """Full cycle: mock API -> collect -> normalize -> interpret -> publish."""
        events = self.producer.run()

        # Should have signal events + forecast events
        self.assertGreater(len(events), 0)

        signal_events = [e for e in events if e.event_type == EventType.SIGNAL_TRADFI_V1]
        forecast_events = [e for e in events if e.event_type == EventType.FORECAST_V1]

        # 4 signals from mock response
        self.assertEqual(len(signal_events), 4)

        # RNDR and TAO should get forecasts (CRYPTO_LEADS + ACTIONABLE)
        # AKT is SEMI_LEADS -> abstain (no forecast event)
        # FET has no matching signal -> no forecast
        forecast_symbols = [e.payload.signal_reason for e in forecast_events]
        self.assertEqual(len(forecast_events), 2)

        # Verify events were published to context
        self.assertEqual(len(self.ctx.published), len(events))

    def test_run_systemic_no_forecasts(self):
        """Under SYSTEMIC regime, no forecasts are emitted (all suppressed)."""
        self._set_mode("valid_json", body=_filtered_response(regime_id="SYSTEMIC"))
        events = self.producer.run()

        forecast_events = [e for e in events if e.event_type == EventType.FORECAST_V1]
        self.assertEqual(len(forecast_events), 0)


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
