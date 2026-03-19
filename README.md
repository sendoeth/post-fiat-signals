# pf-regime-sdk

Python SDK for the Post Fiat Signal Intelligence API. Connects to a regime-aware semi-crypto divergence engine and returns structured signals with backtested hit rates, regime filters, and rebalancing instructions.

Zero external dependencies. Python 3.10+.

> **New here?** Start with **[QUICKSTART.md](QUICKSTART.md)** — clone to first signal output in under 5 minutes, no live API needed. Then read [PRODUCT.md](PRODUCT.md) for the full value proposition, or jump to [USE_CASES.md](USE_CASES.md) for three ready-to-paste code snippets.

## System Health

Check whether the signal stack is safe to integrate against before you start:

**[`status.json`](status.json)** — current health state of all subsystems (regime engine, Granger pipeline, circuit breaker). Auto-updated every 15 minutes via cron (`update_status.sh`). States: `HEALTHY` (safe to integrate), `DEGRADED` (proceed with caution), `HALT` (signals blocked — protective behavior, not a crash). Each component includes a human-readable message explaining the current condition. Check the `generated_at` timestamp to confirm freshness.

Live endpoint: `GET http://<your-server-ip>:8080/system/status` — same schema, real-time. Replace `<your-server-ip>` with the IP of the node running `signal_api.js`.

## Regime Transition Forecast

`GET /regime/current` includes a `transitionForecast` object that estimates when the current SYSTEMIC regime will end. Uses velocity-based forward projection with historical calibration from observed SYSTEMIC periods. Outputs confidence bands (optimistic/base/pessimistic), per-type recovery requirements, and backtest validation against past transitions. See [`docs/TRANSITION_FORECAST.md`](docs/TRANSITION_FORECAST.md) for the full algorithm, field reference, and consumer usage patterns.

## Hit Rate Decay Model

`GET /signals/filtered` and `GET /regime/current` include a `hitRateDecayModel` object that applies duration-conditioned exponential decay to signal hit rates under SYSTEMIC regime. Static aggregate hit rates are duration-blind — they overstate the current hit probability during extended SYSTEMIC periods. The decay model corrects this by computing `adjustedConfidence` per signal type using calibrated half-lives (CRYPTO_LEADS ~4.2d, FULL_DECOUPLE ~8.5d, SEMI_LEADS ~32d). Each signal also carries per-signal `adjustedConfidence`, `decayHalfLifeDays`, and `daysToNoise` fields. Includes sensitivity bands (±30% half-life), backtest calibration table, and aggregate bias detection. See [`docs/HIT_RATE_DECAY.md`](docs/HIT_RATE_DECAY.md) for the full model derivation, API field reference, and b1e55ed SPI mapping.

## Capital Preservation Score

`GET /signals/filtered` and `GET /regime/current` include a `capitalPreservation` object that quantifies how much capital was saved by each NO_TRADE decision during SYSTEMIC. Uses cross-regime return decomposition (solving for per-type win/loss returns from NEUTRAL and SYSTEMIC data as simultaneous equations) combined with duration-decayed hit rates to compute counterfactual expected loss per entry. Includes an inverse signal analysis showing that CRYPTO_LEADS short positions become mathematically viable after ~8 days of SYSTEMIC (with n=5 caveat). See [`docs/CAPITAL_PRESERVATION.md`](docs/CAPITAL_PRESERVATION.md) for the full model derivation, decomposed return table, API field reference, and b1e55ed attribution mapping.

## Optimal Re-Entry Timing

`GET /signals/filtered` and `GET /regime/current` include an `optimalReEntry` object that synthesizes all 4 upstream diagnostic modules (proximity, transition forecast, hit rate decay, capital preservation decomposition) into a prescriptive expected-value surface. Core equation: `E[R|d] = P(NEUTRAL|d) * E[R|NEUTRAL] + P(SYSTEMIC|d) * [h_adj(d) * R_win + (1-h_adj(d)) * R_loss]`. Outputs per-type EV curves, crossover day (when expected return exceeds risk-free rate), Kelly fractions with serial correlation adjustment, entry threshold, and sensitivity bands. CRYPTO_LEADS crosses first due to fastest confidence recovery. Net bias direction: PREMATURE (3 of 4 upstream biases push re-entry earlier — treat as lower bound). See [`docs/OPTIMAL_REENTRY.md`](docs/OPTIMAL_REENTRY.md) for the full equation derivation, field reference, upstream dependency diagram, and consumer code example.

## Forward-Test Audit Dashboard

