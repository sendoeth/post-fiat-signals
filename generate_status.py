#!/usr/bin/env python3
"""Generate status.json — public system health surface for the signal stack.

Queries the live Signal Intelligence API, runs the same circuit breaker
checks as the watchdog, and writes a structured status.json that external
builders can read to determine whether the stack is safe to integrate against.

Usage:
    export PF_API_URL=http://localhost:8080
    python3 generate_status.py

    # or specify output path
    python3 generate_status.py --out /path/to/status.json

The output file is designed to be committed to the public repo so it is
accessible at:
    https://raw.githubusercontent.com/sendonanawitakeshi/post-fiat-signals/main/status.json

Health states:
    HEALTHY   — all subsystems operational, signals fresh, safe to integrate
    DEGRADED  — warning conditions present, proceed with caution
    HALT      — signal integrity compromised, do not rely on signals

Each component reports its own state and a human-readable message explaining
the current condition in terms a first-time builder would understand.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from pf_regime_sdk import RegimeClient


# ── Thresholds (same as watchdog.py) ────────────────────────────────────────

STALE_WARNING_SEC = 900
STALE_CRITICAL_SEC = 1800
MIN_CONFIDENCE = 50
DECAY_TYPES_WARN = 1
DECAY_TYPES_STOP = 2
CRYPTO_LEADS_DROP_WARN = 20
CRYPTO_LEADS_DROP_STOP = 40


# ── Component Checks ───────────────────────────────────────────────────────

def check_regime_engine(regime, filtered):
    """Evaluate the regime classification engine."""
    state = "HEALTHY"
    details = {}
    messages = []

    # Regime state
    details["regime"] = regime.regime_id
    details["regime_label"] = regime.regime_type
    details["confidence"] = regime.confidence_score
    details["is_alert"] = regime.is_alert

    if regime.confidence_score < MIN_CONFIDENCE:
        state = "DEGRADED"
        messages.append(
            f"Regime classifier confidence is {regime.confidence_score}%, "
            f"below the {MIN_CONFIDENCE}% threshold. Classification may be unreliable."
        )

    if regime.is_alert:
        if state != "HALT":
            state = "DEGRADED"
        messages.append(
            "Regime alert is active, indicating the market regime may be shifting. "
            "Signal classifications should be treated with extra caution."
        )

    # Regime type context
    if regime.regime_id == "SYSTEMIC":
        details["regime_implication"] = (
            "SYSTEMIC regime is active. All signal types are suppressed by the regime filter. "
            "This is expected behavior during broad market stress — the system is correctly "
            "blocking trades that historically lose money in this environment."
        )
    elif regime.regime_id == "NEUTRAL":
        details["regime_implication"] = (
            "NEUTRAL regime is active. CRYPTO_LEADS signals are actionable (82% hit rate, "
            "+8.24% avg return). Other signal types remain filtered."
        )
    else:
        details["regime_implication"] = (
            f"{regime.regime_id} regime is active. Signal reliability varies by type — "
            "check the filtered signals endpoint for per-signal classifications."
        )

    # Filtered signals summary
    details["actionable_signals"] = filtered.get("actionableCount", 0)
    details["suppressed_signals"] = filtered.get("suppressedCount", 0)
    details["total_signals"] = filtered.get("totalSignals", 0)

    if not messages:
        if regime.regime_id == "SYSTEMIC":
            messages.append(
                "Regime engine is operating correctly. SYSTEMIC regime detected — "
                "all signals suppressed as expected. This is a safe-halt state, not a malfunction."
            )
        else:
            messages.append("Regime engine is operating normally.")

    return {
        "state": state,
        "message": " ".join(messages),
        "details": details,
    }


def check_granger_pipeline(reliability):
    """Evaluate the Granger causality signal pipeline."""
    state = "HEALTHY"
    details = {}
    messages = []

    # Count decaying types
    decaying = []
    type_status = {}
    for sig_type, sig in reliability.types.items():
        is_dec = sig.is_decaying
        drop = sig.drop_pct
        type_status[sig_type] = {
            "label": sig.label,
            "score": sig.score,
            "all_time_score": sig.all_time_score,
            "current_rolling": sig.current_rolling,
            "drop_pct": round(drop, 1),
            "is_decaying": is_dec,
            "freshness": sig.freshness,
        }
        if is_dec:
            decaying.append(sig_type)

    details["signal_types"] = type_status
    details["decaying_count"] = len(decaying)
    details["decaying_types"] = decaying

    # CRYPTO_LEADS specific (primary tradeable signal)
    cl = reliability.types.get("CRYPTO_LEADS")
    if cl:
        details["primary_signal_drop"] = round(cl.drop_pct, 1)
        details["primary_signal_freshness"] = cl.freshness

    # Determine state
    if len(decaying) >= DECAY_TYPES_STOP:
        state = "HALT"
        messages.append(
            f"{len(decaying)} of 3 signal types are decaying ({', '.join(decaying)}). "
            "This means the historical correlations that power the trading signals have "
            "weakened significantly. The system is correctly halting to prevent trades "
            "based on unreliable statistical relationships."
        )
    elif len(decaying) >= DECAY_TYPES_WARN:
        state = "DEGRADED"
        messages.append(
            f"{len(decaying)} signal type is decaying ({', '.join(decaying)}). "
            "The primary signal pipeline is partially degraded. Proceed with caution."
        )
    else:
        messages.append("All signal types are within normal reliability ranges.")

    # CRYPTO_LEADS drop
    if cl and cl.drop_pct >= CRYPTO_LEADS_DROP_STOP:
        state = "HALT"
        messages.append(
            f"CRYPTO_LEADS (the primary tradeable signal) has dropped {cl.drop_pct:.1f}% "
            f"from its all-time score. This exceeds the {CRYPTO_LEADS_DROP_STOP}% halt "
            "threshold. The semi-to-crypto lead-lag relationship that underpins the core "
            "trading edge may no longer be intact."
        )
    elif cl and cl.drop_pct >= CRYPTO_LEADS_DROP_WARN:
        if state == "HEALTHY":
            state = "DEGRADED"
        messages.append(
            f"CRYPTO_LEADS has dropped {cl.drop_pct:.1f}% from its all-time score. "
            "The primary signal is weakening — reduce position sizes."
        )

    # Regime alert
    alert = reliability.regime_alert
    if alert.get("triggered"):
        state = "HALT"
        details["regime_alert"] = True
        messages.append(
            "Regime alert triggered — multiple signal types decaying simultaneously "
            "suggests a structural shift in market correlations."
        )
    else:
        details["regime_alert"] = False

    if state == "HEALTHY":
        messages = ["Signal pipeline is healthy. All Granger-validated correlations are within expected ranges."]

    return {
        "state": state,
        "message": " ".join(messages),
        "details": details,
    }


def check_circuit_breaker(health):
    """Evaluate the circuit breaker / system health layer."""
    state = "HEALTHY"
    details = {}
    messages = []

    details["api_status"] = health.status
    details["uptime_seconds"] = health.uptime
    details["data_age_seconds"] = health.data_age_sec
    details["is_stale"] = health.is_stale
    details["last_error"] = health.last_error
    details["refresh_count"] = health.refresh_count
    details["schema_version"] = health.schema_version

    # API status
    if health.status == "warming":
        state = "HALT"
        messages.append(
            "The API is still starting up and loading its initial data cache. "
            "This typically takes 30-60 seconds after a restart. Wait and retry."
        )
    elif health.status != "ok":
        state = "DEGRADED"
        messages.append(f"API status is '{health.status}' (expected 'ok').")

    # Data freshness
    age = health.data_age_sec or 0
    if age > STALE_CRITICAL_SEC:
        state = "HALT"
        messages.append(
            f"Data is {age} seconds old (>{STALE_CRITICAL_SEC}s threshold). "
            "The API has not refreshed recently — signals may be outdated. "
            "This could indicate a Puppeteer extraction failure or upstream issue."
        )
    elif age > STALE_WARNING_SEC:
        if state == "HEALTHY":
            state = "DEGRADED"
        messages.append(
            f"Data is {age} seconds old (>{STALE_WARNING_SEC}s). "
            "One refresh cycle may have been missed."
        )

    # Staleness flag
    if health.is_stale:
        state = "HALT"
        messages.append("Server-side staleness flag is set — data is confirmed outdated.")

    # Last error
    if health.last_error:
        if state == "HEALTHY":
            state = "DEGRADED"
        messages.append(f"Last refresh error: {health.last_error[:100]}")

    if not messages:
        messages.append(
            "Circuit breaker is operational. API is responding, data is fresh, "
            "and no errors have been recorded."
        )

    return {
        "state": state,
        "message": " ".join(messages),
        "details": details,
    }


# ── Aggregate ───────────────────────────────────────────────────────────────

SEVERITY = {"HEALTHY": 0, "DEGRADED": 1, "HALT": 2}


def aggregate_health(components):
    """Determine overall health from component states."""
    states = [c["state"] for c in components.values()]
    max_sev = max(SEVERITY[s] for s in states)
    return ["HEALTHY", "DEGRADED", "HALT"][max_sev]


def build_summary(overall, components):
    """Build a one-sentence summary a first-time builder can understand."""
    if overall == "HEALTHY":
        return (
            "All subsystems are operational. The signal pipeline is safe to integrate against. "
            "Signals are fresh and the statistical foundation is intact."
        )
    elif overall == "DEGRADED":
        degraded = [name for name, c in components.items() if c["state"] == "DEGRADED"]
        return (
            f"Warning conditions detected in: {', '.join(degraded)}. "
            "The system is still operational but signals may be less reliable than normal. "
            "Proceed with caution and reduced position sizes if trading."
        )
    else:
        halted = [name for name, c in components.items() if c["state"] == "HALT"]
        return (
            f"Signal integrity issue detected in: {', '.join(halted)}. "
            "This is a safe halt — the system is correctly preventing reliance on potentially "
            "unreliable signals. This does NOT mean the infrastructure is broken. It means "
            "current market conditions or data quality do not meet the thresholds required "
            "for confident signal generation. Check component messages for details."
        )


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # Parse args
    out_path = None
    api_url = os.environ.get("PF_API_URL", "http://localhost:8080")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--out="):
            out_path = arg.split("=", 1)[1]
        elif arg == "--out" and i < len(sys.argv) - 1:
            out_path = sys.argv[i + 1]
        elif arg.startswith("--url="):
            api_url = arg.split("=", 1)[1]

    if not out_path:
        out_path = os.path.join(os.path.dirname(__file__), "status.json")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Query live API
    client = RegimeClient(base_url=api_url, timeout=15)

    try:
        health = client.get_health()
        reliability = client.get_signal_scores()
        regime = client.get_regime_state()
        filtered_raw = client._request("/signals/filtered")
    except Exception as e:
        # API unreachable — write a HALT status
        status = {
            "schema": "pf-system-status/v1",
            "generated_at": ts,
            "overall_health": "HALT",
            "summary": (
                "The Signal Intelligence API is currently unreachable. "
                "This means health checks cannot be performed and signals are unavailable. "
                f"Error: {type(e).__name__}: {e}"
            ),
            "components": {
                "regime_engine": {
                    "state": "HALT",
                    "message": "Cannot evaluate — API unreachable.",
                    "details": {},
                },
                "granger_pipeline": {
                    "state": "HALT",
                    "message": "Cannot evaluate — API unreachable.",
                    "details": {},
                },
                "circuit_breaker": {
                    "state": "HALT",
                    "message": f"API unreachable: {type(e).__name__}: {e}",
                    "details": {"api_status": "unreachable"},
                },
            },
            "how_to_read_this": {
                "HEALTHY": "All subsystems operational — safe to integrate and trade against signals.",
                "DEGRADED": "Warning conditions present — signals available but may be less reliable. Proceed with caution.",
                "HALT": "Signal integrity issue detected — the system is safely blocking unreliable signals. This is protective behavior, not a crash. Check component messages for explanation.",
            },
            "source": "rfLJ4ZRnqmGFLAcMvCD56nKGbjpdTJmMqo",
            "api_url": "http://84.32.34.46:8080",
            "repo": "https://github.com/sendonanawitakeshi/post-fiat-signals",
        }
        with open(out_path, "w") as f:
            json.dump(status, f, indent=2)
        print(f"[HALT] API unreachable — wrote {out_path}")
        return

    # Run component checks
    regime_check = check_regime_engine(regime, filtered_raw)
    pipeline_check = check_granger_pipeline(reliability)
    breaker_check = check_circuit_breaker(health)

    components = {
        "regime_engine": regime_check,
        "granger_pipeline": pipeline_check,
        "circuit_breaker": breaker_check,
    }

    overall = aggregate_health(components)
    summary = build_summary(overall, components)

    status = {
        "schema": "pf-system-status/v1",
        "generated_at": ts,
        "overall_health": overall,
        "summary": summary,
        "components": components,
        "how_to_read_this": {
            "HEALTHY": "All subsystems operational — safe to integrate and trade against signals.",
            "DEGRADED": "Warning conditions present — signals available but may be less reliable. Proceed with caution.",
            "HALT": "Signal integrity issue detected — the system is safely blocking unreliable signals. This is protective behavior, not a crash. Check component messages for explanation.",
        },
        "source": "rfLJ4ZRnqmGFLAcMvCD56nKGbjpdTJmMqo",
        "api_url": "http://84.32.34.46:8080",
        "repo": "https://github.com/sendonanawitakeshi/post-fiat-signals",
    }

    with open(out_path, "w") as f:
        json.dump(status, f, indent=2)

    print(f"[{overall}] Wrote {out_path}")
    print(f"  Regime Engine:    {regime_check['state']}")
    print(f"  Granger Pipeline: {pipeline_check['state']}")
    print(f"  Circuit Breaker:  {breaker_check['state']}")
    print(f"  Summary: {summary[:120]}...")


if __name__ == "__main__":
    main()
