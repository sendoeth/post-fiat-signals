# Optimal Re-Entry Timing Model

## overview

synthesizes 4 upstream diagnostic modules (regime proximity, transition forecast, hit rate decay, capital preservation decomposition) into a single prescriptive `optimalReEntry` object. answers the question downstream consumers actually need: "when does expected value of market entry turn positive?"

available on `/regime/current` and `/signals/filtered`.

## core equation

```
E[R|d] = P(NEUTRAL|d) * E[R|NEUTRAL] + P(SYSTEMIC|d) * [h_adj(d) * R_win + (1 - h_adj(d)) * R_loss]
```

where:
- `d` = forward day (0 = today, 1 = tomorrow, ..., 30)
- `P(NEUTRAL|d)` = probability that regime has flipped to NEUTRAL by forward day d
- `h_adj(d)` = duration-decayed hit rate at total regime day (curDur + d)
- `R_win`, `R_loss` = per-type win/loss returns from cross-regime decomposition
- crossover = first forward day where `E[R|d]` exceeds the risk-free rate

### survival function

```
P(NEUTRAL|d) = uniform CDF between optimistic and pessimistic transition bounds
             = 0                          if d <= opt
             = (d - opt) / (pess - opt)   if opt < d < pess
             = 1                          if d >= pess
```

epistemic humility choice: with only n=2 historical SYSTEMIC periods, any distributional shape is unconstrained. uniform avoids false precision.

### decay model

```
h_adj(d) = neutralRate * exp(-lambda * (curDur + d))
lambda   = ln(neutralRate / systemicRate) / medianDuration
```

inherited from hitRateDecayModel. calibrated so that at the median historical SYSTEMIC duration (8.5d), the output equals the empirical SYSTEMIC aggregate hit rate.

### cross-regime decomposition

solves the 2-equation system per signal type:

```
N_hitRate * R_win + (1 - N_hitRate) * R_loss = N_avgRet
S_hitRate * R_win + (1 - S_hitRate) * R_loss = S_avgRet
```

yields:
- CRYPTO_LEADS: R_win = +13.48%, R_loss = -15.62%
- SEMI_LEADS: R_win = R_loss = -14.60% (anti-signal — no directional info)
- FULL_DECOUPLE: R_win = -3.05%, R_loss = -10.05% (negative EV even in NEUTRAL)

### kelly criterion

```
rawKelly = (effHit * b - (1 - effHit)) / b
where b = |R_win| / |R_loss|, effHit = P(NEUTRAL)*neutralRate + P(SYSTEMIC)*h_adj

serialAdj = min(1, 1 / sqrt(overlapFactor))
where overlapFactor = max(1, totalRegimeDay / horizonDays)

kelly = max(0, min(1, rawKelly * serialAdj))
```

serial correlation adjustment reduces kelly by ~15-30% for overlapping 14-day windows.

## field reference

### top-level fields

| field | type | description |
|---|---|---|
| `status` | string | `AT_NEUTRAL`, `ACTIVE`, `CONDITIONAL`, or `NO_DATA` |
| `modelVersion` | string | `optimal-reentry-v1` |
| `message` | string | human-readable summary |
| `methodology` | string | full equation description |
| `regimeDurationDays` | number | current SYSTEMIC duration |
| `riskFreeRate14d` | number | 14-day yield from 4% APY (~0.150%) |
| `riskFreeRateDescription` | string | describes the benchmark |
| `transitionBounds` | object | optimistic/base/pessimistic days + isConditional flag |
| `optimalEntryDay` | number\|null | first day where weighted EV > risk-free |
| `crossoverDay` | number\|null | same as optimalEntryDay |
| `crossoverDate` | string\|null | ISO date of crossover |
| `crossoverMessage` | string | human-readable crossover explanation |
| `firstTypeToCross` | object\|null | type/label/day/date/reason for earliest crossing type |
| `entryThreshold` | object | proximity score threshold for re-entry monitoring |
| `perType` | object | per-signal-type EV analysis |
| `aggregateCurve` | array | sampled weighted EV curve across all types |
| `sensitivityBands` | object | optimistic/base/pessimistic scenario analysis |
| `upstreamDependencies` | object | status + bias direction for each upstream module |
| `limitations` | object | net bias direction, cascade risk, specific limitations |

### perType[TYPE] fields

| field | type | description |
|---|---|---|
| `label` | string | human-readable type name |
| `neutralExpectedReturn` | number | E[R\|NEUTRAL] for this type |
| `currentExpectedReturn` | number | E[R] at forward day 0 |
| `decomposition` | object | `{winReturn, lossReturn}` from cross-regime system |
| `halfLifeDays` | number | decay half-life (from hitRateDecayModel) |
| `crossoverDay` | number\|null | first day this type crosses risk-free |
| `crossoverDate` | string\|null | ISO date |
| `crossoverMessage` | string | explanation |
| `sampleCurve` | array | sampled EV curve points at days [0,1,2,3,4,5,7,10,14,21,30] |

### aggregateCurve[d] fields

| field | type | description |
|---|---|---|
| `day` | number | forward day |
| `regimeDay` | number | total regime duration at this point |
| `pNeutral` | number | P(NEUTRAL\|d) |
| `weightedExpectedReturn` | number | signal-count-weighted avg EV |
| `weightedKellyFraction` | number | signal-count-weighted avg kelly |
| `weightedHitRate` | number | signal-count-weighted avg hit rate |
| `exceedsRiskFree` | boolean | whether weighted EV > risk-free |