**[Live Dashboard](https://sendoeth.github.io/validator/audit.html)** — 6-panel real-time monitoring surface polling the live API every 60 seconds. Shows decision status, regime proximity gradient, ledger accumulation progress (7-day milestone), system health, regime timeline, and consumer activity. Single-file HTML, zero dependencies. See [`docs/AUDIT_DASHBOARD.md`](docs/AUDIT_DASHBOARD.md) for full documentation.

## Install

```bash
git clone https://github.com/sendoeth/post-fiat-signals.git
cd post-fiat-signals
```

No pip install required. The SDK is a single package with no dependencies beyond the Python standard library.

## Try It Locally

Run every example against a built-in mock server — no live API needed:

```bash
# 1. Start the mock server (serves all 6 endpoints with realistic test data)
python3 examples/mock_server.py &

# 2. Point the SDK at the mock
export PF_API_URL=http://localhost:8080

# 3. Run any example
python3 examples/full_pipeline_demo.py  # full 3-stage pipeline (recommended)
python3 examples/regime_scanner.py      # 7-gate EXECUTE/WAIT decision engine
python3 examples/watchdog.py            # circuit breaker integrity check
```

The mock server returns plausible NEUTRAL-regime data with 2 ACTIONABLE signals (NVDA/RNDR, AMD/TAO), so the pipeline will output EXECUTE_REDUCED (2 execute, 3 wait), the scanner will output EXECUTE, and the watchdog will return DEGRADED. All three [USE_CASES.md](USE_CASES.md) snippets also work against the mock — paste them into a script, set `PF_API_URL`, and run.

The pipeline demo chains all three stages (watchdog → scanner → trade decision) into a single script and writes structured JSON to `pipeline_output.json`. See [PIPELINE_DEMO_REQUIREMENTS.md](PIPELINE_DEMO_REQUIREMENTS.md) for the full architecture spec.

See [CHANGELOG.md](CHANGELOG.md) for version history.

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
- **NEUTRAL regime + CRYPTO_LEADS type**: 82% hit rate, +8.24% avg 14d return, n=22

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
| `/system/status` | Public system health surface — overall + per-component health with explanations | — |

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

## Safety & Validation

**Always run the watchdog before opening positions.** The regime scanner tells you WHAT to trade. The watchdog tells you WHETHER the signals are still trustworthy.

```bash
export PF_API_URL=http://your-node:8080
python3 examples/watchdog.py

# use exit code in scripts
python3 examples/watchdog.py && python3 examples/regime_scanner.py
```

The watchdog implements a **circuit breaker** pattern with three verdict levels:

| Verdict | Exit Code | Meaning |
|---------|-----------|---------|
| `VALID` | 0 | All checks pass — safe to trade |
| `DEGRADED` | 1 | Warning conditions — proceed with caution, reduce size |
| `STOP` | 2 | Signal integrity compromised — do not open positions |

Three independent health dimensions are checked:

| Check | What It Measures | DEGRADED When | STOP When |
|-------|-----------------|---------------|-----------|
| System Health | API status, data freshness, staleness | Data age > 15min, or last error present | Data age > 30min, API warming, or stale flag set |
| Signal Fidelity | Decay status across signal types, CRYPTO_LEADS drop % | 1 type decaying, or CRYPTO_LEADS dropped 20%+ | 2+ types decaying, CRYPTO_LEADS dropped 40%+, or regime alert triggered |
| Regime Confidence | Classifier confidence, alert status, backtest accuracy | Confidence below 50, alert active, or FP rate > 50% | (rolls up from sub-checks) |

**Workflow:**

The recommended way to run the full pipeline is the demo script, which chains all three stages automatically:

```bash
python3 examples/full_pipeline_demo.py && python3 my_bot.py
```

Or run the stages individually:

1. Run `watchdog.py` — if STOP, do not trade. If DEGRADED, reduce position sizes.
2. Run `regime_scanner.py` — if WAIT, no actionable setup exists.
3. Only if watchdog returns VALID and scanner returns EXECUTE do you have a full-conviction position.

The baselines come from 264 trading days of backtesting. Models drift. The watchdog catches that drift before it costs money. A VALID verdict means the statistical foundation (Granger-validated semi-leads-crypto at 1h-72h lag, 82% hit rate under NEUTRAL) is still intact. A STOP verdict means something has shifted and the historical edge may no longer apply.

**If the live API returns STOP**: this is expected protective behavior during the current SYSTEMIC regime, not a malfunction. See [`docs/STOP_STATE_DIAGNOSTIC.md`](docs/STOP_STATE_DIAGNOSTIC.md) for the full root cause analysis. Use the mock server to test the HEALTHY and DEGRADED paths locally.

**Testing**: 15 end-to-end integration tests cover the full pipeline across HEALTHY, DEGRADED, and HALT states. See [`TESTING.md`](TESTING.md) for results and what each scenario proves.

## Performance Ledger

**[`performance_log.json`](performance_log.json)** — forward-testing proof layer. Logs every pipeline decision in real time: NO_TRADE entries during STOP regimes (capital preservation evidence) and EXECUTE entries with live prices during actionable regimes (with automated 14-day outcome evaluation). Auto-updated every 15 minutes via cron (`update_ledger.sh`).

Each entry includes SPI-aligned fields (`ticker`, `action`, `confidence`, `horizon_hours`) for direct consumption by downstream systems like the b1e55ed attribution pipeline. Entries are deduplicated by 15-min cycle key and written atomically.

See **[`LEDGER_SCHEMA.md`](LEDGER_SCHEMA.md)** for the full schema reference — field definitions, evaluation lifecycle, summary object spec, and b1e55ed SPI field mapping.

## b1e55ed Integration

**[`INTEGRATION_B1E55ED.md`](INTEGRATION_B1E55ED.md)** — drop-in producer for b1e55ed's event-sourced engine. Copy `integration/regime_scanner_producer.py` to their `engine/producers/` directory, set `PF_REGIME_API_URL`, and it auto-discovers via `@register`. Emits `SIGNAL_TRADFI_V1` events from our `/signals/filtered` API and `FORECAST_V1` events via a regime-aware interpreter. Includes regime state mapping (NEUTRAL→BULL, SYSTEMIC→CRISIS) and signal classification (CRYPTO_LEADS→long, SEMI_LEADS→abstain).

## Builder Validation

**[`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)** — structured results from the first builder validation loop. Documents friction points found during a zero-assistance quickstart attempt, fixes shipped from the feedback, and external builder outreach status. Updated as external responses arrive.

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
