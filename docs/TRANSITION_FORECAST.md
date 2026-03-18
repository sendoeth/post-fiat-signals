# Regime Transition Forecast

**Endpoint**: `GET /regime/current` — nested under `transitionForecast` object
**Also on**: `GET /signals/filtered` — same object
**Schema**: Extension to `pf-system-status/v1`

## Overview

Velocity-based regime transition predictor that estimates when the current SYSTEMIC regime will end. Uses per-type velocity time series from the proximity data to project forward trajectories, applies historical calibration from observed SYSTEMIC periods, and generates confidence bands (pessimistic/base/optimistic).

During SYSTEMIC, all trading signals are suppressed. The transition forecast answers the question: **when can we trade again?**

## How It Works

### 1. Historical Calibration

The predictor extracts all historical SYSTEMIC periods from `/regime/history` (90-day window) and computes implied recovery rates for each:

```
implied_rate = 25.0 / duration_days  (pct points per day)
```

The 25% baseline assumes types are ~25% above the 20% decay threshold when entering SYSTEMIC (calibrated from observed entry conditions).

From the current 90-day history, 2 SYSTEMIC periods exist:
- **Nov 6-19 (13 days)**: Implied rate 1.92 pct/day (slow recovery)
- **Nov 24-28 (4 days)**: Implied rate 6.25 pct/day (rapid recovery)

### 2. Forward Trajectory Per Type

For each signal type (SEMI_LEADS, CRYPTO_LEADS, FULL_DECOUPLE):

- **distanceToThreshold**: How far above the 20% decay threshold (in pct points)
- **velocity**: Rate of change over last 5 time series data points (fractional)
- **dailyVelocity**: velocity / 5 (approximate daily rate)
- **daysToThreshold**: Projected days to cross below 20% (null if deteriorating)

### 3. The 2-of-3 Condition

SYSTEMIC exits when **at least 2 of 3** signal types recover below the 20% decay threshold. The transition timing is determined by the **2nd-fastest recovering type** (the bottleneck), not the leader.

### 4. Confidence Bands

Three scenarios based on different recovery rate assumptions:

| Band | Rate Source | Current Estimate |
|------|-----------|-----------------|
| **Optimistic** | Fastest observed SYSTEMIC recovery (6.25 pct/day) | ~5 days |
| **Base** | Median of historical recovery rates (4.08 pct/day) | ~7 days |
| **Pessimistic** | Slowest observed SYSTEMIC recovery (1.92 pct/day) | ~15 days |

When signal types are actively recovering (velocity > 0.02), the pessimistic band uses velocity-based projection instead of historical rates.

### 5. Backtest Validation

The predictor is backtested against the 2 observed SYSTEMIC→NEUTRAL transitions:

| Period | Actual | Model Predicted | Error |
|--------|--------|----------------|-------|
| Nov 6-19 | 13 days | 7 days | -6 days |
| Nov 24-28 | 4 days | 7 days | +3 days |

**Mean Absolute Error: 4.5 days** (Moderate predictive accuracy)

### Honest Limitations

- **No stored velocity history**: Backtest uses implied rates from transition durations, not actual velocity snapshots at time of prediction. This validates duration patterns, not velocity patterns.
- **Small sample size**: Only 2 SYSTEMIC periods in the 90-day window. Accuracy assessment will improve as more transitions are observed.
- **Assumes linear recovery**: Real recovery may be non-linear (e.g., sudden reversals vs gradual improvement).
- **25% entry assumption**: The implied rate calculation assumes ~25% average distance at SYSTEMIC entry, which may vary.

## Field Reference

