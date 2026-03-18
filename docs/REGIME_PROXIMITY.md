# Regime Proximity Gradient

**Added**: March 18, 2026
**Schema version**: 1.1.0
**Breaking**: No — additive field only
**Endpoints**: `/regime/current`, `/signals/filtered`

## Why

During SYSTEMIC risk-off, the API returns `decision: "NO_TRADE"` with all signals suppressed. Consumers like downstream attribution systems see this as a dead period with no insight into when signals might resume. The proximity gradient converts binary STOP/GO into a continuous signal — consumers can monitor how close the regime engine is to transitioning back to NEUTRAL, making NO_TRADE periods informative rather than opaque.

## Schema

```json
{
  "regimeProximity": {
    "score": 0.012,
    "label": "ENTRENCHED",
    "scale": "0.0 = deep SYSTEMIC, 1.0 = transition imminent",
    "regime": "SYSTEMIC",
    "regimeDurationDays": 12,
    "transitionsNeeded": 2,
    "leader": {
      "type": "SEMI_LEADS",
      "label": "Semi Leads",
      "dropPct": 44.1,
      "distanceToThreshold": 24.1,
      "recoveryScore": 0.196,
      "velocity": -0.41,
      "velocityLabel": "DETERIORATING"
    },
    "bottleneck": {
      "type": "FULL_DECOUPLE",
      "label": "Full Decouple",
      "dropPct": 48.0,
      "distanceToThreshold": 28.0,
      "recoveryScore": 0.067,
      "velocity": -0.45,
      "velocityLabel": "DETERIORATING"
    },
    "perType": {
      "SEMI_LEADS": { "dropPct": 44.1, "distanceToThreshold": 24.1, "recoveryScore": 0.196, "velocity": -0.41, "velocityLabel": "DETERIORATING", "isDecaying": true },
      "FULL_DECOUPLE": { "dropPct": 48.0, "distanceToThreshold": 28.0, "recoveryScore": 0.067, "velocity": -0.45, "velocityLabel": "DETERIORATING", "isDecaying": true },
      "CRYPTO_LEADS": { "dropPct": 50.6, "distanceToThreshold": 30.6, "recoveryScore": 0.0, "velocity": -0.47, "velocityLabel": "DETERIORATING", "isDecaying": true }
    },
    "ifLeaderRecovers": "EARNINGS",
    "interpretation": "All 3 signal types are significantly below their all-time reliability scores..."
  }
}
```

## Field Reference

### Top Level

| Field | Type | Description |
|-------|------|-------------|
| `score` | float | Composite proximity score, 0.0-1.0 |
| `label` | string | Human-readable state (see Label Tiers below) |
| `scale` | string | Static description of the scale endpoints |
| `regime` | string | Current regime ID (SYSTEMIC, NEUTRAL, etc.) |
| `regimeDurationDays` | int | Days since last regime transition |
| `transitionsNeeded` | int | Number of signal types that must recover below 20% decay threshold for NEUTRAL (0 if already NEUTRAL) |
| `leader` | object | Signal type closest to recovery |
| `bottleneck` | object | Signal type that determines transition timing (2nd closest) |
| `perType` | object | Per-signal-type breakdown |
| `ifLeaderRecovers` | string | What regime would result if the leader type recovers |
| `interpretation` | string | Human-readable explanation of current state |

### Per-Type Fields

| Field | Type | Description |
|-------|------|-------------|
| `dropPct` | float | Current drop from all-time score (percentage, e.g. 44.1 = 44.1%) |
| `distanceToThreshold` | float | Percentage points above the 20% decay threshold (0.0 = at threshold) |
| `recoveryScore` | float | Per-type recovery score, 0.0-1.0 (1.0 = at or below threshold) |
| `velocity` | float | Rate of change in reliability score over recent data points. Positive = recovering, negative = deteriorating |
| `velocityLabel` | string | `RECOVERING` (>+2%), `STABLE` (-2% to +2%), `DETERIORATING` (<-2%) |
| `isDecaying` | bool | Whether this type is flagged as decaying (drop >= 20%) |

## Label Tiers

| Label | Score Range | Meaning |
|-------|-------------|---------|
| `ENTRENCHED` | 0.00-0.14 | Deep in non-NEUTRAL regime, all types far from recovery |
| `STABILIZING` | 0.15-0.39 | Decay has stopped accelerating, early signs of stabilization |
| `RECOVERING` | 0.40-0.74 | Active recovery — signal types rebuilding toward threshold |
| `NEAR_TRANSITION` | 0.75-0.99 | Transition imminent — 2+ types approaching recovery threshold |
| `AT_NEUTRAL` | 1.00 | Already at NEUTRAL — signals are live and actionable |

