# Parameter Uncertainty Propagation Model

**Model Version**: `parameter-uncertainty-v1`
**Endpoints**: `GET /regime/current`, `GET /signals/filtered`
**Field**: `parameterUncertainty`

## Problem Statement

Every model in the signal pipeline uses estimated parameters:
- Hit rates estimated from small samples (n=5 to n=17)
- Transition duration estimated from n=2 historical SYSTEMIC periods
- Return magnitudes estimated without raw trade-level data

Point estimates mask the uncertainty inherent in small-sample estimation. The Parameter Uncertainty Propagation Model quantifies this uncertainty by computing confidence intervals on every input parameter and propagating them through the full EV equation to produce confidence intervals on the outputs consumers actually use (crossover day, expected return, probability of profitable entry).

## Mathematical Foundation

### Wilson Score Intervals (Hit Rates)

For binomial proportions (hit rates), naive normal-approximation CIs are unreliable at small n. The Wilson score interval corrects for small-sample bias:

```
center = (p + z^2/(2n)) / (1 + z^2/n)
margin = (z / (1 + z^2/n)) * sqrt(p(1-p)/n + z^2/(4n^2))
CI = [max(0, center - margin), min(1, center + margin)]
```

Where z=1.96 for 95% CI.

Example: CRYPTO_LEADS SYSTEMIC hit rate (1 success in 5 trials):
- Naive CI: [0.20 ± 1.96 * sqrt(0.2*0.8/5)] = [-0.15, 0.55] (includes negative!)
- Wilson CI: [0.036, 0.625] (properly bounded, wider on the right)

### t-Distribution CI (Transition Duration)

With only n=2 observed SYSTEMIC periods (13 days and 4 days), the transition duration CI uses the t-distribution with df=1:

```
mean = 8.5 days, s = 6.36 days, SE = 4.5 days
t_crit(df=1, alpha=0.025) = 12.706
CI = [max(1, 8.5 - 12.706*4.5), 8.5 + 12.706*4.5] = [1, 65.7] days
```

This CI is intentionally wide. It is mathematically honest — with only 2 observations, we genuinely cannot narrow the range much. One additional observation reduces it by ~66% (t_crit drops from 12.706 to 4.303 as df goes from 1 to 2).

### Return Magnitude CI (Heuristic)

Without raw return series, return CIs are estimated heuristically:

```
sigma = 1.5 * |avgRet|
SE = sigma / sqrt(n)
CI = avgRet ± z * SE
```

This likely UNDERSTATES return uncertainty. True CIs require trade-level data.

### Cross-Regime Decomposition CI Propagation

Wilson CIs on hit rates propagate through the 2-equation decomposition system:

```
R_loss = (hN * rS - hS * rN) / (hN - hS)
R_win = (rN - (1-hN) * R_loss) / hN
```

When Wilson CI bounds bring hN and hS close together, the denominator (hN - hS) approaches zero, causing decomposition instability. This is a genuine feature, not a bug — when we can't distinguish neutral from systemic hit rates, the win/loss decomposition becomes meaningless.

### Scenario-Based EV Propagation

Seven parameter combinations are evaluated per type:

| Scenario | Hit Rates | Transition |
|----------|-----------|------------|
| Base | Point estimates | 1.0x |
| Optimistic params | Upper neutral, lower systemic | 1.0x |
| Pessimistic params | Lower neutral, upper systemic | 1.0x |
| Fast transition | Point estimates | 0.5x |
| Slow transition | Point estimates | 2.0x |
| Combined best | Upper neutral, lower systemic | 0.5x |
| Combined worst | Lower neutral, upper systemic | 2.0x |

Each scenario runs the full EV equation through 30 forward days:

```
E[R|d] = P(NEUTRAL|d) * E[R|NEUTRAL] + P(SYSTEMIC|d) * [h_adj(d) * R_win + (1-h_adj(d)) * R_loss]
```

The min/max across scenarios gives the 95% CI envelope. The fraction of scenarios where EV > risk-free rate gives the probability of profitable entry.

## API Response Schema

