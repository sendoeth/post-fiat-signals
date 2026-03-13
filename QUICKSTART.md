# Quickstart — Clone to First Signal in Under 5 Minutes

Zero-intervention first run. No API keys, no pip install, no live server needed.

**Prerequisites**: Python 3.10+ and git. Nothing else.

---

## Step 1: Clone the repo

```bash
git clone https://github.com/sendonanawitakeshi/post-fiat-signals.git
cd post-fiat-signals
```

Thats the entire install. The SDK is pure Python stdlib — no `requirements.txt`, no virtual env, no package manager.

## Step 2: Start the mock server

```bash
python3 examples/mock_server.py &
```

This launches a local HTTP server on port 8080 that serves realistic signal data for all 6 API endpoints. It simulates a NEUTRAL regime with 5 active signals (2 actionable CRYPTO_LEADS, 2 suppressed SEMI_LEADS, 1 ambiguous FULL_DECOUPLE). No internet connection required — everything runs locally.

> **Custom port**: If port 8080 is in use, run `python3 examples/mock_server.py --port 9090 &` and set `export PF_API_URL=http://localhost:9090` before step 3.

## Step 3: Run the full pipeline

```bash
python3 examples/full_pipeline_demo.py
```

This runs the complete 3-stage signal intelligence pipeline in one command. Heres what each stage does:

### Stage 1 — Watchdog (circuit breaker)

The pipeline starts with a pre-trade safety check. The watchdog polls the API and evaluates three independent health dimensions:

| Dimension | What it checks | When it fires STOP |
|-----------|---------------|-------------------|
| **System Health** | Is the API responsive? Is data fresh? | Data older than 30 min, API warming, stale flag set |
| **Signal Fidelity** | Are correlation signals decaying? How far has CRYPTO_LEADS dropped? | 2+ signal types decaying, CRYPTO_LEADS reliability dropped 40%+ |
| **Regime Confidence** | Is the regime classifier confident? Is the backtest still credible? | Rolls up from confidence, alert status, and false positive rate |

Each dimension returns VALID, DEGRADED, or STOP. If any dimension returns STOP, the pipeline halts immediately — the scanner never runs. This is the circuit breaker pattern: dont evaluate signals whose statistical foundation has eroded.

**What you should see**: The mock data has 1 of 3 signal types decaying (SEMI_LEADS), which triggers a DEGRADED verdict on signal fidelity. System health and regime confidence are both VALID. Overall watchdog verdict: **DEGRADED** (proceed with reduced size).

### Stage 2 — Regime Scanner (7-gate decision engine)

Because the watchdog didnt return STOP, the scanner runs. It pulls regime-classified signals and runs each through a 7-gate decision tree built from 264 trading days of backtesting:

| Gate | Question | If failed |
|------|---------|-----------|
| 1 | Is the regime SYSTEMIC? | WAIT — all signals suppressed |
| 2 | Is it NEUTRAL? | WAIT — only regime with positive-EV signals |
| 3 | Is the signal type SEMI_LEADS? | WAIT — anti-signal (12% hit rate, -14.60% avg return) |
| 4 | Is it CRYPTO_LEADS? | WAIT — only type with reliable edge |
| 5 | Is the regime filter ACTIONABLE? | WAIT — filter says no |
| 6 | Is hit rate above 65%? | WAIT — degraded below threshold |
| 7 | Is reliability stable? | WAIT — signal going stale |

Only one combination survives all 7 gates: **NEUTRAL regime + CRYPTO_LEADS type** (82% hit rate, +8.24% avg 14d return, n=22). Everything else is WAIT with a specific reason.

**What you should see**: 2 signals pass (NVDA/RNDR and AMD/TAO — both CRYPTO_LEADS under NEUTRAL). 3 signals are blocked — 2 SEMI_LEADS (anti-signal gate) and 1 FULL_DECOUPLE (wrong type gate).

### Stage 3 — Trade Decision (synthesis)

The pipeline combines the watchdog verdict with the scanner results:

| Watchdog | Scanner | Final Decision | Exit Code |
|----------|---------|---------------|-----------|
| VALID | EXECUTE signals found | **EXECUTE** | 0 |
| DEGRADED | EXECUTE signals found | **EXECUTE_REDUCED** | 1 |
| STOP | (skipped) | **NO_TRADE** | 2 |
| any | No EXECUTE signals | **NO_TRADE** | 1 or 2 |

**What you should see**: Watchdog is DEGRADED + scanner found 2 EXECUTE signals = **EXECUTE_REDUCED** (exit code 1). The pipeline writes structured output to `pipeline_output.json`.

## Step 4: Check the output

The pipeline writes machine-readable JSON alongside the CLI report:

```bash
cat pipeline_output.json
```

This file contains the full pipeline state — watchdog verdicts, scanner decisions per signal (with gate, reason, hit rate, avg return), and the overall trade recommendation. Use this for programmatic integration with your trading bot.

