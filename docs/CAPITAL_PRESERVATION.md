# Capital Preservation Score

Counterfactual PnL model that quantifies capital saved by NO_TRADE decisions during SYSTEMIC regime.

## Problem

The performance ledger logs NO_TRADE entries during SYSTEMIC periods, but "we didn't trade" is passive evidence. The reviewer/consumer question is: **how much would you have lost if you had traded?** Without answering this, the NO_TRADE streak is just a timestamp log — not a measurable output.

## Model

### Cross-Regime Return Decomposition

The key insight: we can solve for per-type win/loss returns using NEUTRAL and SYSTEMIC data as simultaneous equations.

Given per signal type:
- NEUTRAL: `N_hr * R_win + (1 - N_hr) * R_loss = N_avg`
- SYSTEMIC: `S_hr * R_win + (1 - S_hr) * R_loss = S_avg`

Solution:
```
R_loss = (N_hr * S_avg - S_hr * N_avg) / (N_hr - S_hr)
R_win  = (N_avg - (1 - N_hr) * R_loss) / N_hr
```

### Decomposed Returns

| Signal Type | R_win | R_loss | Verification |
|---|---|---|---|
| CRYPTO_LEADS | +13.48% | -15.62% | 0.82×13.48 + 0.18×(-15.62) = 8.24% (NEUTRAL avg) |
| SEMI_LEADS | -14.60% | -14.60% | Anti-signal: wins and losses identical |
| FULL_DECOUPLE | -3.05% | -10.05% | Even "wins" lose money under current conditions |

### Duration-Adjusted Expected Return

Using the decay model's adjustedConfidence at day `d`:

```
adjustedExpectedReturn(d) = adjustedConf(d) * R_win + (1 - adjustedConf(d)) * R_loss
```

| Type | Static Expected | Adjusted (Day 13) | Bias |
|---|---|---|---|
| CRYPTO_LEADS | -9.80% | -12.86% | Static understates loss by 31% |
| SEMI_LEADS | -14.60% | -14.60% | No change (anti-signal) |
| FULL_DECOUPLE | -8.30% | -8.84% | Static understates loss by 6.5% |

### Counterfactual Loss Per Entry

For each NO_TRADE ledger entry at regime day `d`:

```
counterfactualLoss = -(weighted sum of adjustedExpectedReturn across all signal types)
```

Positive value = loss avoided by not trading. Weights proportional to signal type count in the current signal set.

## API Response Fields

### `capitalPreservation` Object

Added to `/signals/filtered` and `/regime/current`:

```json
{
  "capitalPreservation": {
    "status": "ACTIVE",
    "modelVersion": "counterfactual-pnl-v1",
    "methodology": "Cross-regime decomposition solves for per-type win/loss returns...",
    "regimeDurationDays": 13,
    "regimeStartEstimate": "2026-03-06",
    "noTradeEntriesEvaluated": 147,
    "liveEntriesEvaluated": 140,
    "backfilledEntriesEvaluated": 7,
    "dateRange": { "first": "...", "last": "..." },
    "aggregate": { ... },
    "perType": { ... },
    "inverseSignal": { ... },
    "sensitivityBands": { ... },
    "sampleEntries": [ ... ],
    "calibration": { ... }
  }
}
```

### Aggregate Fields

| Field | Type | Description |
|---|---|---|
| `totalDrawdownAvoided` | float | Sum of all per-entry counterfactualLoss values (see non-independence note) |
| `avgCounterfactualLossPerEntry` | float | Mean loss avoided per 15-min cycle |
| `liveAvgCounterfactualLoss` | float | Mean for live (non-backfilled) entries only |
| `worstSingleEntry` | float | Maximum counterfactualLoss across all entries |
| `bestSingleEntry` | float | Minimum counterfactualLoss (earliest entries may be lower) |
| `independentTradeWindows` | float | `regimeDuration / 14` — how many non-overlapping trades fit |
| `positionAdjustedDrawdown` | float | `avgLoss * independentWindows` — conservative estimate |
| `unit` | string | Clarifies the metric unit |

### Per-Type Fields

Each entry in `capitalPreservation.perType`:

| Field | Type | Description |
|---|---|---|
| `decomposition.winReturn` | float | Average return of winning trades (from cross-regime solve) |
| `decomposition.lossReturn` | float | Average return of losing trades |
| `staticExpectedReturn` | float | Expected return using aggregate SYSTEMIC hit rate |
| `adjustedExpectedReturn` | float | Expected return using duration-decayed hit rate |
| `staticVsAdjustedBias` | float | How much the static rate under/overstates the adjusted (%) |
| `signalCount` | int | Number of signals of this type in current set |
| `weight` | float | Portfolio weight (signalCount / totalSignals) |
| `sampleSize` | object | `{neutral, systemic}` observation counts |

