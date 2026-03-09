# pf-regime-sdk

Python SDK for the Post Fiat Signal Intelligence API. Connects to a regime-aware semi-crypto divergence engine and returns structured signals with backtested hit rates, regime filters, and rebalancing instructions.

Zero external dependencies. Python 3.10+.

## Install

```bash
git clone https://github.com/postfiatorg/pf-regime-sdk.git
cd pf-regime-sdk
```

No pip install required. The SDK is a single package with no dependencies beyond the Python standard library.

## Quickstart

```python
from pf_regime_sdk import RegimeClient

client = RegimeClient(base_url="http://your-node:8080")

# Get regime-filtered signals with EXECUTE/WAIT classification
report = client.get_filtered_signals()

for signal in report.actionable_signals:
    print(f"{signal.pair}: {signal.regime_filter} "
          f"hit={signal.regime_filter_hit_rate:.0%} "
          f"avg_ret={signal.regime_filter_avg_ret:+.2f}%")
```

Or run the full decision engine:

```bash
export PF_API_URL=http://your-node:8080
python3 examples/regime_scanner.py
```

The scanner maps every signal to a binary **EXECUTE** or **WAIT** decision using the backtested decision tree (see below).

## Decision Logic

The scanner implements a 7-gate decision tree. A signal must pass all gates to reach EXECUTE:

| Gate | Check | If Failed |
|------|-------|-----------|
| 1 | Regime = SYSTEMIC? | WAIT — all signals suppressed |
| 2 | Regime != NEUTRAL? | WAIT — signals ambiguous |
| 3 | Type = SEMI_LEADS? | WAIT — anti-signal (12% hit rate) |
| 4 | Type != CRYPTO_LEADS? | WAIT — ambiguous expectancy |
| 5 | Filter != ACTIONABLE? | WAIT — regime filter says no |
| 6 | Hit rate < 65%? | WAIT — degraded below threshold |
| 7 | Reliability decaying? | WAIT — signal going stale |

The single actionable setup from 264 trading days of backtesting:
- **NEUTRAL regime + CRYPTO_LEADS type**: 82% hit rate, +8.24% avg 14d return, n=17

Everything else is WAIT. SEMI_LEADS under NEUTRAL is a documented anti-signal (12% hit rate, -14.60% avg return).

## Data Contract

**Schema version**: `v1.1.0`

**Refresh interval**: Signals refresh every 15 minutes. The `dataAgeSec` field in every response tells you how many seconds since the last refresh. Data older than 30 minutes is flagged `isStale: true`.

**Regime states**: `NEUTRAL`, `SYSTEMIC`, `DIVERGENCE`, `EARNINGS`. Regime classification is computed from signal reliability decay patterns across a configurable window (default 30 trading days).

**Signal types**: `SEMI_LEADS`, `CRYPTO_LEADS`, `FULL_DECOUPLE`. Each signal is classified per-regime as `ACTIONABLE`, `SUPPRESS`, or `AMBIGUOUS` with a backtested hit rate and sample size.

**Response guarantees**:
- All endpoints return JSON with `Content-Type: application/json`
- CORS headers are set (`Access-Control-Allow-Origin: *`)
- `503` during warmup (first ~30s after API start, cache loading)
- All numeric fields are deterministic for a given data window
- No authentication required

**Breaking changes**: Schema version bumps from `1.x` to `2.x` indicate breaking changes. Minor bumps (`1.0` to `1.1`) add fields but never remove or rename existing ones.

## API Endpoints

| Endpoint | Returns | SDK Method |
|----------|---------|------------|
| `/regime/current` | Current regime state, confidence, signal breakdown | `get_regime_state()` |
| `/rebalancing/queue` | Prioritized trade instructions with urgency tiers | `get_rebalance_queue()` |
| `/signals/reliability` | Signal reliability scores with decay status | `get_signal_scores()` |
| `/signals/filtered` | Regime-conditional signal filter (ACTIONABLE/SUPPRESS/AMBIGUOUS) | `get_filtered_signals()` |
| `/regime/history` | 90-day regime transition timeline | `get_regime_history()` |
| `/health` | Server health, uptime, data freshness | `get_health()` |

## Error Handling

```python
from pf_regime_sdk import RegimeClient, ConnectionError, StaleDataError, WarmingError

client = RegimeClient(
    base_url="http://your-node:8080",
    timeout=15,
    max_retries=3,
    raise_on_stale=True,  # raise StaleDataError when data is old
)

try:
    state = client.get_regime_state()
except ConnectionError:
    print("API unreachable")
except WarmingError:
    print("API still loading — retry in 30s")
except StaleDataError as e:
    print(f"Data is {e.data_age_sec}s old — may be stale")
```

The client automatically retries on 503 and 5xx errors with exponential backoff (1s, 2s, 4s).

## Configuration

The SDK reads `PF_API_URL` from the environment by default in the example scripts. The `RegimeClient` constructor accepts `base_url` directly:

```python
# From environment
import os
client = RegimeClient(base_url=os.environ.get("PF_API_URL", "http://localhost:8080"))

# Direct
client = RegimeClient(base_url="http://your-node:8080")
```

## License

MIT
