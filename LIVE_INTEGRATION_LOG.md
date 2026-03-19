# Live Integration Log — b1e55ed Oracle

Producer: `post-fiat-signals` (wallet `rfLJ4ZRnqmGFLAcMvCD56nKGbjpdTJmMqo`)
Consumer: b1e55ed Synthesis 2026 hackathon oracle (`oracle.b1e55ed.permanentupperclass.com`)
Integration model: **push** — our adapter polls local signal API and POSTs to their oracle
First signal: 2026-03-19T22:44:28Z
Status: **LIVE** — cron pushing every 15 minutes

---

## Timeline

### 2026-03-16 — First contact (inbound pull)

b1e55ed reached out after discovering the public SDK through shipped artifacts. They are building a self-improving trading intelligence system that attributes P&L outcomes back to signals. Our regime-gated pipeline was cited as a reference example in their SPI adapter spec.

### 2026-03-16 to 2026-03-18 — Schema alignment

Built `integration/regime_scanner_producer.py` conforming to b1e55eds BaseProducer interface. Resolved P0/P1 schema gaps:

- Mode A (direct API consumption): field mapping for regime state, signal types, confidence
- Mode B (event-sourced producer): SIGNAL_TRADFI_V1 and FORECAST_V1 event normalization
- Decision field added to `/signals/filtered` after b1e55ed reported "no signals" — SYSTEMIC was suppressing silently without explanation

### 2026-03-19 — Hackathon oracle registration + first live signals

b1e55ed launched a fresh oracle instance for the Synthesis 2026 hackathon (judging closes March 22). Registered as producer and began pushing live signals.

**Registration:**
```
POST https://oracle.b1e55ed.permanentupperclass.com/api/v1/spi/producers
{"producer_id": "post-fiat-signals", "producer_name": "post-fiat-signals"}

Response (HTTP 201):
{
  "producer_id": "post-fiat-signals",
  "api_key": "[REDACTED]",
  "forge": {
    "required": false,
    "grace_period_days": 90,
    "message": "Every b1e55ed producer eventually needs a 0xb1e55ed vanity address."
  }
}
```

**First signal batch (2026-03-19T22:44:28Z):**
```
POST https://oracle.b1e55ed.permanentupperclass.com/api/v1/spi/signals
X-Producer-Key: [REDACTED]

Payload (NVDA example):
{
  "signal_client_id": "pf-NVDA-1773960268",
  "symbol": "NVDA",
  "direction": "bearish",
  "confidence": 0.114,
  "horizon_hours": 168
}

Response (HTTP 201):
{
  "signal_id": "cb1e5a43-1dba-4301-bf0f-21da4bdcc98f",
  "status": "accepted",
  "attribution_window_end": "2026-03-26T22:44:28.456846+00:00"
}
```

All 5 tickers accepted on first attempt. Zero HTTP errors across 5 consecutive push cycles (25 signals total).

---

## Field-Level Mismatch Log

| # | Field / Issue | Severity | Detail | Resolution |
|---|--------------|----------|--------|------------|
| 1 | Registration schema: `producer_name` alone insufficient | P1 | b1e55eds docs showed only `producer_name`. API requires both `producer_id` and `producer_name`. First POST returned 422 with `{"detail":[{"type":"missing","loc":["body","producer_id"]}]}` | Fixed on second attempt. Added both fields. |
| 2 | `regime_filter` camelCase mismatch | P1 | Raw API returns `regimeFilter` (camelCase). Producer `normalize()` wrote `regime_filter` (snake_case) for interpreter consumption but didnt store it back to signal dict. Interpreter couldnt read classification. | Fixed in `regime_scanner_producer.py` — added `sig["regime_filter"] = classification` in normalize loop. |
| 3 | Silent suppression during SYSTEMIC | P0 | b1e55ed reported "no signals" when consuming our API. Root cause: SYSTEMIC regime suppresses all 17 divergence signals but original API returned empty array with no explanation. Consumer couldnt distinguish "no market activity" from "system is working but deliberately sitting out." | Fixed by adding `decision` and `decisionReason` fields to `/signals/filtered`. Now returns `"decision": "NO_TRADE"` with explicit reason. |
| 4 | Empty response during API restart | P2 | b1e55ed hit our API during a restart window. Got empty/error response. No graceful degradation. | Documented. Mitigation: 503 with `status: "warming"` now returned during cache initialization. Pre-deploy warning recommended for future restarts. |
| 5 | `horizon_hours` semantic gap | P3 | b1e55eds example uses short horizons (4h). Our pipeline operates on 14d (336h) backtest windows. Current adapter sends 168h (7d) from transition forecast. Mismatch in expected granularity. | Accepted. Our signals are inherently longer-horizon. Oracle scores against actual market outcome regardless of horizon length. |

---

## Live Traffic Proof

### Signal flow summary (as of 2026-03-19T22:48:01Z)