## How the Score is Calculated

### 1. Per-Type Recovery Score

Each of the 3 signal types (SEMI_LEADS, CRYPTO_LEADS, FULL_DECOUPLE) gets a recovery score based on distance from the 20% decay threshold:

```
if dropPct <= 0.20: recoveryScore = 1.0   (at or below threshold — recovered)
if dropPct >= 0.50: recoveryScore = 0.0   (floor — maximally decayed)
else: recoveryScore = (0.50 - dropPct) / (0.50 - 0.20)
```

### 2. Bottleneck-Weighted Composite

Types are sorted by recovery score. For NEUTRAL, 2 of 3 types must recover below 20%, so the **2nd-closest type (bottleneck)** determines when we escape SYSTEMIC:

```
rawScore = leader * 0.30 + bottleneck * 0.50 + laggard * 0.20
```

### 3. Velocity Adjustment

If the bottleneck type is actively recovering (velocity > +2%), score gets a bonus. If still deteriorating (velocity < -2%), score gets a penalty:

```
velocityBonus = min(bottleneckVelocity * 2.0, 0.10)   if velocity > +2%
velocityPenalty = min(|bottleneckVelocity| * 1.5, 0.08)  if velocity < -2%

finalScore = clamp(rawScore + bonus - penalty, 0.0, 1.0)
```

### 4. Leader vs Bottleneck

- **Leader**: the signal type closest to recovering (highest recovery score). Recovery here would shift the regime from SYSTEMIC toward EARNINGS/DIVERGENCE.
- **Bottleneck**: the 2nd-closest type. This is what determines when we reach NEUTRAL — even if the leader recovers first, we need the bottleneck to follow.

## Consumer Patterns

### Quick check — is the regime shifting?

```bash
curl -s http://<host>:8080/regime/current | jq '.regimeProximity | {score, label, bottleneck: .bottleneck.type}'
```

### Monitor over time (cron/polling)

```python
import json, urllib.request

data = json.loads(urllib.request.urlopen("http://<host>:8080/regime/current").read())
prox = data["regimeProximity"]

print(f"Proximity: {prox['score']:.3f} ({prox['label']})")
print(f"Bottleneck: {prox['bottleneck']['label']} — {prox['bottleneck']['dropPct']}% drop, {prox['bottleneck']['velocityLabel']}")
print(f"In SYSTEMIC for {prox['regimeDurationDays']} days")

if prox["score"] > 0.60:
    print("ALERT: regime transition approaching — prepare for signal activation")
```

### Combined with decision field

```bash
curl -s http://<host>:8080/signals/filtered | jq '{
  decision: .decision,
  proximity: .regimeProximity.score,
  proximityLabel: .regimeProximity.label,
  bottleneck: .regimeProximity.bottleneck.type,
  bottleneckVelocity: .regimeProximity.bottleneck.velocityLabel
}'
```

### b1e55ed SPI mapping

| Our field | SPI equivalent | Notes |
|-----------|---------------|-------|
| `regimeProximity.score` | regime confidence modifier | Can scale forecast confidence by proximity |
| `regimeProximity.label` | regime sub-state | Enriches the regime classification beyond binary |
| `regimeProximity.velocity` | trend signal | Positive velocity = early indicator of regime shift |
| `ifLeaderRecovers` | next expected regime | Helps pre-position for upcoming signal types |

## Testing

19 dedicated tests in `tests/test_regime_proximity.py`:

```bash
python tests/test_regime_proximity.py
```

Test scenarios:
- SYSTEMIC entrenched (all types >40% decay, deteriorating)
- SYSTEMIC recovering (types approaching threshold, positive velocity)
- SYSTEMIC near transition (types at threshold boundary)
- NEUTRAL (at target)
- Edge: all types at exact threshold (20%)
- Edge: all types at floor (50%)
- Velocity bonus/penalty effects
- Score bounded [0.0, 1.0]
- Monotonic score vs decay depth
- Schema completeness
- Mock API endpoint integration

Full suite: 86 tests across 5 test files.

## Refresh Cadence

The proximity score recalculates every 15 minutes with the rest of the signal data. During SYSTEMIC, watch for:
1. `velocity` flipping from DETERIORATING to STABLE — first sign of bottom
2. `score` crossing 0.15 — entering STABILIZING zone
3. `bottleneck` type changing — means the weakest link has shifted
4. `ifLeaderRecovers` changing — new transition path emerging