## Step 5: Stop the mock server

```bash
kill %1
```

---

## Expected Output

When you run step 3 against the mock server, you should see:

```
Connecting to http://localhost:8080...

======================================================================
  FULL PIPELINE DEMO — Signal Intelligence Pipeline
======================================================================
  Version: 1.0.0
  Timestamp: 2026-03-13T21:04:36Z

  STAGE 1: WATCHDOG
  Verdict: DEGRADED
    VALID      System Health
    DEGRADED   Signal Fidelity
    VALID      Regime Confidence

  STAGE 2: REGIME SCANNER
  Regime: NEUTRAL (confidence: 72)
  Signals: 5

    EXECUTE  NVDA/RNDR      [CRYPTO_LEADS  ]
             NEUTRAL + CRYPTO_LEADS + ACTIONABLE | hit=82% avg_ret=+8.24% n=22
    EXECUTE  AMD/TAO        [CRYPTO_LEADS  ]
             NEUTRAL + CRYPTO_LEADS + ACTIONABLE | hit=82% avg_ret=+8.24% n=22
      WAIT   AVGO/AKT       [SEMI_LEADS    ]
             SEMI_LEADS is an anti-signal under NEUTRAL (12% hit rate, -14.60% avg return)
      WAIT   MRVL/FET       [SEMI_LEADS    ]
             SEMI_LEADS is an anti-signal under NEUTRAL (12% hit rate, -14.60% avg return)
      WAIT   ASML/RNDR      [FULL_DECOUPLE ]
             FULL_DECOUPLE is not CRYPTO_LEADS — ambiguous expectancy

  STAGE 3: TRADE DECISION
  EXECUTE_REDUCED  (2 execute, 3 wait)
  Note: reduce size due to degraded conditions

  Exit code: 1
======================================================================

  Output written to pipeline_output.json
```

**Reading this output**:

- **EXECUTE_REDUCED** means actionable signals exist but conditions are degraded — reduce position size
- **2 execute, 3 wait** means 2 of 5 signals passed all 7 gates. The other 3 were correctly filtered out
- **NVDA/RNDR and AMD/TAO** are the actionable pairs — both CRYPTO_LEADS under NEUTRAL regime, 82% historical hit rate
- **AVGO/AKT and MRVL/FET** are SEMI_LEADS — a documented anti-signal (12% hit rate, negative avg return). The scanner blocks these
- **ASML/RNDR** is FULL_DECOUPLE — ambiguous expectancy, not tradeable
- **Exit code 1** = DEGRADED. Use this in shell chaining: `python3 examples/full_pipeline_demo.py && python3 my_bot.py`

---

## When the Live API Returns STOP

If you point the pipeline at the live API (`--url http://84.32.34.46:8080`) instead of the mock, you will currently see **NO_TRADE** with exit code 2. This is correct protective behavior — not a malfunction.

The live market is in a SYSTEMIC regime where all three signal types have been decaying for 5-7 months. Under SYSTEMIC, every signal type historically produces losses. The circuit breaker correctly blocks all trades until market conditions return to NEUTRAL with intact CRYPTO_LEADS correlation.

**Where to look**:

- **[`status.json`](status.json)** — real-time system health across all 3 subsystems (regime engine, Granger pipeline, circuit breaker). Auto-updated every 15 minutes. Check this first to see whether the system is HEALTHY, DEGRADED, or HALT, and read the per-component messages explaining why.

- **[`docs/STOP_STATE_DIAGNOSTIC.md`](docs/STOP_STATE_DIAGNOSTIC.md)** — full root cause analysis of the current STOP state. Includes evidence from live test runs, signal decay tables, threshold justification, and why the thresholds should not be loosened. Read this when you see HALT/STOP and want to understand whether its a market condition or an infrastructure problem (spoiler: its market conditions).

- **Live health endpoint**: `GET http://84.32.34.46:8080/system/status` — same data as `status.json` but real-time from the API.

The mock server deliberately returns NEUTRAL-regime data with healthy CRYPTO_LEADS so you can test the EXECUTE path locally regardless of live market conditions.

---

## Next Steps

- **[`USE_CASES.md`](USE_CASES.md)** — 3 ready-to-paste code snippets for regime-gated trade execution, decay-aware position sizing, and regime shift alerting
- **[`PRODUCT.md`](PRODUCT.md)** — full value proposition and architecture diagram
- **[`TESTING.md`](TESTING.md)** — 15 integration tests proving the pipeline works across HEALTHY, DEGRADED, and HALT states
- **[`CHANGELOG.md`](CHANGELOG.md)** — version history

To run the pipeline stages individually instead of the combined demo:

```bash
python3 examples/watchdog.py          # circuit breaker only (exit 0/1/2)
python3 examples/regime_scanner.py    # 7-gate scanner only
python3 examples/watchdog.py && python3 examples/regime_scanner.py  # chained
```
