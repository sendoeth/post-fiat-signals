# Regime Survival Model (Weibull Duration Prediction)

## Overview

The `regimeSurvival` object on `/regime/current` and `/signals/filtered` replaces the uniform CDF assumption in the optimal re-entry pipeline with a Weibull hazard-based model. Instead of assuming "equal probability of exit on every day between optimistic and pessimistic bounds," the Weibull model captures **duration dependence** — whether regimes become more or less likely to end as they persist.

Core equation: `S(t) = exp(-(t/λ)^k)` where:
- **k** (shape): controls duration dependence. k>1 means regimes "wear out" (exit becomes more likely over time). k<1 means regimes "harden" (become more entrenched). k=1 is memoryless (exponential).
- **λ** (scale): characteristic life. The 63.2nd percentile of the duration distribution.

## Model Derivation

### Data Source

Durations are extracted from the 90-day regime history (`cache.history`):
- **SYSTEMIC periods**: [13d (Nov 6-19), 4d (Nov 24-28)] — n=2 completed
- **EARNINGS periods**: [14d (Jan 13-27), 5d (Jan 30-Feb 4)] — n=2 completed
- **DIVERGENCE periods**: [5d (Oct 24-29)] — n=1 completed
- **Current period**: right-censored (ongoing)

### Model Selection

Two fits are computed:
1. **SYSTEMIC-only** (n=2 completed + 1 censored): uses only SYSTEMIC durations. Preferred when n≥2 because it preserves regime-type specificity.
2. **Pooled non-NEUTRAL** (n=5 completed + 1 censored): all non-NEUTRAL types. Larger sample but assumes common duration structure across regime types.

Primary model = SYSTEMIC-only when n≥2, pooled otherwise.

### MLE via Profile Likelihood

The Weibull log-likelihood for uncensored observations:

```
ℓ(k,λ) = Σ [ln(k/λ) + (k-1)·ln(t_i/λ) - (t_i/λ)^k]
```

For right-censored observations (ongoing regime):

```
ℓ_c = -(t_c/λ)^k
```

**Fitting procedure:**
1. Given k, analytically solve for optimal λ: `λ(k) = [Σ t_i^k / n_uncensored]^(1/k)`
2. Substitute into profile log-likelihood `ℓ(k)`
3. Grid search k ∈ [0.3, 5.0] for initial estimate
4. Newton-Raphson refinement on `ℓ'(k) = 0` with numerical gradient

### Confidence Intervals

Profile-likelihood CIs for k: find k values where `ℓ(k) = ℓ_max - χ²(1, 0.95)/2 ≈ ℓ_max - 1.92`.

Lambda CIs derived from k CIs via the profile relationship (higher k → lower λ).

### Duration Dependence Classification

| k CI vs 1.0 | Classification | Confidence | Meaning |
|-------------|---------------|------------|---------|
| CI entirely > 1.0 | WEARING_OUT | significant | Regime more likely to end over time |
| CI entirely < 1.0 | HARDENING | significant | Regime becomes more entrenched |
| CI spans 1.0 | Point estimate direction | not_significant | Cannot reject memoryless null |

### Backtest

Leave-one-out cross-validation against 5 completed transitions:
- For each period, refit Weibull WITHOUT that observation
- Compute P(exit by observed duration) under LOO Weibull vs uniform CDF
- Compare Brier scores: `Brier = (predicted - observed)^2`

## API Field Reference

### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `ACTIVE`, `AT_NEUTRAL`, or `NO_DATA` |
| `modelVersion` | string | `regime-survival-v1` |
| `message` | string | Human-readable summary with key values |
| `methodology` | string | Fitting method description |
| `regimeDurationDays` | number | Current regime duration in days |

### `weibullParameters`

| Field | Type | Description |
|-------|------|-------------|
| `k` | number | Shape parameter (MLE) |
| `lambda` | number | Scale parameter (MLE) |
| `ci95.k` | object | `{lower, upper, method}` — 95% CI for k |
| `ci95.lambda` | object | `{lower, upper, method}` — 95% CI for λ |
| `fittingMethod` | string | `profile-mle` or `single-observation-default` |
| `converged` | boolean | Whether Newton-Raphson converged |
| `logLikelihood` | number | Maximized log-likelihood |
| `dataSource` | string | `SYSTEMIC-only` or `pooled-non-NEUTRAL` |
| `nCompleted` | number | Number of completed (uncensored) periods |
| `nCensored` | number | Number of right-censored periods |
| `observedDurations` | array | Duration values used for fitting |
| `observedDurationsByType` | object | Durations grouped by regime type |

