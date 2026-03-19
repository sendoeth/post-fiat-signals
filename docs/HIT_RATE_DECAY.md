# Hit Rate Decay Model

Duration-conditioned exponential decay for signal hit rates under SYSTEMIC regime.

## Problem

The `/signals/filtered` endpoint reports **static** SYSTEMIC hit rates (e.g., CRYPTO_LEADS = 20%). These aggregates are duration-blind — they weight a trade triggered on day 1 of SYSTEMIC identically to one on day 13. For extended SYSTEMIC periods, the static rate **overstates** the true current hit probability because early-period observations (when signals still carried residual edge from NEUTRAL) inflate the average.

## Model

```
adjustedConfidence(t) = neutralRate * exp(-lambda * t)

where lambda = ln(neutralRate / systemicRate) / medianDuration
```

| Parameter | Description |
|-----------|-------------|
| `neutralRate` | Hit rate under NEUTRAL regime (baseline) |
| `systemicRate` | Aggregate hit rate under SYSTEMIC (from REGIME_FILTER) |
| `medianDuration` | Median historical SYSTEMIC period length (8.5 days) |
| `t` | Current regime duration in days |
| `lambda` | Decay constant (derived) |
| `halfLife` | `medianDuration * ln(2) / ln(neutralRate/systemicRate)` |

### Calibration Property

At `t = medianDuration`, the model output **equals** the empirical SYSTEMIC aggregate. This anchors the decay curve to observed data:
- `t < median` → adjusted > aggregate (signals still have residual edge)
- `t = median` → adjusted = aggregate (calibration anchor)
- `t > median` → adjusted < aggregate (extended decay, aggregate overstates)

## Per-Type Parameters

| Signal Type | Neutral Rate | SYSTEMIC Aggregate | Half-Life | At Day 12 | Bias at Day 12 |
|---|---|---|---|---|---|
| CRYPTO_LEADS | 82% | 20% | ~4.2 days | ~11% | +78% overstated |
| FULL_DECOUPLE | 50% | 25% | ~8.5 days | ~19% | +33% overstated |
| SEMI_LEADS | 12% | 10% | ~32 days | ~9% | +8% overstated |

- **CRYPTO_LEADS** decays fastest because it has the highest NEUTRAL-to-SYSTEMIC contrast (82% → 20%). After ~4 days in SYSTEMIC, its hit rate has already halved from 82% to 41%.
- **SEMI_LEADS** barely decays because it is already an anti-signal under NEUTRAL (12% hit rate). The model correctly identifies minimal additional decay.
- **FULL_DECOUPLE** has exactly an 8.5-day half-life (coincidentally equal to the median SYSTEMIC duration) because its NEUTRAL/SYSTEMIC ratio is exactly 2:1.

## API Response Fields

### Top-Level: `hitRateDecayModel`

Added to `/signals/filtered` and `/regime/current`:

```json
{
  "hitRateDecayModel": {
    "status": "ACTIVE",
    "modelVersion": "exponential-duration-v1",
    "regimeDurationDays": 12,
    "medianHistoricalDuration": 8.5,
    "historicalPeriodCount": 2,
    "noiseThreshold": 0.10,
    "perType": { ... },
    "sensitivityBands": { ... },
    "calibration": { ... }
  }
}
```

### Per-Type Fields

Each entry in `hitRateDecayModel.perType`:

| Field | Type | Description |
|---|---|---|
| `adjustedConfidence` | float | Duration-aware hit rate at current SYSTEMIC day |
| `halfLifeDays` | float | Days for hit rate to fall to 50% of NEUTRAL |
| `decayConstant` | float | Lambda (decay rate per day) |
| `decayVelocityPerDay` | float | Rate of confidence change per day (negative) |
| `daysToNoise` | float | Total days from SYSTEMIC onset to sub-10% confidence |
| `daysToNoiseRemaining` | float | Days remaining until sub-10% (0 if already there) |
| `aggregateBias.pct` | float | How much the static rate over/understates current probability |
| `aggregateBias.direction` | string | `OVERSTATED`, `UNDERSTATED`, or `ALIGNED` |
| `backtestPredictions` | array | Model predictions at each historical period endpoint |

### Per-Signal Fields

Each signal in `signals[]` now includes:

| Field | Type | Description |
|---|---|---|
| `adjustedConfidence` | float | Duration-decayed hit rate for this signal type |
| `decayHalfLifeDays` | float | Half-life for this signal type |
| `daysToNoise` | float | Days to sub-10% threshold |

### Sensitivity Bands

`hitRateDecayModel.sensitivityBands` shows how adjusted confidence varies with +-30% half-life:

```json
{
  "CRYPTO_LEADS": {
    "conservative": { "halfLifeDays": 2.92, "adjustedConfidence": 0.0637 },
    "base":         { "halfLifeDays": 4.17, "adjustedConfidence": 0.1124 },
    "optimistic":   { "halfLifeDays": 5.42, "adjustedConfidence": 0.1729 }
  }
}
```

## Consumer Integration

### b1e55ed SPI Mapping

| Decay Model Field | SPI Field | Notes |
|---|---|---|
| `adjustedConfidence` | `confidence` | Use instead of static `hit_rate` for forecast weighting |
| `decayHalfLifeDays` | `horizon_hours` | Convert: `halfLife * 24` for signal validity window |
| `daysToNoiseRemaining` | `ttl_days` | Signal time-to-live estimate |
| `aggregateBias.direction` | (metadata) | Log for attribution: bias direction at signal time |

### Example: Adjusted Forecast Weighting

```python
import requests

resp = requests.get("http://YOUR_API:8080/signals/filtered").json()

for signal in resp["signals"]:
    static_conf = signal["confidence"]       # 0.20 (duration-blind)
    adjusted = signal["adjustedConfidence"]   # 0.11 (duration-aware)

    # Use adjusted confidence for position sizing
    if adjusted < 0.10:
        print(f"{signal['pair']}: below noise floor, skip")
    else:
        weight = adjusted / sum(s["adjustedConfidence"] for s in resp["signals"])
        print(f"{signal['pair']}: weight {weight:.3f} (adjusted {adjusted:.2%})")
```

## Limitations

1. **n=2 calibration periods.** Only 2 historical SYSTEMIC periods (4d, 13d) anchor the model. A single additional observation could shift half-life estimates significantly.

2. **Inseparable per-period hit rates.** The aggregate SYSTEMIC hit rates combine all observations across both periods. The model cannot cross-validate predictions against individual periods.

3. **Zero-floor assumption.** Adjusted confidence approaches 0 at long durations. In reality, there may be a nonzero residual signal floor — but with n=5/10/4 SYSTEMIC observations per type, the floor is not estimable.

4. **Onset lag.** The model assumes instantaneous decay at SYSTEMIC onset. The first 1-2 days may retain higher hit rates than predicted if signal quality degrades with a lag.

5. **Current period is in extrapolation territory.** At day 12+, we are near or past the longest historical SYSTEMIC period (13d). Model predictions beyond this point are extrapolations from a small sample.

## Tests

42 tests in `tests/test_hit_rate_decay.py` covering:
- Core decay math (half-life, lambda, monotonic decay)
- Calibration anchor (day 0 = NEUTRAL, median = aggregate, extended < aggregate)
- Backtest predictions (short period above, long period below aggregate)
- Sensitivity bands (conservative < base < optimistic)
- Aggregate bias detection (OVERSTATED/UNDERSTATED/ALIGNED)
- Days-to-noise calculations
- Edge cases (NEUTRAL, day 0, very long durations, anti-signals)
- Per-signal injection
