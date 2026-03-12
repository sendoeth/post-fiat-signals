#!/usr/bin/env python3
"""Full Pipeline Demo — watchdog -> regime scanner -> trade decision.

Chains three stages into a single executable flow:
  Stage 1: Watchdog — pre-trade safety check (VALID/DEGRADED/STOP)
  Stage 2: Scanner  — 7-gate EXECUTE/WAIT decision per signal
  Stage 3: Decision — synthesize into a final trade recommendation

Outputs both a human-readable CLI report and a machine-readable JSON file
(pipeline_output.json). Returns exit codes for shell chaining:
  0 = EXECUTE      — at least one signal passed all gates
  1 = DEGRADED     — signals present but conditions uncertain
  2 = STOP         — do not trade

Usage:
    # mock mode (default — no live API needed)
    python3 examples/mock_server.py &
    python3 examples/full_pipeline_demo.py

    # live API
    python3 examples/full_pipeline_demo.py --url http://your-node:8080

    # shell chaining
    python3 examples/full_pipeline_demo.py && python3 my_bot.py
"""

import json
import os
import sys
from datetime import datetime, timezone

# SDK + sibling imports
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, ".."))
sys.path.insert(0, _here)

from pf_regime_sdk import RegimeClient
from watchdog import check_system_health, check_signal_fidelity, check_regime_confidence
from regime_scanner import evaluate

# ── Config ─────────────────────────────────────────────────────────────────

PIPELINE_VERSION = "1.0.0"
OUTPUT_FILE = "pipeline_output.json"


# ── Stage 1: Watchdog ──────────────────────────────────────────────────────

def run_watchdog(client):
    """Run 3 circuit breaker checks. Returns (verdict, details, reliability)."""
    health = client.get_health()
    reliability = client.get_signal_scores()
    regime = client.get_regime_state()

    r1 = check_system_health(health)
    r2 = check_signal_fidelity(reliability)
    r3 = check_regime_confidence(regime)

    verdicts = [r1[0], r2[0], r3[0]]
    overall = "STOP" if "STOP" in verdicts else (
        "DEGRADED" if "DEGRADED" in verdicts else "VALID")

    details = {
        "verdict": overall,
        "system_health": r1[0],
        "signal_fidelity": r2[0],
        "regime_confidence": r3[0],
    }
    return overall, details, reliability


# ── Stage 2: Regime Scanner ────────────────────────────────────────────────

def run_scanner(client, reliability):
    """Run 7-gate decision engine. Returns (decisions, filtered_report)."""
    filtered = client.get_filtered_signals()
    decisions = evaluate(filtered, reliability)
    return decisions, filtered


# ── Stage 3: Trade Decision ────────────────────────────────────────────────

def synthesize(watchdog_verdict, watchdog_details, decisions, filtered):
    """Combine watchdog + scanner into final trade output with exit code."""
    execute_list = [d for d in decisions if d["decision"] == "EXECUTE"]
    wait_list = [d for d in decisions if d["decision"] == "WAIT"]

    if watchdog_verdict == "STOP":
        decision = "NO_TRADE"
        note = "signal integrity compromised"
        exit_code = 2
    elif not execute_list:
        decision = "NO_TRADE"
        reason = decisions[0]["reason"] if decisions else "no signals"
        note = f"no actionable signals: {reason}"
        exit_code = 1
    elif watchdog_verdict == "DEGRADED":
        decision = "EXECUTE_REDUCED"
        note = "reduce size due to degraded conditions"
        exit_code = 1
    else:
        decision = "EXECUTE"
        note = None
        exit_code = 0

    scanner_decisions = []
    for d in decisions:
        sig = d["signal"]
        entry = {"decision": d["decision"], "gate": d["gate"], "reason": d["reason"]}
        if sig:
            entry.update({
                "pair": sig.pair,
                "signal_type": sig.signal_type,
                "hit_rate": sig.regime_filter_hit_rate,
                "avg_return": sig.regime_filter_avg_ret,
                "conviction": sig.conviction,
            })
        scanner_decisions.append(entry)

    output = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_version": PIPELINE_VERSION,
        "watchdog": watchdog_details,
        "scanner": {
            "regime": filtered.regime_id,
            "confidence": filtered.regime_confidence,
            "total_signals": filtered.total_signals,
            "decisions": scanner_decisions,
        },
        "overall": {
            "decision": decision,
            "execute_count": len(execute_list),
            "wait_count": len(wait_list),
            "position_note": note,
        },
    }
    return output, exit_code