```json
{
  "parameterUncertainty": {
    "status": "ACTIVE",
    "modelVersion": "parameter-uncertainty-v1",
    "message": "Parameter uncertainty propagation active...",
    "methodology": "Wilson score intervals on binomial hit rates...",
    "regimeDurationDays": 13,

    "parameterCIs": {
      "CRYPTO_LEADS": {
        "label": "Crypto Leads",
        "neutralHitRate": {
          "point": 0.82,
          "ci95": { "lower": 0.5897, "upper": 0.9381, "center": 0.7639 },
          "n": 17,
          "interpretation": "Wilson score interval..."
        },
        "systemicHitRate": {
          "point": 0.2,
          "ci95": { "lower": 0.0362, "upper": 0.6245, "center": 0.3303 },
          "n": 5,
          "interpretation": "With n=5, the 95% CI spans [0.0362, 0.6245]..."
        },
        "neutralAvgReturn": { "point": 8.24, "ci95": {...}, "n": 17 },
        "systemicAvgReturn": { "point": -9.8, "ci95": {...}, "n": 5 },
        "decomposition": {
          "winReturn": { "lower": -204.46, "upper": 13.48, "point": 13.48 },
          "lossReturn": { "lower": -15.62, "upper": 313.94, "point": -15.62 }
        }
      },
      "_transitionDuration": {
        "observedDurations": [13, 4],
        "ci95": { "lower": 1, "upper": 65.7, "center": 8.5, "n": 2, "tCritical": 12.706 }
      }
    },

    "propagatedOutputCIs": {
      "riskFreeRate14d": 0.151,
      "curve": [{
        "day": 0,
        "regimeDay": 13,
        "aggregate": {
          "pointEstimateEV": -12.1,
          "ci95Lower": -12.1, "ci95Upper": -4.303,
          "ciWidth": 7.797,
          "probabilityProfitable": 0,
          "scenariosProfitable": 0, "totalScenarios": 7
        },
        "CRYPTO_LEADS": {
          "pointEstimateEV": -12.862,
          "ci95Lower": -12.862, "ci95Upper": 8.24,
          "ciWidth": 21.102,
          "probabilityProfitable": 0.286,
          "scenariosProfitable": 2, "totalScenarios": 7
        }
      }]
    },

    "crossoverDayCI": {
      "pointEstimate": null,
      "ci95Lower": null, "ci95Upper": null,
      "scenariosWithCrossover": 0, "totalScenarios": 7,
      "neverCrossProbability": 1.0,
      "interpretation": "No scenario achieves aggregate crossover...",
      "perType": {
        "CRYPTO_LEADS": { "ci95Lower": 0, "ci95Upper": 24, "scenariosCrossing": 7 },
        "SEMI_LEADS": { "ci95Lower": null, "ci95Upper": null, "scenariosCrossing": 0 },
        "FULL_DECOUPLE": { "ci95Lower": null, "ci95Upper": null, "scenariosCrossing": 0 }
      }
    },

    "informationValue": {
      "ranking": [
        {
          "parameter": "transitionDuration",
          "currentCI": "[1, 65.7] days",
          "currentN": 2,
          "uncertaintyReductionPct": 50,
          "oneMoreObservation": { "reductionPct": 66.1 }
        }
      ]
    },

    "effectiveSampleDiagnostic": {
      "weakestLink": { "parameter": "transitionDuration", "currentN": 2, "ciWidth": 64.7 },
      "moduleContributions": [
        { "module": "transitionForecast", "uncertaintyContributionPct": 56.7, "bottleneck": true },
        { "module": "hitRateDecay", "uncertaintyContributionPct": 40.0, "bottleneck": false },
        { "module": "returnDecomposition", "uncertaintyContributionPct": 3.3, "bottleneck": false }
      ],
      "overallAssessment": "Total estimation uncertainty is dominated by transition timing...",
      "recommendation": "Prioritize evidence accumulation over model refinement..."
    },

    "limitations": {
      "methodology": "Scenario-based propagation... NOT a full Monte Carlo simulation...",
      "returnCIHeuristic": "Return CIs estimated heuristically... likely UNDERSTATES return uncertainty",
      "wilsonAssumptions": "Wilson score assumes independent Bernoulli trials...",
      "jointUncertainty": "Parameters are varied independently...",
      "netBias": "NET UNDERSTATED — three of four limitation factors push toward wider true CIs"
    }
  }
}
```

## Key Findings (Live Data)

### Dominant Uncertainty Source: Transition Timing

| Module | Contribution | Effective N | Bottleneck? |
|--------|-------------|-------------|-------------|
| Transition Forecast | 56.7% | 2 | **Yes** |
| Hit Rate Decay | 40.0% | 5 | No |
| Return Decomposition | 3.3% | 4 | No |

