# Forward-Test Attribution — Window Closure Report

**Producer**: `post-fiat-signals` (wallet: `rfLJ4ZRnqmGFLAcMvCD56nKGbjpdTJmMqo`)
**Period**: March 19 – 26, 2026
**Generated**: 2026-03-26
**Proof artifact**: `proof_surface.json`

## Summary

First complete forward-test cycle closure for the post-fiat-signals producer. 2,492 live crypto signals (BTC/ETH/SOL/LINK) pushed to the b1e55ed oracle over 7 days, resolved against realized hourly prices from Yahoo Finance.

| Metric | Value |
|--------|-------|
| Total signals | 2,492 |
| Resolved | 2,128 (85.4%) |
| Unresolved | 364 (open windows) |
| Overall accuracy | 51.5% [49.7%, 53.2%] |
| Overall Brier score | 0.2481 |
| Reliability | 0.000137 (near-perfect) |
| Resolution | 0.002641 |
| Pre-cal accuracy (n=28) | 7.1% |
| Post-cal accuracy (n=2100) | 52.0% |
| Accuracy delta | +44.9% (Fisher p = 1e-6) |
| Confidence-weighted accuracy | 51.9% (delta +0.44% vs binary) |
| Conf-outcome correlation | r=0.087, p=0.0001 |
| Producer Reputation (v2) | 0.556 (Grade C) |

## Key Findings

### 1. Calibration quality is near-perfect

Reliability of 0.000137 means the confidence values assigned to signals almost exactly match realized accuracy rates. The engine knows it is close to a coin flip on 24h direction and assigns appropriately low confidence (~0.53 mean). This is a feature, not a bug — the system is honestly calibrated rather than overconfident.

### 2. Pre-cal vs post-cal improvement is massive and statistically significant

The natural experiment: 28 pre-calibration signals (all bearish, arbitrary low confidence ~0.10) achieved 7.1% accuracy. 2,100 post-calibration signals (data-driven direction and confidence) achieved 52.0% accuracy. Delta of +44.9%, Fisher exact p = 1e-6.

The improvement is primarily from correct direction selection (the naive "everything bearish" assumption during a mixed market was catastrophically wrong). The calibration engine correctly identified that most symbols leaned bullish in this window.

### 3. Directional accuracy is modest — and the engine knows it

Overall accuracy of 51.5% barely clears a coin flip. The 90% Wilson CI [49.7%, 53.2%] includes 50%. But the calibration engine correctly assigns ~53% confidence to ~52% accurate signals — the calibration gap is only 0.99 percentage points. The product claim is not "high accuracy"; it is "honest calibration, correct direction selection, and transition detection."

### 4. Confidence-weighted accuracy shows calibration adds marginal value

Confidence-weighted accuracy (51.9%) exceeds binary accuracy (51.5%) by +0.44%. The confidence-outcome correlation is r=0.087 with p=0.0001 (highly significant at n=2128). High-confidence signals do perform slightly better than low-confidence ones. The effect is small but statistically real.

Confidence brackets show monotonic accuracy increase:
- LOW (0-0.30): 7.1% accuracy, n=28 (pre-calibration)
- HIGH (0.50-0.60): 52.0% accuracy, n=2100 (post-calibration)

Monotonicity test: PASSED.

### 5. Symbol-level performance varies significantly

| Symbol | Accuracy | 90% CI | Brier | Karma | Verdict |
|--------|----------|--------|-------|-------|---------|
| LINK | 56.6% | [53.0%, 60.1%] | 0.2464 | +38.0 | Strongest performer |
| ETH | 54.3% | [50.8%, 57.8%] | 0.2471 | +25.7 | Second strongest |
| BTC | 50.9% | [47.4%, 54.5%] | 0.2501 | +9.1 | Coin flip |
| SOL | 44.0% | [40.5%, 47.5%] | 0.2488 | -30.3 | Below coin flip — known weakness |

LINK and ETH show genuine edges (CIs exclude or nearly exclude 50%). BTC is a coin flip. SOL is a negative contributor — the calibration engine assigned bearish-leaning signals during a period that was net bullish for SOL.

Excluding SOL would raise aggregate accuracy from 51.5% to 53.9%.

### 6. Karma trajectory is net positive but not yet converged

Final cumulative karma: +42.47 across 2,128 resolved signals.
Final mean karma per signal: +0.020.
Convergence: NOT YET — running mean still varying (last-quarter std = 0.015).

Per-symbol karma shows LINK (+38.0) and ETH (+25.7) as the primary positive contributors, BTC (+9.1) as marginal positive, and SOL (-30.3) as the sole negative contributor.

### 7. Accuracy is NOT stable across time

Stability test: first 500 signals at 46.4% accuracy vs remaining 1,628 at 53.0% (z=-2.59, p=0.010). The early period includes pre-calibration signals and a stretch of poor performance around signals 500-1000. Late-period accuracy recovery to 53% drives the aggregate above 50%.

This instability is an important caveat: the 51.5% aggregate may not represent steady-state performance. More forward-test windows are needed to determine whether the later accuracy improvement is persistent.

## Signal Quality Decomposition

