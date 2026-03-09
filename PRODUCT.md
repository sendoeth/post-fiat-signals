# post-fiat-signals

## what it is

Semiconductor stocks lead crypto AI tokens by 1-72 hours. Not always, not every pair, but often enough to trade — 82% hit rate in the one regime that matters (NEUTRAL), across 264 trading days of backtesting. post-fiat-signals is a Python toolkit that turns that research into a production pipeline: pull regime-classified signals from a live API, run them through a backtested 7-gate decision engine, and validate signal integrity with a circuit breaker before you execute. Zero external deps, pure stdlib, one `git clone` and youre running. The thesis isnt "semis predict crypto" — its narrower and more useful: when crypto AI tokens diverge from semis during a NEUTRAL regime, the convergence trade has a documented statistical edge. Everything else is noise, and the toolkit is built to filter it out.

## core use case

You run a trading bot. Its 2am. NVDA closed up 3% but RNDR is flat. Your bot detects the divergence. Before post-fiat-signals, you fire the trade and hope the backtest still holds. After post-fiat-signals, heres what happens:

**Step 1: Watchdog check.** Your bot runs `watchdog.py`. The circuit breaker polls the API and checks three dimensions — system health (is the data fresh? is the API responsive?), signal fidelity (are reliability scores decaying? is CRYPTO_LEADS dropping?), and regime confidence (is the classifier confident? is the backtest accuracy still credible?). Each dimension gets a verdict: VALID, DEGRADED, or STOP. If any dimension returns STOP, your bot doesnt trade. Full stop. Exit code 2.

**Step 2: Scanner check.** Watchdog returned VALID (exit code 0), so the bot chains into `regime_scanner.py`. The scanner pulls the current regime state and filtered signals from the API, then runs every signal through a 7-gate decision tree. Gate 1: is the regime SYSTEMIC? WAIT — everything is suppressed. Gate 2: is it NEUTRAL? Only regime with positive-EV signals. Gate 3: is the signal type SEMI_LEADS? WAIT — thats an anti-signal (12% hit rate, -14.60% avg return under NEUTRAL). Gate 4: is it CRYPTO_LEADS? The only type with reliable edge. Gate 5: does the regime filter classify it ACTIONABLE? Gate 6: is the hit rate above 65%? Gate 7: is the reliability score stable (not decaying)?

All 7 gates pass. The scanner returns EXECUTE. Your bot opens the position with the full context: 82% historical hit rate, +8.24% avg 14-day return, n=17 sample size under this specific regime x signal combination.

**Step 3: What actually saved you.** Two weeks later, the watchdog catches CRYPTO_LEADS reliability decaying — the rolling score has dropped 40% from its all-time. The underlying Granger relationship between NVDA and RNDR may have weakened (model drift happens). The watchdog returns STOP. Your bot sits on its hands while the scanner wouldve said EXECUTE based on stale statistical foundations. The circuit breaker caught the drift before it cost money.

Thats the pipeline. Watchdog validates the infrastructure. Scanner validates the signal. Both must agree before you trade. One shell command:

```bash
python3 watchdog.py && python3 regime_scanner.py
```

If watchdog fails (exit code 1 or 2), the scanner never runs.

## components

```
Signal Intelligence API (Node.js, port 8080)
         |
         | JSON over HTTP, 15-min refresh, 6 endpoints
         v
   pf_regime_sdk (Python client)
         |
         | Typed dataclasses, retry logic, error handling
         v
   +-----+-----+
   |             |
watchdog.py   regime_scanner.py
   |             |
   | VALID/       | EXECUTE/
   | DEGRADED/    | WAIT
   | STOP         |
   +------+------+
          |
     Trade Decision
```

**SDK client** (`pf_regime_sdk/`) — Python 3.10+, zero deps. 6 methods mapping to 6 API endpoints. Auto-retry with exponential backoff on 5xx and timeout. All transport errors wrapped in SDK exceptions — you never see raw `OSError` or `KeyError` from any public method. Configurable timeout, retry count, backoff, and stale-data behavior.

**Regime scanner** (`examples/regime_scanner.py`) — The decision engine. Pulls regime state and filtered signals, runs them through the 7-gate tree, outputs EXECUTE or WAIT per signal with the specific gate that tripped. The gates encode 264 days of backtested research: only NEUTRAL + CRYPTO_LEADS + ACTIONABLE filter + hit rate above 65% + non-decaying reliability = EXECUTE. Everything else is a documented WAIT with a reason.

**Circuit breaker watchdog** (`examples/watchdog.py`) — Pre-trade integrity check. Three independent health dimensions (system, signal fidelity, regime confidence), each with VALID/DEGRADED/STOP thresholds calibrated from the backtest. Returns exit code 0/1/2 so you can chain it in shell scripts. Catches model drift, data staleness, API degradation, and reliability decay before the scanner even runs.

**Stress tests** (`tests/test_stress.py`) — 18 degraded-API scenarios against a mock HTTP server. Tests every failure mode a consumer can hit: malformed JSON, empty bodies, HTTP 500/502/503, connection refused, timeouts, partial responses, stale data. Every test passes. The implicit reliability contract for anyone integrating the SDK.

## who this is for

**Crypto traders running automated systems** who need regime-conditional signals backed by actual research, not vibes. Specifically:

- **Bot operators** who want a pre-trade safety layer. The watchdog/scanner pipeline is designed to be called programmatically — exit codes, structured output, zero interactive prompts. Chain it into your existing bot with two lines of bash.

- **Quant-curious traders** who understand that backtested edges decay. The circuit breaker isnt a nice-to-have — its the thing that keeps the 82% hit rate from becoming a 50% hit rate after the underlying lead-lag relationship weakens. Models drift. The watchdog catches it.

- **Signal consumers** who want structured data, not chart screenshots. Every response is typed JSON with schema guarantees (v1.1.0). Every field is documented. The SDK gives you Python dataclasses with `.from_dict()` that handle missing keys gracefully instead of crashing.

**What makes this different from generic trading APIs:**

- **Research-backed, not curve-fit.** The underlying thesis (semi price action leads crypto AI tokens at 1-72h lag) is validated via Granger causality testing across 9 pairs. 6/9 significant at p<0.05, 1/9 survives Bonferroni correction (NVDA to RNDR, p=0.004). The regime filter narrows the tradeable universe to the single combination with documented positive expectancy.

- **Built-in signal decay detection.** Most trading APIs give you a number and let you figure out if its still valid. This pipeline tracks reliability scores over time, flags when signals are decaying, and automatically suppresses trades when the statistical foundation erodes. The watchdog doesnt just check if the API is up — it checks if the math still works.

- **Circuit breaker, not just signals.** The three-verdict system (VALID/DEGRADED/STOP) is borrowed from production infrastructure patterns. DEGRADED means reduce size. STOP means dont trade. Your bot doesnt need to interpret confidence scores or make judgment calls — the watchdog already encoded those thresholds from the backtest.

- **Zero deps, zero vendor lock.** Pure Python stdlib. No pip install, no requirements.txt, no API keys, no auth tokens, no rate limits. Clone and run. The API is self-hosted — you control the data pipeline, the refresh interval, and the uptime.
