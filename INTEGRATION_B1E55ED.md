# b1e55ed Integration Guide — Post Fiat Regime Scanner Producer

## Overview

The `RegimeScannerProducer` bridges Post Fiat's regime-gated semi→crypto divergence signals into b1e55ed's event-sourced architecture. It conforms to the `BaseProducer` interface and auto-discovers via the `@register` decorator.

**What it does**: polls our `/signals/filtered` API every 15 minutes, normalizes each divergence signal into a `SIGNAL_TRADFI_V1` event, then runs a `RegimeScannerInterpreter` that emits `FORECAST_V1` events for symbols in your universe — but only when the regime state says signals are actionable.

**Signal edge**: under NEUTRAL regime, CRYPTO_LEADS divergences hit 82% with +8.24% avg return (n=22). Under SYSTEMIC regime, everything gets suppressed. The producer handles this automatically.

## Installation

### 1. Copy the producer file

```bash
cp integration/regime_scanner_producer.py /path/to/b1e55ed/engine/producers/
```

### 2. Set environment variable

```bash
export PF_REGIME_API_URL=http://<signal-api-host>:8080
```

Optional timeout override (default 10 seconds):

```bash
export PF_REGIME_API_TIMEOUT=15
```

### 3. Verify auto-discovery

The producer registers itself via:

```python
@register("regime-scanner", domain="tradfi")
class RegimeScannerProducer(BaseProducer):
    ...
```

It should appear in your producer registry on next engine restart. No manual wiring needed.

### 4. Configure universe symbols

Your config needs to include the crypto symbols you want forecasts for. The producer only emits forecasts for symbols in `config.universe.symbols` that have a matching signal from our API.

Symbols in our pipeline:

| Pair | Crypto Symbol | Semi Symbol |
|------|--------------|-------------|
| NVDA/RNDR | RNDR | NVDA |
| AMD/TAO | TAO | AMD |
| AVGO/AKT | AKT | AVGO |
| MRVL/FET | FET | MRVL |
| ASML/RNDR | RNDR | ASML |

## Schema Mapping

### Our API fields → SIGNAL_TRADFI_V1 payload

| Our field (camelCase) | Event payload field | Notes |
|-----------------------|-------------------|-------|
| `pair` | `pair` | e.g. "NVDA/RNDR" |
| pair split `/`[1] | `symbol` | crypto side, e.g. "RNDR" |
| pair split `/`[0] | `source_symbol` | semi side, e.g. "NVDA" |
| `type` | (used in logic) | CRYPTO_LEADS / SEMI_LEADS / FULL_DECOUPLE |
| `regimeFilter` | (used in logic) | ACTIONABLE / SUPPRESS / AMBIGUOUS |
| `regimeFilterHitRate` | `confidence` | 0.82 for CRYPTO_LEADS under NEUTRAL |
| `conviction` | `conviction` | 0-100 score |
| `reliability` | `reliability` | 0-100 score |
| `reliabilityLabel` | `reliability_label` | STRONG / MODERATE / DEGRADED |
| (computed) | `direction` | "long" if ACTIONABLE, "flat" otherwise |
| (computed) | `signal_reason` | formatted string with pair, type, stats |

### Regime State Mapping

| Our regime | b1e55ed tag | Confidence multiplier | Behavior |
|-----------|-------------|----------------------|----------|
| NEUTRAL | BULL | 1.1x | Signals boosted — risk-on, divergences are actionable |
| SYSTEMIC | CRISIS | N/A (abstain) | Everything suppressed — no forecasts emitted |
| EARNINGS | TRANSITION | 0.85x | Cautious — earnings volatility creates noise |
| DIVERGENCE | TRANSITION | 0.85x | Cautious — regime is shifting |
| (unknown) | TRANSITION | 0.85x | Default to cautious on unrecognized states |

### Signal Type Classification

| Signal type | Classification | Producer action | Reason |
|-------------|---------------|----------------|--------|
| CRYPTO_LEADS | ACTIONABLE | long forecast | 82% hit rate, +8.24% avg return |
| SEMI_LEADS | SUPPRESS | abstain (REGIME_FILTERED) | Anti-signal: 12% hit rate, -14.60% avg return |
| FULL_DECOUPLE | AMBIGUOUS | abstain (INSUFFICIENT_DATA) | Only n=5 observations, ambiguous expectancy |

## Expected Behavior by Regime

### NEUTRAL (mapped to BULL)

This is the only regime where forecasts get emitted. The producer:
1. Collects all signals from `/signals/filtered`
2. Normalizes each to a `SIGNAL_TRADFI_V1` event
3. For each symbol in your universe with a CRYPTO_LEADS + ACTIONABLE signal:
   - Emits a `FORECAST_V1` with action=long, confidence=0.902 (0.82 * 1.1 BULL multiplier)
4. SEMI_LEADS and FULL_DECOUPLE signals produce abstentions (no forecast events)

### SYSTEMIC (mapped to CRISIS)

All signals suppressed. The producer:
1. Collects signals (they still exist in the API response)
2. Normalizes them to `SIGNAL_TRADFI_V1` events (for your event log)
3. Interpreter returns None for every symbol (CRISIS = abstain)
4. Zero `FORECAST_V1` events emitted

This is the correct, protective behavior. Our pipeline shows all signal types lose their edge under SYSTEMIC conditions.

### EARNINGS / DIVERGENCE (mapped to TRANSITION)

Same flow as NEUTRAL but with reduced confidence:
- CRYPTO_LEADS signals get confidence=0.697 (0.82 * 0.85 TRANSITION multiplier)
- The reduced confidence lets your position sizing react accordingly

## Event Deduplication

Dedupe keys follow this format:

```
signal.tradfi.v1:regime-scanner:{symbol}:{unix_timestamp}
forecast.v1:regime-scanner:{symbol}:{unix_timestamp}
```

Since our API refreshes every 15 minutes and the producer polls on the same schedule, you should see one batch of events per cycle with no duplicates.

## Health States

The producer sets its own health status:

| Health | Meaning |
|--------|---------|
| HEALTHY | Last collect() succeeded, signals returned |
| DEGRADED | Connection error, timeout, or unexpected response format — retrying next cycle |
| ERROR | PF_REGIME_API_URL not set or unrecoverable error |

## Testing

Run standalone tests (no b1e55ed dependency required):

```bash
cd /path/to/pf-regime-sdk
python3 -m unittest tests/test_regime_scanner_producer.py -v
```

Tests mock b1e55ed's framework interfaces and use a built-in HTTP server to simulate the signal API.

## Quickstart Verification

To verify the full pipeline works before connecting to live API:

```bash
# Terminal 1: start mock API
python3 examples/mock_server.py

# Terminal 2: run tests
python3 -m unittest tests/test_regime_scanner_producer.py -v
```

All 14 tests should pass. The mock server returns NEUTRAL regime with CRYPTO_LEADS signals, so you will see long forecasts for RNDR and TAO in the full cycle test.

## API Reference

**Endpoint**: `GET /signals/filtered`

**Live URL**: set via `PF_REGIME_API_URL` env var

**Public SDK repo**: https://github.com/sendoeth/post-fiat-signals

**System status**: check `status.json` in the SDK repo or the `/system/status` live endpoint for current pipeline health before relying on signals.