### `currentDay`

| Field | Type | Description |
|-------|------|-------------|
| `hazardRate` | number | h(t) at current duration — instantaneous exit rate |
| `survivalProbability` | number | S(t) = P(regime lasts ≥ t days) |
| `cumulativeExitProbability` | number | F(t) = 1 - S(t) = P(exit by day t) |
| `medianRemainingDays` | object | `{base, optimistic, pessimistic}` — conditional on survival to today |

### `durationDependence`

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `WEARING_OUT`, `HARDENING`, or `MEMORYLESS` |
| `confidence` | string | `significant` or `not_significant` |
| `duration_dependence_interpretation` | string | Human-readable classification |
| `interpretation` | string | Full explanation with statistical context |

### `hazardCurve`

Array of `{day, hazardRate, survivalProb, cumulativeExitProb, uniformExitProb}` at sample days. Includes uniform CDF for direct comparison.

### `sensitivityBands`

Three scenarios: `{optimistic, base, pessimistic}` each with `{k, lambda, pExitCurrentDay, hazardCurrentDay, medianDuration}`.

### `modelComparison`

Shows both SYSTEMIC-only and pooled fits with `{k, lambda, n, logLik}` for each, plus which was selected and why.

### `backtestValidation`

- `transitions`: array of per-period predictions with `{weibull: {pExit, brierComponent}, uniform: {pExit, brierComponent}, observed, weibullBetter}`
- `summary`: `{completedPeriods, avgBrierWeibull, avgBrierUniform, brierImprovement, weibullWins, uniformWins, verdict}`

### `uniformComparison`

| Field | Type | Description |
|-------|------|-------------|
| `uniformPExitCurrentDay` | number | P(exit) under uniform CDF |
| `weibullPExitCurrentDay` | number | P(exit) under Weibull |
| `divergence` | number | Absolute difference |
| `recommendation` | string | Which model is more appropriate and why |

### `limitations`

Five quantified limitation fields: `sampleSize`, `poolingAssumption`, `rightCensoring`, `stationarity`, `netBias`.

## Consumer Code Example

```python
import requests

resp = requests.get("http://your-node:8080/regime/current")
data = resp.json()
surv = data.get("regimeSurvival", {})

if surv.get("status") == "ACTIVE":
    k = surv["weibullParameters"]["k"]
    p_exit = surv["currentDay"]["cumulativeExitProbability"]
    dep_type = surv["durationDependence"]["type"]
    median_remaining = surv["currentDay"]["medianRemainingDays"]["base"]

    print(f"Weibull k={k} ({dep_type})")
    print(f"P(exit by today): {p_exit:.1%}")
    print(f"Median remaining: {median_remaining} days")

    # Use sensitivity bands for position sizing
    bands = surv["sensitivityBands"]
    p_optimistic = bands["optimistic"]["pExitCurrentDay"]
    p_pessimistic = bands["pessimistic"]["pExitCurrentDay"]
    print(f"P(exit) range: [{p_pessimistic:.1%}, {p_optimistic:.1%}]")

    # Compare with uniform CDF assumption
    uc = surv["uniformComparison"]
    if uc["divergence"] and uc["divergence"] > 0.1:
        print(f"WARNING: Weibull diverges from uniform by {uc['divergence']:.0%}")
        print(uc["recommendation"])
```

## b1e55ed SPI Mapping

| regimeSurvival field | SPI field | Notes |
|---------------------|-----------|-------|
| `currentDay.cumulativeExitProbability` | `regime_exit_probability` | Primary signal for timing |
| `durationDependence.type` | `duration_dependence` | WEARING_OUT/HARDENING/MEMORYLESS |
| `currentDay.hazardRate` | `hazard_rate` | Instantaneous exit density |
| `currentDay.medianRemainingDays.base` | `median_remaining_days` | Conditional on survival |
| `weibullParameters.k` | `weibull_shape` | k>1 favorable for timing |
| `backtestValidation.summary.verdict` | `model_verdict` | WEIBULL_BETTER/UNIFORM_BETTER |
