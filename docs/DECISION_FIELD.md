# Decision Field — `/signals/filtered` Endpoint

**Added**: March 18, 2026
**Schema version**: 1.1.0
**Breaking**: No — additive fields only

## Why

The `/signals/filtered` endpoint returns 17 signals with per-signal `action` and `regimeFilter` fields. But when the regime suppresses everything (e.g. SYSTEMIC risk-off), consumers see 17 HOLD/SUPPRESS signals with no explicit top-level indicator that the system is alive and deliberately not trading.

This made it look like the endpoint was broken or empty when it was actually doing its job — capital preservation.

## New Fields

Two new top-level fields on `/signals/filtered`:

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `decision` | string | `TRADE` \| `NO_TRADE` | System-level trading decision for this refresh cycle |
| `decisionReason` | string | human-readable | Why the decision was made — includes regime, confidence, hit rates |

## Decision Logic

```
if actionableCount > 0:
    decision = "TRADE"
    reason includes: regime, count of actionable signals, signal types, hit rate, avg return

if actionableCount == 0 and regime == SYSTEMIC:
    decision = "NO_TRADE"
    reason includes: regime confidence, suppressed count, historical hit rates (10-25%), capital preservation note

if actionableCount == 0 and regime == EARNINGS or DIVERGENCE:
    decision = "NO_TRADE"
    reason includes: transitional regime label, suppressed count

if actionableCount == 0 and regime == NEUTRAL (edge case — no signals meet threshold):
    decision = "NO_TRADE"
    reason includes: suppressed count, conditions not met
```

## Example Responses

### NO_TRADE (current — SYSTEMIC regime)

```json
{
  "decision": "NO_TRADE",
  "decisionReason": "SYSTEMIC regime (confidence 77%) — all 17 signals suppressed. Historical hit rates 10-25% with negative avg returns under risk-off conditions. Capital preservation mode active.",
  "regimeId": "SYSTEMIC",
  "regimeConfidence": 77,
  "actionableCount": 0,
  "suppressedCount": 17,
  "totalSignals": 17,
  "signals": [ ... ]
}
```

### TRADE (when regime is NEUTRAL with active CRYPTO_LEADS)

```json
{
  "decision": "TRADE",
  "decisionReason": "NEUTRAL regime — 3 actionable signals (CRYPTO_LEADS). Hit rate 82% with 8.2% avg return under this regime.",
  "regimeId": "NEUTRAL",
  "regimeConfidence": 72,
  "actionableCount": 3,
  "suppressedCount": 2,
  "totalSignals": 5,
  "signals": [ ... ]
}
```

## How to Consume

### Quick check — is there anything to trade?

```bash
curl -s "http://<host>:8080/signals/filtered" | jq '.decision'
# "NO_TRADE" or "TRADE"
```

### Get the reason

```bash
curl -s "http://<host>:8080/signals/filtered" | jq '.decisionReason'
# "SYSTEMIC regime (confidence 77%) — all 17 signals suppressed..."
```

### Filter for actionable signals only

```bash
curl -s "http://<host>:8080/signals/filtered" | jq '[.signals[] | select(.action == "BUY")]'
# [] when NO_TRADE, populated array when TRADE
```

### Python consumer pattern

```python
import json, urllib.request

resp = urllib.request.urlopen("http://<host>:8080/signals/filtered")
data = json.loads(resp.read())

if data["decision"] == "TRADE":
    buys = [s for s in data["signals"] if s["action"] == "BUY"]
    for s in buys:
        print(f"{s['ticker']}: {s['signal_type']}, confidence {s['confidence']}, hit_rate {s['hit_rate']}")
elif data["decision"] == "NO_TRADE":
    print(f"No trade: {data['decisionReason']}")
```

### b1e55ed SPI mapping

| Our field | SPI equivalent | Notes |
|-----------|---------------|-------|
| `decision` | forecast action | `TRADE` → `long`, `NO_TRADE` → `abstain` |
| `decisionReason` | forecast rationale | Human-readable, log or display as-is |
| `actionableCount` | — | Quick integer check before iterating signals |
| Per-signal `action` | per-asset action | `BUY` / `HOLD` / `AVOID` per ticker |
| Per-signal `confidence` | signal confidence | Regime-conditional hit rate (0-1 scale) |

## Testing with Mock Server

The mock server (`examples/mock_server.py`) returns a TRADE scenario with 2 actionable CRYPTO_LEADS signals:

```bash
python3 examples/mock_server.py &
curl -s "http://localhost:8080/signals/filtered" | jq '{decision, decisionReason, actionableCount}'
# {"decision": "TRADE", "decisionReason": "NEUTRAL regime — 2 actionable signals...", "actionableCount": 2}
```

## Refresh Cadence

The decision field updates every 15 minutes along with all other signal data. Each response includes:
- `timestamp` — when the data was last computed
- `dataAgeSec` — seconds since last refresh
- `isStale` — true if data is >30 minutes old (2 refresh cycles missed)

During SYSTEMIC regime, expect `NO_TRADE` on every cycle. This is the system working correctly — not silence. The `decision` field confirms the endpoint is alive and processing.