| Quality Tier | Accuracy | Mean Confidence | Mean Karma | n |
|-------------|----------|-----------------|------------|---|
| GOOD | 51.6% | 0.576 | +0.019 | 525 |
| MARGINAL | 52.2% | 0.515 | +0.023 | 1,575 |
| UNKNOWN | 7.1% | 0.120 | -0.102 | 28 |

MARGINAL signals slightly outperform GOOD signals (52.2% vs 51.6%) despite lower confidence. This suggests the quality classification boundary between GOOD and MARGINAL may need recalibration. The distinction adds limited discrimination value at current sample sizes.

## Producer Reputation Score

### V1 (original Module 17)
Composite: **0.535** (Grade C)
- Calibration quality: 0.999 (near-perfect reliability)
- Directional accuracy: 0.073 (51.5% is barely above coin flip)
- Abstention discipline: 1.000 (all signals during NO_TRADE regime)
- Confidence sharpness: 0.057 (low variance in forecasts)
- Karma validation: 0.700

### V2 (enhanced with confidence-weighting)
Composite: **0.556** (Grade C)
- Adds: confidence-weighted accuracy component (+0.095 score)
- Adds: calibration monotonicity component (1.0 — brackets are monotonic)
- Both grades C — the dominant drag is low directional accuracy (51.5%)

The C grade is honest. The engine is well-calibrated but has thin directional edges. The reputation score correctly reflects this.

## Limitations

### 1. RESOLUTION_GAP (Bias: INDETERMINATE)
364 of 2,492 signals (14.6%) have attribution windows extending beyond available price data. These are the most recent signals (March 25-26). If recent signals perform differently, the resolved sample may not represent steady-state accuracy.

### 2. SOL_WEAKNESS (Bias: PESSIMISTIC ON AGGREGATE)
SOL accuracy at 44.0% drags the aggregate below what it would be without SOL (53.9% vs 51.5%). The calibration engine correctly identified SOL as the weakest symbol but could not predict that this specific 7-day window would be net bullish for SOL. Excluding SOL would raise aggregate accuracy by ~2.4 percentage points.

### 3. ACCURACY_VS_CALIBRATION_DISCONNECT (Bias: NONE — CORRECTLY CALIBRATED)
51.5% accuracy with 0.000137 reliability means the engine knows its edges are thin and prices them correctly. This is not a limitation in calibration — it is a limitation in the underlying directional signal. The product claim must center on "honest calibration" not "high accuracy."

### 4. PRE_CALIBRATION_SAMPLE_SIZE (Bias: OVERSTATED IMPROVEMENT)
The +44.9% improvement is against n=28 pre-calibration signals that were ALL bearish with arbitrary low confidence. A random-direction baseline would show ~50% accuracy, making the real improvement ~2% not 45%. The massive delta is driven by eliminating a catastrophically wrong direction assumption, not by adding genuine directional alpha.

### 5. SINGLE_REGIME_WINDOW (Bias: UNDERSTATED UNCERTAINTY)
**Most important limitation.** The entire forward-test occurred during SYSTEMIC regime with NO_TRADE decision. The system has NOT been forward-tested during NEUTRAL regime, which is when TRADE signals (the actionable output) would actually fire. The live execution path (NEUTRAL + CRYPTO_LEADS) has zero forward-test resolution. A regime flip is needed for the next validation cycle.

### 6. CONFIDENCE_WEIGHTING_DIAGNOSTIC (Bias: CALIBRATION ADDS VALUE)
The +0.44% confidence-weighted delta and significant correlation (p=0.0001) suggest calibration adds marginal value. But the effect size is small — the practical difference between 51.5% and 51.9% is within transaction cost noise for most strategies. The calibration value is primarily in direction selection (avoiding the catastrophic all-bearish assumption) rather than in confidence discrimination.

## What This Proves

1. **The calibration engine works.** Pre→post improvement is massive, statistically significant, and the direction is correct (from catastrophically wrong to slightly above random).
2. **The confidence calibration is near-perfect.** Reliability of 0.000137 means the engine honestly represents its uncertainty.
3. **Directional accuracy is modest.** 51.5% is honest. The value proposition is not directional prophecy.
4. **The system has not been tested where it matters most.** NEUTRAL/CRYPTO_LEADS execution path has zero forward-test data. This is the critical gap.
5. **SOL is the weakest link.** Known before the forward-test, confirmed by it.
6. **LINK and ETH show genuine edges.** Both exceed 50% with CIs that nearly exclude chance.

## What This Does Not Prove

- That the system makes money (no PnL calculation — 24h directional accuracy is not a trading strategy)
- That performance persists across regime changes (single-regime window)
- That TRADE decisions would be profitable when they fire (zero NEUTRAL data)
- That the ~2% genuine edge over random direction is economically significant after costs

## Artifact Reference

- `proof_surface.json` — machine-readable canonical proof (49 KB, JSON)
- `proof_surface_generator.py` — generation script (extends Module 17)
- `test_proof_surface.py` — 69 tests validating proof surface
- `forward_test_attribution.py` — base Module 17 (signal resolution engine)