### `transitionForecast` Object

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `NO_RECOVERY_SIGNAL` / `EARLY_RECOVERY` / `RECOVERY_DETECTED` / `STABILIZING` / `AT_NEUTRAL` |
| `message` | string | Human-readable summary with confidence bands |
| `currentTrajectory.allDeteriorating` | boolean | True if all 3 types have velocity < -0.02 |
| `currentTrajectory.anyRecovering` | boolean | True if any type has velocity > 0.02 |
| `currentTrajectory.recoveringCount` | integer | Number of types with positive velocity |
| `currentTrajectory.deterioratingCount` | integer | Number of types with negative velocity |
| `estimatedTransition.pessimistic` | object | `{days, date, scenario}` — worst-case estimate |
| `estimatedTransition.base` | object | `{days, date, scenario}` — median historical rate |
| `estimatedTransition.optimistic` | object | `{days, date, scenario}` — best-case estimate |
| `recoveryRequirements.condition` | string | "2 of 3 signal types must recover below 20% decay threshold" |
| `recoveryRequirements.perType[]` | array | Per-type: currentVelocity, requiredVelocity, velocityGap, feasibility |
| `projectedRegime` | string | What regime the system transitions to (from `ifLeaderRecovers`) |
| `typeProjections[]` | array | Per-type: distanceToThreshold, velocity, dailyVelocity, daysToThreshold, trajectoryNote |
| `historicalCalibration` | object | Observed SYSTEMIC periods, recovery rates, duration stats |
| `backtestValidation` | object | Predicted vs actual for historical transitions, MAE, limitations |

### Status Values

| Status | Meaning | Action |
|--------|---------|--------|
| `NO_RECOVERY_SIGNAL` | All types deteriorating | Use historical bands for planning; no velocity-based estimate |
| `EARLY_RECOVERY` | 1 type recovering | Watch for 2nd type to follow; transition not yet certain |
| `RECOVERY_DETECTED` | 2+ types recovering | Velocity-based estimate available; prepare for transition |
| `STABILIZING` | Velocities near zero | Decay has stopped but recovery not started |
| `AT_NEUTRAL` | Already in NEUTRAL | No forecast needed; signals are live |

### Feasibility Values (per-type)

| Value | Meaning |
|-------|---------|
| `ON_TRACK` | Current velocity meets or exceeds required rate for 14-day recovery |
| `SLOW` | Recovering but too slowly for 14-day target |
| `REVERSED` | Moving away from threshold (negative velocity) |

## Consumer Usage

### Python
```python
import urllib.request, json

resp = urllib.request.urlopen('http://your-api:8080/regime/current')
data = json.loads(resp.read())
tf = data['transitionForecast']

if tf['status'] == 'NO_RECOVERY_SIGNAL':
    print(f"No recovery yet. Historical base: ~{tf['estimatedTransition']['base']['days']}d")
    print(f"Pessimistic: ~{tf['estimatedTransition']['pessimistic']['days']}d")
elif tf['status'] in ('EARLY_RECOVERY', 'RECOVERY_DETECTED'):
    print(f"Recovery in progress! Base estimate: {tf['estimatedTransition']['base']['date']}")
```

### b1e55ed Integration

The transition forecast provides a time-horizon estimate that b1e55ed can use for forecast confidence weighting:

```python
# In b1e55ed interpreter:
forecast = regime_data['transitionForecast']
base_days = forecast['estimatedTransition']['base']['days']

if forecast['status'] == 'NO_RECOVERY_SIGNAL':
    # Scale down confidence — no signals expected soon
    confidence_modifier = 0.3
elif base_days and base_days <= 7:
    # Transition approaching — start preparing positions
    confidence_modifier = 0.7
else:
    confidence_modifier = 0.5
```

## Test Coverage

39 tests across 7 test classes in `tests/test_transition_forecast.py`:

- **TestForecastStatus** (5): Status determination from velocity data
- **TestConfidenceBands** (6): Band ordering, rate sources, edge cases
- **TestHistoricalCalibration** (7): Period extraction, median computation, percentiles
- **TestBacktestValidation** (4): Error calculation, MAE, honest limitations
- **TestTypeProjections** (5): Per-type trajectory, 2-of-3 condition, daily velocity
- **TestEdgeCases** (4): Empty history, single period, 365-day cap, zero distance
- **TestLiveAPIIntegration** (8): Live endpoint schema validation, field presence