| Metric | Value |
|--------|-------|
| Push cycles completed | 5 |
| Total signals accepted | 25 |
| HTTP errors | 0 |
| Tickers | NVDA, AMD, AVGO, TSM, MRVL |
| Direction | bearish (all — SYSTEMIC regime) |
| Confidence range | 0.097 (AVGO) to 0.143 (MRVL) |
| Horizon | 168h (7 days) |
| Cron cadence | every 15 min (:03, :18, :33, :48) |
| Attribution windows | close 2026-03-26 |

### Oracle-assigned signal IDs (first batch)

| Ticker | Confidence | Oracle Signal ID | Attribution Window End |
|--------|-----------|-----------------|----------------------|
| NVDA | 0.114 | `cb1e5a43-1dba-4301-bf0f-21da4bdcc98f` | 2026-03-26T22:44:28Z |
| AMD | 0.131 | `ae5c143f-55a2-45f7-8851-fb4bf73ca537` | 2026-03-26T22:44:28Z |
| AVGO | 0.097 | `f8dba320-2b7c-4f15-809e-a3e99d3eb3f3` | 2026-03-26T22:44:28Z |
| TSM | 0.137 | `9e310a98-a176-4e51-a138-98c9e4759a68` | 2026-03-26T22:44:28Z |
| MRVL | 0.143 | `d37f3edb-a987-4880-bb4a-4ab12e814f8b` | 2026-03-26T22:44:28Z |

### Confidence derivation

Per-ticker confidence is not flat — derived from regime state and ticker beta:

```
confidence = 0.15 * (1 - proximity) * ticker_beta * (regime_confidence / 100)

Current: 0.15 * (1 - 0.012) * beta * 0.77

AVGO: beta=0.85 -> 0.097  (strongest fundamental, least directional)
NVDA: beta=1.00 -> 0.114  (baseline)
AMD:  beta=1.15 -> 0.131  (higher beta)
TSM:  beta=1.20 -> 0.137  (geo-exposed)
MRVL: beta=1.25 -> 0.143  (smallest, most volatile)
```

### Regime-adaptive behavior

The adapter handles all 4 regime states:

| Regime | Direction | Confidence Range | Behavior |
|--------|-----------|-----------------|----------|
| SYSTEMIC | bearish | 0.05-0.30 | Capital preservation — low confidence, "stay out" not "go short" |
| NEUTRAL | bullish | 0.10-0.85 | Actionable signals — confidence scales with proximity score |
| DIVERGENCE | bearish | 0.10-0.50 | Transitional — moderate confidence |
| EARNINGS | bearish | 0.10-0.50 | Transitional — moderate confidence |

When the regime flips from SYSTEMIC to NEUTRAL, the adapter automatically switches direction and scales confidence up. No manual intervention required.

---

## Audit Trail

Full signal log maintained at `b1e55ed_signal_log.json` on the producer server. Each entry records:

- Timestamp, regime state, decision, proximity score, regime duration
- Per-signal: `signal_client_id`, symbol, direction, confidence, horizon
- Oracle response: `signal_id` (UUID), `status`, `attribution_window_end`
- HTTP status code

Sample entry:
```json
{
  "signal": {
    "signal_client_id": "pf-NVDA-1773960268",
    "symbol": "NVDA",
    "direction": "bearish",
    "confidence": 0.114,
    "horizon_hours": 168
  },
  "http_status": 201,
  "response": "{\"signal_id\":\"cb1e5a43-1dba-4301-bf0f-21da4bdcc98f\",\"status\":\"accepted\",\"attribution_window_end\":\"2026-03-26T22:44:28.456846+00:00\"}"
}
```

---

## Next Steps

### Producer side (us)

1. **Monitor cron through hackathon** — verify signals keep flowing through March 22 judging close
2. **Capture regime flip if it happens** — the most interesting demo would be automatic bullish switch under NEUTRAL. Transition forecast estimates optimistic March 24, base March 26.
3. **Review karma scores post-hackathon** — oracle will score signals against actual market outcomes. First real forward-test of our pipeline through an external attribution system.
4. **Schema feedback** — if b1e55ed flags any field-level issues from the oracle side, adjust adapter mapping

### Consumer side (b1e55ed)

1. **Confirm signal visibility** — verify `post-fiat-signals` appears as active producer in oracle dashboard
2. **Hackathon demo** — multi-party signal flow is live. 200+ signals expected by Saturday.
3. **Post-hackathon**: decide on permanent integration path — direct API consumption (pull) vs oracle producer (push) vs both
4. **Forge address** — 90-day grace period for `0xb1e55ed` vanity address requirement. Not urgent.

### Joint

1. **Attribution window results** — first windows close March 26. Compare oracle karma scores against our internal backtest predictions.
2. **Schema stabilization** — agree on canonical field mapping for permanent (non-hackathon) integration
3. **Second validation loop** — if karma scores are reasonable, this becomes the first real forward-tested evidence of pipeline quality through an independent scoring system