# ── CLI Report ─────────────────────────────────────────────────────────────

def print_report(output, exit_code):
    """Print human-readable pipeline report to stdout."""
    G, Y, RED, RST = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
    C = {"VALID": G, "DEGRADED": Y, "STOP": RED,
         "EXECUTE": G, "EXECUTE_REDUCED": Y, "NO_TRADE": RED, "WAIT": Y}

    print()
    print("=" * 70)
    print("  FULL PIPELINE DEMO — Signal Intelligence Pipeline")
    print("=" * 70)
    print(f"  Version: {output['pipeline_version']}")
    print(f"  Timestamp: {output['timestamp']}")

    # Stage 1
    w = output["watchdog"]
    print(f"\n  STAGE 1: WATCHDOG")
    print(f"  Verdict: {C.get(w['verdict'], '')}{w['verdict']}{RST}")
    for dim in ("system_health", "signal_fidelity", "regime_confidence"):
        c = C.get(w[dim], "")
        print(f"    {c}{w[dim]:10s}{RST} {dim.replace('_', ' ').title()}")

    # Stage 2
    s = output["scanner"]
    if s:
        print(f"\n  STAGE 2: REGIME SCANNER")
        print(f"  Regime: {s['regime']} (confidence: {s['confidence']})")
        print(f"  Signals: {s['total_signals']}")
        print()
        for d in s["decisions"]:
            if d["decision"] == "EXECUTE":
                tag = f"  {G}EXECUTE{RST}"
            else:
                tag = f"  {Y}  WAIT {RST}"
            pair = d.get("pair", "(regime-level)")
            stype = d.get("signal_type", "")
            print(f"  {tag}  {pair:14s} [{stype:14s}]")
            print(f"             {d['reason']}")

    # Stage 3
    o = output["overall"]
    oc = C.get(o["decision"], "")
    print(f"\n  STAGE 3: TRADE DECISION")
    print(f"  {oc}{o['decision']}{RST}  "
          f"({o['execute_count']} execute, {o['wait_count']} wait)")
    if o["position_note"]:
        print(f"  Note: {o['position_note']}")
    print(f"\n  Exit code: {exit_code}")
    print("=" * 70)
    print()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    url_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("--url="):
            url_arg = arg.split("=", 1)[1]
        elif arg == "--url" and sys.argv.index(arg) + 1 < len(sys.argv):
            url_arg = sys.argv[sys.argv.index(arg) + 1]

    api_url = url_arg or os.environ.get("PF_API_URL", "http://localhost:8080")
    client = RegimeClient(base_url=api_url, timeout=15)

    print(f"Connecting to {api_url}...")

    # Stage 1
    try:
        wd_verdict, wd_details, reliability = run_watchdog(client)
    except Exception as e:
        print(f"\n  \033[31mSTOP\033[0m  API unreachable: {type(e).__name__}: {e}")
        print(f"\n  Make sure the API is running at {api_url}")
        print("  Start mock: python3 examples/mock_server.py")
        sys.exit(2)

    # STOP gate — halt before scanner
    if wd_verdict == "STOP":
        output = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pipeline_version": PIPELINE_VERSION,
            "watchdog": wd_details,
            "scanner": None,
            "overall": {
                "decision": "NO_TRADE",
                "execute_count": 0,
                "wait_count": 0,
                "position_note": "signal integrity compromised",
            },
        }
        with open(OUTPUT_FILE, "w") as f:
            json.dump(output, f, indent=2)
        print_report(output, 2)
        print(f"  Output written to {OUTPUT_FILE}")
        sys.exit(2)

    # Stage 2
    try:
        decisions, filtered = run_scanner(client, reliability)
    except Exception as e:
        print(f"\n  \033[31mERROR\033[0m  Scanner failed: {type(e).__name__}: {e}")
        sys.exit(2)

    # Stage 3
    output, exit_code = synthesize(wd_verdict, wd_details, decisions, filtered)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print_report(output, exit_code)
    print(f"  Output written to {OUTPUT_FILE}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
