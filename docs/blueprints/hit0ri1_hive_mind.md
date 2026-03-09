# Integration Blueprint: Regime Signals → hit0ri1 Hive Mind Pipeline

**Target**: [hit0ri1](https://github.com/hit0ri1) — PFT validator-linked yield coordination stack
**SDK**: [post-fiat-signals](https://github.com/sendonanawitakeshi/post-fiat-signals) v0.3.0
**Date**: 2026-03-09

---

## 1. Builder Profile

hit0ri1 is building a complete off-chain coordination stack for Post Fiat validator operators managing yield positions. 12 repos, all Python, all stdlib (except the treasury monitor which uses web3). The pipeline:

```
Protocol APIs (Pendle/Morpho/Maple/HLP)
        |
        v
  Yield Sentinel (pf_yield_sentinel.py)
  - 4 protocol adapters, 6 risk rules
  - emits: pf-signal-v1 alerts (APY_DRIFT, TVL_DECLINE, UTILIZATION_SPIKE, etc.)
        |
        v
  Hive Mind (hive_mind.py)
  - 5-rule Decision Matrix
  - routes to: AUTOMATED_REBALANCE / VALIDATOR_VOTE / ALERT_ONLY / MONITOR
        |
        v
  Signal Consumer (signal_consumer.py)
  - 3 schema validators (pf-signal-v1, pf-validator-signal-v1, pf-task-v1)
  - severity-based routing → MockNodeState
        |
        v
  [STUB] → Post Fiat Task Network
```

**Decision points that need regime data**:

1. **Hive Mind rule #3** (`critical_immediate_action`): triggers AUTOMATED_REBALANCE on CRITICAL + actionable type + position ≤ $2K. Currently has no market regime awareness — fires during SYSTEMIC regimes when correlations break down and rebalancing destroys value.

2. **Yield Sentinel circuit breakers**: APY collapse >50%, TVL collapse >40%. No external signal integrity check — doesnt know if the underlying lead-lag relationship between semis and crypto has shifted.

3. **NAVCoin Treasury Monitor**: manages $50K across Pendle/Morpho positions. Rebalancing decisions happen in a vacuum — no regime context to suppress trades during SYSTEMIC or amplify during NEUTRAL/CRYPTO_LEADS.

4. **Signal Consumer routing**: routes CRITICAL signals to action handlers unconditionally. A regime gate before the router would prevent automated actions during hostile market regimes.

---

## 2. Integration Map

| hit0ri1 Component | Decision Point | SDK Endpoint | What It Adds |
|---|---|---|---|
| Hive Mind rule #3 | AUTOMATED_REBALANCE trigger | `/regime/current` via `get_regime_state()` | Suppress rebalance when regime ≠ NEUTRAL. SYSTEMIC = force ALERT_ONLY regardless of severity. |
| Hive Mind rule #1 | stale_data_guard | `/health` via `get_health()` | Add signal-pipeline staleness to existing stale_data check. If regime data is stale, treat all signals as stale. |
| Yield Sentinel circuit breakers | APY/TVL collapse | `/signals/filtered` via `get_filtered_signals()` | 4th circuit breaker: REGIME_ALERT. If regime filter = SUPPRESS on current signal type, block the trade. |
| Signal Consumer router | severity routing | `/signals/reliability` via `get_signal_scores()` | Pre-route check: if CRYPTO_LEADS reliability is decaying, downgrade CRITICAL → WARNING before routing. |
| NAVCoin Treasury Monitor | rebalance decisions | `/rebalancing/queue` via `get_rebalance_queue()` | Replace mock rebalancing logic with backtested rebalancing queue. Priority tiers align with treasury position sizes. |

---

## 3. Code Snippet: Regime Gate for Hive Mind

Drop this into `hive_mind.py` as a new rule in the Decision Matrix, priority 0 (before `stale_data_guard`). Zero new deps — the SDK is pure stdlib.

```python
"""
regime_gate.py — Regime-aware gate for hit0ri1's Hive Mind Decision Matrix

Plugs into hive_mind.py as rule priority 0 (before stale_data_guard).
Pulls live regime state + signal integrity from the post-fiat-signals API.
If regime is hostile or signals are degraded, forces ALERT_ONLY regardless
of downstream severity.

Setup:
  git clone https://github.com/sendonanawitakeshi/post-fiat-signals.git
  export PF_REGIME_API=http://<node-ip>:8080
"""

import os
import sys
import logging
from dataclasses import dataclass

# --- SDK import (add post-fiat-signals to PYTHONPATH or copy pf_regime_sdk/) ---
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "post-fiat-signals"))
from pf_regime_sdk import RegimeClient
from pf_regime_sdk.exceptions import (
    ConnectionError as RegimeConnError,
    TimeoutError as RegimeTimeout,
    StaleDataError,
    WarmingError,
)

logger = logging.getLogger("regime_gate")

# --- thresholds (calibrated from 264-day backtest) ---
HOSTILE_REGIMES = {"SYSTEMIC", "DIVERGENCE", "EARNINGS"}
MIN_CONFIDENCE = 50
CRYPTO_LEADS_DECAY_THRESHOLD = 0.40  # 40% drop = STOP
MAX_DATA_AGE_SEC = 1800              # 30 min


@dataclass
class RegimeVerdict:
    allow_automated: bool
    regime: str
    confidence: float
    reason: str


def check_regime(api_url: str = None) -> RegimeVerdict:
    """
    Returns a RegimeVerdict that the Hive Mind checks before routing.
    If allow_automated=False, force ALERT_ONLY on any signal.
    """
    url = api_url or os.environ.get("PF_REGIME_API", "http://localhost:8080")
    client = RegimeClient(base_url=url, timeout=10, max_retries=2)

    try:
        health = client.get_health()
        if health.data_age_sec > MAX_DATA_AGE_SEC or health.is_stale:
            return RegimeVerdict(
                allow_automated=False, regime="UNKNOWN",
                confidence=0, reason=f"regime data stale ({health.data_age_sec}s old)"
            )

        state = client.get_regime_state()
        if state.regime in HOSTILE_REGIMES:
            return RegimeVerdict(
                allow_automated=False, regime=state.regime,
                confidence=state.confidence,
                reason=f"regime={state.regime} — all signals suppressed"
            )
        if state.confidence < MIN_CONFIDENCE:
            return RegimeVerdict(
                allow_automated=False, regime=state.regime,
                confidence=state.confidence,
                reason=f"regime confidence {state.confidence} < {MIN_CONFIDENCE}"
            )

        # check signal integrity (optional — skip if filtered endpoint unavailable)
        try:
            filtered = client.get_filtered_signals()
            for sig in filtered.signals:
                if sig.signal_type == "CRYPTO_LEADS" and sig.regime_filter == "SUPPRESS":
                    return RegimeVerdict(
                        allow_automated=False, regime=state.regime,
                        confidence=state.confidence,
                        reason="CRYPTO_LEADS suppressed by regime filter"
                    )
        except Exception:
            pass  # filtered endpoint optional — dont block on it

        return RegimeVerdict(
            allow_automated=True, regime=state.regime,
            confidence=state.confidence, reason="NEUTRAL — signals valid"
        )

    except (RegimeConnError, RegimeTimeout) as e:
        return RegimeVerdict(
            allow_automated=False, regime="UNREACHABLE",
            confidence=0, reason=f"regime API unreachable: {e}"
        )
    except StaleDataError as e:
        return RegimeVerdict(
            allow_automated=False, regime="STALE",
            confidence=0, reason=f"regime data stale: {e}"
        )
    except WarmingError:
        return RegimeVerdict(
            allow_automated=False, regime="WARMING",
            confidence=0, reason="regime API still loading — retry in 30s"
        )


# --- Hive Mind integration ---
# In hive_mind.py, add this as rule priority 0 in the Decision Matrix:
#
#   from regime_gate import check_regime, RegimeVerdict
#
#   class DecisionMatrix:
#       def evaluate(self, signal: YieldSignal) -> Decision:
#           # Rule 0: regime gate (before stale_data_guard)
#           verdict = check_regime()
#           if not verdict.allow_automated:
#               return Decision(
#                   action="ALERT_ONLY",
#                   confidence=0.99,
#                   reason=f"REGIME_GATE: {verdict.reason}",
#                   rule="regime_gate"
#               )
#           # ... existing rules continue ...


if __name__ == "__main__":
    v = check_regime()
    status = "PASS" if v.allow_automated else "BLOCK"
    print(f"[{status}] regime={v.regime} confidence={v.confidence:.0f} — {v.reason}")
```

**Line count**: 78 lines (excluding comments/blanks). Pure stdlib + the SDK.

**What this does**: Before the Hive Mind evaluates any yield signal, `check_regime()` polls the regime API and returns a binary verdict. If the regime is hostile (SYSTEMIC/DIVERGENCE/EARNINGS), confidence is low, data is stale, or the API is unreachable, all signals get forced to ALERT_ONLY. Only when regime=NEUTRAL with valid signals does the Hive Mind proceed to its normal decision matrix. The 264-day backtest shows SYSTEMIC suppresses all signal types — this gate prevents automated rebalancing during exactly those periods.

---

## 4. Onboarding Steps

**Time to integrate**: ~5 minutes. Zero pip installs.

```bash
# 1. clone the SDK into your project
cd /path/to/PFT-Hive-Mind-Multi-Agent-Coordination-Logic
git clone https://github.com/sendonanawitakeshi/post-fiat-signals.git

# 2. copy the regime gate module
cp post-fiat-signals/regime_gate.py .
# (or use the snippet above — its self-contained)

# 3. set the API endpoint
export PF_REGIME_API=http://84.32.34.46:8080

# 4. test standalone
python3 regime_gate.py
# output: [PASS] regime=NEUTRAL confidence=72 — NEUTRAL — signals valid
# or:     [BLOCK] regime=SYSTEMIC confidence=85 — regime=SYSTEMIC — all signals suppressed

# 5. wire into hive_mind.py (3 lines)
# add to imports:
#   from regime_gate import check_regime
# add as first rule in DecisionMatrix.evaluate():
#   verdict = check_regime()
#   if not verdict.allow_automated:
#       return Decision(action="ALERT_ONLY", confidence=0.99,
#                       reason=f"REGIME_GATE: {verdict.reason}", rule="regime_gate")
```

**Whats running behind the API**:
- Semi/crypto correlation engine tracking 5 semis × 4 crypto AI tokens (20 pairs)
- Granger-validated lead-lag: semi stocks cause crypto AI tokens at 1-72h lag (20/20 daily pairs, p<0.001)
- Regime classifier: NEUTRAL/SYSTEMIC/DIVERGENCE/EARNINGS from 30d signal reliability decay patterns
- NEUTRAL + CRYPTO_LEADS = 82% hit rate, +8.24% avg 14d return (n=17, 264 trading days)
- 15-min cache refresh, circuit breaker watchdog, 36 integration tests, 18 stress test scenarios

**Data contract**: Schema v1.1.0. All responses are typed JSON. No auth required. No rate limits. SDK handles retry with exponential backoff on 5xx. See [README](https://github.com/sendonanawitakeshi/post-fiat-signals#readme) for full endpoint reference and [PRODUCT.md](https://github.com/sendonanawitakeshi/post-fiat-signals/blob/main/PRODUCT.md) for the research thesis.

---

*Built by [sendoeth](https://github.com/sendonanawitakeshi) for the Post Fiat network. The SDK is the handshake — other nodes consume signals programmatically, not through social media or personal brand.*
