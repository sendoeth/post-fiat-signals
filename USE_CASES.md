# Use Cases

Three trading personas, three different ways to use the SDK. Each snippet is self-contained — paste it into your bot, set `PF_API_URL`, and it runs.

---

## 1. Regime-Gated Bot Operator

You run an automated crypto trading bot. It fires trades based on your own signals — momentum, mean reversion, whatever. The problem: it doesnt know when to sit out. During SYSTEMIC regimes, all your crypto signals get polluted by correlated selloffs. During EARNINGS rotation, sector noise drowns out real divergences. You need a binary gate — trade or dont trade — before your bot even looks at its own signals.

This snippet calls `/regime/current` and `/signals/filtered` to produce a single EXECUTE or WAIT verdict. If the regime is hostile or CRYPTO_LEADS is suppressed, your bot skips the cycle. Only NEUTRAL regime with at least one ACTIONABLE signal returns EXECUTE. The 7-gate logic is the same decision tree from the [regime scanner](examples/regime_scanner.py), compressed into a pre-trade check you can call from any bot framework.

```python
from pf_regime_sdk import RegimeClient

client = RegimeClient(base_url="http://your-node:8080")

state = client.get_regime_state()
if state.regime_type != "NEUTRAL" or state.confidence_score < 50:
    print(f"WAIT — regime={state.regime_type}, confidence={state.confidence_score}")
    raise SystemExit(0)

report = client.get_filtered_signals()
actionable = [s for s in report.actionable_signals
              if s.signal_type == "CRYPTO_LEADS" and s.regime_filter_hit_rate > 0.65]

if not actionable:
    print(f"WAIT — {report.actionable_count} actionable but none meet CRYPTO_LEADS + 65% threshold")
    raise SystemExit(0)

for sig in actionable:
    print(f"EXECUTE {sig.pair}: hit={sig.regime_filter_hit_rate:.0%}, "
          f"avg_ret={sig.regime_filter_avg_ret:+.2f}%, conv={sig.conviction}")
```

---

## 2. Decay-Aware Position Sizer

You already have entry signals. What you dont have is a way to scale position size based on how trustworthy those signals are right now. Signal reliability decays — a strategy that hit 82% last month might be running at 50% this week because the underlying lead-lag relationship weakened. You need your position sizing to reflect current signal health, not historical backtests.

This snippet calls `/signals/reliability` and uses the decay percentage to scale a base position size. Fresh signals (no decay) get full size. Aging signals (10-30% decay) get half size. Stale signals (30%+ decay) get quarter size. If 2 or more signal types are decaying simultaneously, the regime alert fires and all positions get cut to minimum. The thresholds come from the [watchdog](examples/watchdog.py) circuit breaker calibration.

```python
from pf_regime_sdk import RegimeClient

client = RegimeClient(base_url="http://your-node:8080")
BASE_SIZE = 1000  # dollars

reliability = client.get_signal_scores()
decaying_count = sum(1 for t in reliability.types.values() if t.is_decaying)

if decaying_count >= 2:
    print(f"REGIME ALERT — {decaying_count} types decaying, minimum size only")
    size = BASE_SIZE * 0.10
else:
    crypto = reliability.types.get("CRYPTO_LEADS")
    if not crypto:
        size = BASE_SIZE * 0.25
    elif crypto.drop_pct < 10:
        size = BASE_SIZE  # fresh — full size
    elif crypto.drop_pct < 30:
        size = BASE_SIZE * 0.50  # aging — half size
    else:
        size = BASE_SIZE * 0.25  # stale — quarter size
    print(f"CRYPTO_LEADS: score={crypto.score}, drop={crypto.drop_pct:.0f}%, "
          f"freshness={crypto.freshness} → size=${size:.0f}")
```

---

## 3. Regime Shift Alert Monitor

You dont trade automatically — you manage a portfolio manually and need to know when the market regime changes so you can adjust exposure. You dont want to stare at dashboards. You want a script that runs on a cron, checks if the regime shifted since last run, and sends you an alert with the new regime, how many transitions happened recently, and what the rebalancing queue looks like.

This snippet calls `/regime/history` to detect recent transitions and `/rebalancing/queue` to get the current trade instructions. It compares the current regime against a locally cached state and only alerts on changes. Wire the `send_alert()` call to Telegram, Discord webhook, email, or whatever your notification stack uses.

```python
from pf_regime_sdk import RegimeClient
import json, os

client = RegimeClient(base_url="http://your-node:8080")
CACHE = "/tmp/pf_last_regime.json"

history = client.get_regime_history()
prev = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

if history.current_regime != prev.get("regime"):
    queue = client.get_rebalance_queue()
    urgent = [t for t in queue.trades if t.urgency == "immediate"]
    msg = (f"REGIME SHIFT: {prev.get('regime','?')} → {history.current_regime} | "
           f"{history.transition_count} transitions in {history.window_days}d | "
           f"{len(urgent)} urgent trades")
    print(msg)  # replace with send_alert(msg)
    for t in urgent:
        print(f"  {t.direction} {t.asset}: {t.delta_pct:+.1f}% ({t.urgency_label})")
    json.dump({"regime": history.current_regime}, open(CACHE, "w"))
else:
    print(f"No change — still {history.current_regime}")
```
