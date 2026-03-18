#!/usr/bin/env python3
"""Lightweight mock API server for testing the Post Fiat Signals SDK.

Serves realistic responses for all 6 API endpoints so you can run every
USE_CASES.md snippet locally without needing access to a live API.

Usage:
    python3 examples/mock_server.py              # starts on port 8080
    python3 examples/mock_server.py --port 9090  # custom port

Then in another terminal:
    export PF_API_URL=http://localhost:8080
    python3 examples/regime_scanner.py
    python3 examples/watchdog.py

Zero external dependencies — stdlib only.
"""

import json
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8080

# ── Mock data ──────────────────────────────────────────────────────────────────
# All field names match the camelCase keys that the SDK's from_dict() expects.

def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def regime_current():
    return {
        "state": "NEUTRAL",
        "id": "NEUTRAL",
        "confidence": 72,
        "isAlert": False,
        "action": "Hold current allocations — no regime-driven rebalancing required.",
        "targetWeights": {
            "NVDA": 0.25, "AMD": 0.20, "AVGO": 0.20,
            "MRVL": 0.15, "ASML": 0.20,
        },
        "signals": {
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "currentScore": 45,
                "allTimeScore": 78,
                "dropPct": 42.3,
                "decaying": True,
            },
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "currentScore": 88,
                "allTimeScore": 91,
                "dropPct": 3.3,
                "decaying": False,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "currentScore": 61,
                "allTimeScore": 70,
                "dropPct": 12.9,
                "decaying": False,
            },
        },
        "backtestContext": {
            "optimalWindow": 60,
            "accuracy": 60,
            "avgLeadTime": 27.0,
            "fpRate": 40,
        },
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def rebalancing_queue():
    return {
        "regimeState": "NEUTRAL",
        "confidence": 72,
        "trades": [
            {
                "asset": "RNDR",
                "direction": "BUY",
                "currentPct": 5.0,
                "targetPct": 12.0,
                "deltaPct": 7.0,
                "urgency": "immediate",
                "urgencyLabel": "Immediate — CRYPTO_LEADS divergence active",
                "drivingSignal": "CRYPTO_LEADS",
                "regime": "NEUTRAL",
            },
            {
                "asset": "TAO",
                "direction": "BUY",
                "currentPct": 3.0,
                "targetPct": 8.0,
                "deltaPct": 5.0,
                "urgency": "immediate",
                "urgencyLabel": "Immediate — CRYPTO_LEADS divergence active",
                "drivingSignal": "CRYPTO_LEADS",
                "regime": "NEUTRAL",
            },
            {
                "asset": "AKT",
                "direction": "HOLD",
                "currentPct": 6.0,
                "targetPct": 6.0,
                "deltaPct": 0.0,
                "urgency": "watch",
                "urgencyLabel": "Watch — no active divergence",
                "drivingSignal": "NONE",
                "regime": "NEUTRAL",
            },
        ],
        "tradeCount": 3,
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def signals_reliability():
    return {
        "window": 30,
        "regimeAlert": {
            "triggered": False,
            "count": 1,
            "types": ["SEMI_LEADS"],
            "msg": "1 signal type shows reliability decay",
        },
        "types": {
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "score": 45,
                "reliabilityLabel": "DEGRADED",
                "allTimeScore": 78.0,
                "currentRolling": 45.0,
                "dropPct": 42.3,
                "isDecaying": True,
                "freshness": "Stale",
                "firstDecayDate": "2026-02-20",
            },
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "score": 88,
                "reliabilityLabel": "STRONG",
                "allTimeScore": 91.0,
                "currentRolling": 88.0,
                "dropPct": 3.3,
                "isDecaying": False,
                "freshness": "Fresh",
                "firstDecayDate": None,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "score": 61,
                "reliabilityLabel": "MODERATE",
                "allTimeScore": 70.0,
                "currentRolling": 61.0,
                "dropPct": 12.9,
                "isDecaying": False,
                "freshness": "Recent",
                "firstDecayDate": None,
            },
        },
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def signals_filtered():
    return {
        "decision": "TRADE",
        "decisionReason": "NEUTRAL regime — 2 actionable signals (CRYPTO_LEADS). Hit rate 82% with 8.2% avg return under this regime.",
        "regimeId": "NEUTRAL",
        "regimeLabel": "Neutral — no systemic stress detected",
        "regimeConfidence": 72,
        "totalSignals": 5,
        "actionableCount": 2,
        "suppressedCount": 2,
        "ambiguousCount": 1,
        "filterRules": {
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "classification": "ACTIONABLE",
                "hitRate": 0.82,
                "n": 22,
                "avgRet": 8.24,
            },
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "classification": "SUPPRESS",
                "hitRate": 0.12,
                "n": 16,
                "avgRet": -14.60,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "classification": "AMBIGUOUS",
                "hitRate": 0.80,
                "n": 5,
                "avgRet": 3.83,
            },
        },
        "signals": [
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
                "ticker": "RNDR",
                "semiTicker": "NVDA",
                "action": "BUY",
                "signal_type": "CRYPTO_LEADS",
                "confidence": 0.82,
                "hit_rate": 0.82,
                "avg_return": 8.24,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
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
                "ticker": "TAO",
                "semiTicker": "AMD",
                "action": "BUY",
                "signal_type": "CRYPTO_LEADS",
                "confidence": 0.82,
                "hit_rate": 0.82,
                "avg_return": 8.24,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
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
                "ticker": "AKT",
                "semiTicker": "AVGO",
                "action": "AVOID",
                "signal_type": "SEMI_LEADS",
                "confidence": 0.12,
                "hit_rate": 0.12,
                "avg_return": -14.60,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
            },
            {
                "pair": "MRVL/FET",
                "type": "SEMI_LEADS",
                "typeLabel": "Semi Leads Crypto",
                "conviction": 55,
                "reliability": 45,
                "reliabilityLabel": "DEGRADED",
                "regimeFilter": "SUPPRESS",
                "regimeFilterHitRate": 0.12,
                "regimeFilterN": 16,
                "regimeFilterAvgRet": -14.60,
                "ticker": "FET",
                "semiTicker": "MRVL",
                "action": "AVOID",
                "signal_type": "SEMI_LEADS",
                "confidence": 0.12,
                "hit_rate": 0.12,
                "avg_return": -14.60,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
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
                "ticker": "RNDR",
                "semiTicker": "ASML",
                "action": "HOLD",
                "signal_type": "FULL_DECOUPLE",
                "confidence": 0.80,
                "hit_rate": 0.80,
                "avg_return": 3.83,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
            },
        ],
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def regime_history():
    return {
        "windowDays": 90,
        "currentRegime": "NEUTRAL",
        "transitions": [
            {"date": "2026-01-15", "regime": "NEUTRAL", "transitionFrom": None},
            {"date": "2026-01-28", "regime": "EARNINGS", "transitionFrom": "NEUTRAL"},
            {"date": "2026-02-05", "regime": "NEUTRAL", "transitionFrom": "EARNINGS"},
            {"date": "2026-02-18", "regime": "DIVERGENCE", "transitionFrom": "NEUTRAL"},
            {"date": "2026-02-22", "regime": "NEUTRAL", "transitionFrom": "DIVERGENCE"},
        ],
        "transitionCount": 5,
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def health():
    return {
        "status": "ok",
        "uptime": 86400,
        "uptimeHuman": "1d 0h",
        "lastRefresh": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
        "refreshCount": 96,
        "dataFresh": True,
        "lastError": None,
        "schemaVersion": "v1.1.0",
    }


# ── Route map ──────────────────────────────────────────────────────────────────

ROUTES = {
    "/regime/current":       regime_current,
    "/rebalancing/queue":    rebalancing_queue,
    "/signals/reliability":  signals_reliability,
    "/signals/filtered":     signals_filtered,
    "/regime/history":       regime_history,
    "/health":               health,
}


# ── HTTP handler ───────────────────────────────────────────────────────────────

class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]  # strip query params
        handler = ROUTES.get(path)
        if handler:
            body = json.dumps(handler(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": f"Not found: {path}",
                "available": list(ROUTES.keys()),
            }).encode())

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[mock] {args[0]} {args[1]} {args[2]}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = PORT
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    try:
        server = HTTPServer(("127.0.0.1", port), MockHandler)
    except OSError as e:
        if e.errno == 98:
            print(f"Error: port {port} is already in use.")
            print(f"Try a different port:  python3 examples/mock_server.py --port 9090")
            print(f"Then set:              export PF_API_URL=http://localhost:9090")
        else:
            print(f"Error starting server: {e}")
        sys.exit(1)
    print(f"Mock API running on http://localhost:{port}")
    print(f"Endpoints: {', '.join(ROUTES.keys())}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