### Inverse Signal Fields

`capitalPreservation.inverseSignal` (CRYPTO_LEADS only):

| Field | Type | Description |
|---|---|---|
| `longExpectedReturn` | float | Expected return if you go long (negative during extended SYSTEMIC) |
| `shortExpectedReturn` | float | Expected return if you short (= negative of long) |
| `adjustedHitRate` | float | Current decay-adjusted hit rate |
| `viability` | string | `VIABLE_WITH_CAVEATS`, `MARGINAL`, or `NOT_VIABLE` |
| `rationale` | string | Human-readable explanation with sample size caveat |
| `limitations` | array | Specific risks of the inverse signal |

### Sample Entry Fields

Each entry in `capitalPreservation.sampleEntries`:

| Field | Type | Description |
|---|---|---|
| `cycle_key` | string | 15-min cycle identifier |
| `timestamp` | string | When the entry was logged |
| `regimeDay` | float | Day within SYSTEMIC when this entry was logged |
| `counterfactualLoss` | float | Loss avoided (positive = good) |
| `expectedReturnIfTraded` | float | What you'd expect to earn (negative = bad) |
| `breakdown` | object | Per-type adjustedConfidence, expectedReturn, weight |

## Consumer Integration

### b1e55ed Attribution Mapping

| Capital Preservation Field | b1e55ed Usage |
|---|---|
| `counterfactualLoss` | Attribution value for NO_TRADE decisions (discipline premium) |
| `inverseSignal.shortExpectedReturn` | Potential alpha for inverse strategy under SYSTEMIC |
| `positionAdjustedDrawdown` | Conservative PnL impact for portfolio-level attribution |
| `perType.decomposition` | Per-signal-type risk decomposition for factor analysis |

### Example: Position Sizing with Counterfactual Risk

```python
import requests

resp = requests.get("http://YOUR_API:8080/signals/filtered").json()
cp = resp["capitalPreservation"]

if cp["status"] == "ACTIVE":
    avg_loss = cp["aggregate"]["avgCounterfactualLossPerEntry"]
    print(f"Each NO_TRADE avoids ~{avg_loss:.1f}% expected loss")

    # Check inverse signal viability
    inv = cp.get("inverseSignal")
    if inv and inv["viability"] == "VIABLE_WITH_CAVEATS":
        print(f"Short opportunity: {inv['shortExpectedReturn']:.1f}% expected")
        print(f"Caveat: {inv['limitations'][0]}")
```

## Limitations

1. **Regime-invariant return assumption.** The cross-regime decomposition assumes R_win and R_loss are constant across regimes. In reality, SYSTEMIC losses may exhibit fat tails (larger drawdowns than NEUTRAL). This makes the model **conservative** — actual counterfactual losses are likely higher.

2. **Non-independent entries.** 15-min cycles with 14-day horizons produce massive overlap (~1344x). `totalDrawdownAvoided` is cumulative exposure, not realized PnL. Use `positionAdjustedDrawdown` for a more defensible estimate.

3. **Signal frequency assumption.** Signal type distribution assumed constant. In practice, some pairs may drop out or shift type during extended SYSTEMIC.

4. **Equal-weight allocation.** Real portfolios weight by conviction, sector limits, and portfolio constraints. The model assumes 1/n allocation across all signals.

5. **SEMI_LEADS decomposition confirms anti-signal.** Cross-regime solve yields R_win = -14.60%, R_loss = -14.60% — wins and losses are identical. This confirms SEMI_LEADS carries zero directional information in either regime. The inverse signal analysis correctly excludes it.

6. **Inverse signal sample size.** CRYPTO_LEADS short viability is derived from n=5 SYSTEMIC observations. Directionally sound but statistically fragile — a single additional observation could shift the decomposed returns significantly.

## Tests

33 tests in `tests/test_capital_preservation.py` covering:
- Cross-regime decomposition math (7 tests)
- Adjusted expected return computation (5 tests)
- Counterfactual loss per-entry and portfolio (3 tests)
- Inverse signal viability analysis (4 tests)
- Sensitivity bands (3 tests)
- Non-independence adjustment (3 tests)
- Edge cases (4 tests)
- Static vs adjusted bias direction (4 tests)