With only n=2 historical SYSTEMIC periods, transition timing dominates total model uncertainty. One additional observation would reduce this component by ~66%.

### CRYPTO_LEADS Crossover CI

Aggregate crossover never occurs (SEMI_LEADS and FULL_DECOUPLE drag the average negative). CRYPTO_LEADS is the only type that crosses the risk-free rate:

| Metric | Value |
|--------|-------|
| CL Crossover Range | Day 0 to Day 24 |
| Scenarios Crossing | 7/7 |
| P(Profitable) at Day 0 | 0.286 (2/7 scenarios) |
| P(Profitable) at Day 14 | 0.857 (6/7 scenarios) |
| P(Profitable) at Day 30 | 1.000 (7/7 scenarios) |

### Information Value Ranking

| Parameter | Crossover Reduction | 1-More-Obs Reduction |
|-----------|-------------------|---------------------|
| Transition Duration | 50% | **66.1%** |
| CL Hit Rate (SYSTEMIC) | 29.2% | 8.7% |
| FD Hit Rate (SYSTEMIC) | 0% | 10.6% |
| SL Hit Rate (SYSTEMIC) | 0% | 4.7% |

The single most valuable observation is one more SYSTEMIC-to-NEUTRAL transition. It provides more information than any algorithmic improvement to existing models.

## Limitations and Net Bias

**Net Bias Direction: UNDERSTATED**

Three of four limitation factors push toward wider true CIs than computed:

1. **Scenario-based, not Monte Carlo**: 7 scenarios per type cannot capture the full joint parameter distribution. True 95% CI may be wider.
2. **Return CI heuristic**: Without raw return series, sigma is estimated as 1.5 * |avgRet|. This likely understates return uncertainty.
3. **Independence assumption**: Wilson score assumes independent Bernoulli trials. Regime-conditioned observations may have serial dependence, making effective n smaller.
4. **Joint uncertainty**: Parameters are varied independently. In reality, hit rates, returns, and transition timing may be correlated.

Treat reported CIs as approximate lower bounds on actual uncertainty.

## Consumer Usage

```python
import json, urllib.request

data = json.loads(urllib.request.urlopen('http://your-api:8080/regime/current').read())
pu = data['parameterUncertainty']

# Check if CL is profitable at 7-day horizon
for point in pu['propagatedOutputCIs']['curve']:
    if point['day'] == 7:
        cl = point['CRYPTO_LEADS']
        print(f"Day 7: P(profitable) = {cl['probabilityProfitable']}")
        print(f"  EV range: [{cl['ci95Lower']}%, {cl['ci95Upper']}%]")
        break

# Check which parameter to prioritize
top = pu['informationValue']['ranking'][0]
print(f"Highest info value: {top['parameter']} (66.1% CI reduction from 1 more obs)")

# Module contributions
for m in pu['effectiveSampleDiagnostic']['moduleContributions']:
    print(f"  {m['module']}: {m['uncertaintyContributionPct']}%")
```

## b1e55ed SPI Mapping

| PU Field | SPI Field | Notes |
|----------|-----------|-------|
| `parameterCIs.CRYPTO_LEADS.systemicHitRate.ci95` | `confidence_interval` | Wilson score bounds on hit rate |
| `propagatedOutputCIs.curve[].CRYPTO_LEADS.probabilityProfitable` | `signal_quality` | Fraction of scenarios profitable |
| `crossoverDayCI.perType.CRYPTO_LEADS` | `forecast_confidence_range` | Days until EV > risk-free |
| `effectiveSampleDiagnostic.weakestLink` | `data_quality_flag` | Identifies estimation bottleneck |
| `limitations.netBias` | `bias_direction` | Systematic bias disclosure |

## Tests

54 tests in `tests/test_parameter_uncertainty.py` covering:
- Wilson score CI computation (7 tests)
- Return CI heuristic (3 tests)
- Transition duration CI with t-distribution (5 tests)
- Cross-regime decomposition CI propagation (3 tests)
- Propagated curve structure and monotonicity (6 tests)
- Crossover day CI per type (5 tests)
- Information value ranking (4 tests)
- Module contribution percentages (5 tests)
- Effective sample size diagnostic (4 tests)
- Limitation disclosure (2 tests)
- Core EV function behavior (4 tests)
- Scenario composition (2 tests)
- Integration/JS-match tests (4 tests)