### sensitivityBands

three scenarios:

| scenario | transition bounds | decay half-life | interpretation |
|---|---|---|---|
| optimistic | opt/pess * 0.7 | * 1.3 (slower) | earliest plausible re-entry |
| base | calibrated | calibrated | best estimate |
| pessimistic | opt/pess * 1.5 | * 0.7 (faster) | latest plausible re-entry |

## upstream dependency diagram

```
                    ┌──────────────────┐
                    │  regimeProximity  │
                    │  (score, label)   │──── entryThreshold advisory
                    └──────────────────┘

┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ transitionForecast│    │  hitRateDecay    │    │ capitalPreserv.  │
│ (opt/base/pess   │    │  (perType decay  │    │ (decomposition   │
│  transition days) │    │   constants)     │    │  Rw, Rl per type)│
└────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
         │                       │                       │
         │    P(NEUTRAL|d)       │    h_adj(d)           │   R_win, R_loss
         │                       │                       │
         └───────────────┐       │       ┌───────────────┘
                         ▼       ▼       ▼
                    ┌──────────────────────┐
                    │  optimalReEntry       │
                    │  E[R|d] = synthesis   │
                    │  → crossoverDay       │
                    │  → kellyFraction      │
                    │  → sensitivityBands   │
                    └──────────────────────┘
```

## status values

| status | meaning |
|---|---|
| `AT_NEUTRAL` | regime is NEUTRAL, entry available now, no model needed |
| `ACTIVE` | SYSTEMIC with live velocity data — transition bounds from velocity extrapolation |
| `CONDITIONAL` | SYSTEMIC but all velocities negative — transition bounds from historical rates only |
| `NO_DATA` | insufficient upstream data (decay model not active) |

## limitation disclosure

### net bias direction: PREMATURE

3 of 4 upstream biases push re-entry earlier than reality:

1. **decay overstatement** → if half-life is overstated, model retains more confidence than warranted → earlier crossover
2. **velocity extrapolation** → assumes current recovery rate continues → optimistic transition timing → earlier crossover
3. **regime-invariant returns** → assumes same R_win/R_loss in both regimes → understates SYSTEMIC tail losses → earlier crossover
4. **proximity score** → advisory only, does not affect EV calculation

**recommendation**: treat crossoverDay as a LOWER BOUND (earliest plausible), not a point estimate.

### cascade risk

a 20% decay overstatement + 15% kelly oversize + 10% transition timing error = ~35-45% combined overstatement of entry attractiveness. sensitivity bands attempt to bound this.

### specific limitations

- P(NEUTRAL|d) modeled as uniform CDF. with n=2 historical SYSTEMIC periods, the distribution shape is unconstrained.
- kelly fraction assumes independent bets but 14d horizon creates overlapping windows. sqrt(independentWindows) adjustment is approximate.
- cross-regime decomposition assumes regime-invariant win/loss magnitudes. SYSTEMIC losses may have fatter tails.
- velocity measured over ~5-day window. sudden reversals lag the model.
- single 14-day horizon. shorter/longer horizons shift the crossover date.
- CONDITIONAL status: historical bounds only — if current period exceeds all historical periods, bounds are unreliable.

## consumer code example

```python
import requests

resp = requests.get("http://your-api:8080/regime/current")
data = resp.json()
re = data.get("optimalReEntry", {})

if re.get("status") == "AT_NEUTRAL":
    print("NEUTRAL — trade now")
elif re.get("status") in ("ACTIVE", "CONDITIONAL"):
    first = re.get("firstTypeToCross")
    if first:
        print(f"{first['type']} crosses risk-free on day {first['crossoverDay']}")
        print(f"Target date: {first['crossoverDate']}")
    else:
        print("No type crosses in 30-day window — stay in cash")

    # Check sensitivity
    bands = re.get("sensitivityBands", {})
    opt = bands.get("optimistic", {}).get("aggregateCrossoverDay")
    pess = bands.get("pessimistic", {}).get("aggregateCrossoverDay")
    print(f"Range: optimistic day {opt} — pessimistic day {pess}")

    # Kelly sizing
    for t, info in (re.get("perType") or {}).items():
        curve = info.get("sampleCurve", [])
        for pt in curve:
            if pt.get("exceedsRiskFree"):
                print(f"{t}: entry viable at day {pt['day']}, kelly = {pt['kellyFraction']}")
                break
else:
    print("Insufficient data — wait for decay model")
```

## test coverage

55 tests in `tests/test_optimal_reentry.py` covering:

- survival function (uniform CDF) boundary conditions
- expected return per type under various scenarios
- crossover day detection and ordering
- kelly fraction bounds and serial correlation adjustment
- sensitivity band ordering (optimistic < base < pessimistic)
- NEUTRAL passthrough (entry at day 0)
- deep SYSTEMIC floor behavior
- model dependency chain (bias direction verification)
- edge cases (zero duration, extreme duration, missing bounds)
- aggregate weighted EV behavior
- CRYPTO_LEADS dominance (only tradeable type)
- numerical stability (no NaN/Inf)
