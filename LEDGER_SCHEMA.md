# Performance Ledger Schema

Schema: `pf-performance-log/v1`

The performance ledger is the forward-testing proof layer for the signal pipeline. It logs every pipeline decision in real time — NO_TRADE during STOP regimes (proving capital preservation discipline) and EXECUTE entries with live prices during actionable regimes (with automated 14-day outcome evaluation).

Entries accumulate in `performance_log.json`, auto-updated every 15 minutes via cron (`update_ledger.sh`). Each entry is deduplicated by `cycle_key` (timestamp rounded to 15-min boundary) so double cron fires never corrupt the log. Writes are atomic (tempfile + `os.replace`).

## File Structure

```json
{
  "schema": "pf-performance-log/v1",
  "entries": [ ... ],
  "summary": { ... },
  "last_updated": "2026-03-17T14:02:31Z"
}
```

## NO_TRADE Entry

Logged when the pipeline returns NO_TRADE — either watchdog STOP (signal integrity compromised) or no actionable signals in current regime. These entries are the capital preservation evidence: the system saw an opportunity to trade and correctly declined.

```json
{
  "schema": "pf-performance-log/v1",
  "cycle_key": "2026-03-17T14:00:00Z",
  "timestamp": "2026-03-17T14:02:31Z",
  "decision": "NO_TRADE",
  "regime": "SYSTEMIC",
  "regime_confidence": 77,
  "watchdog_verdict": "STOP",
  "signal_fidelity": "STOP",
  "regime_confidence_verdict": "VALID",
  "note": "signal integrity compromised",
  "action": "NO_TRADE",
  "horizon_hours": 336
}
```

| Field | Type | Description |
|-------|------|-------------|
| `schema` | string | Always `pf-performance-log/v1` |
| `cycle_key` | string | ISO timestamp rounded to nearest 15-min boundary. Dedup key — only one entry per cycle. |
| `timestamp` | string | Exact UTC time the entry was created |
| `decision` | string | `NO_TRADE` — pipeline declined to trade |
| `regime` | string | Active regime at time of decision: `NEUTRAL`, `SYSTEMIC`, `DIVERGENCE`, `EARNINGS` |
| `regime_confidence` | int | Regime classifier confidence (0-100) |
| `watchdog_verdict` | string | Circuit breaker verdict: `VALID`, `DEGRADED`, `STOP` |
| `signal_fidelity` | string | Signal decay check: `VALID`, `DEGRADED`, `STOP` |
| `regime_confidence_verdict` | string | Confidence check: `VALID`, `DEGRADED` |
| `note` | string | Human-readable reason for the decision |
| `action` | string | SPI field — `NO_TRADE` for declined entries |
| `horizon_hours` | int | SPI field — evaluation horizon in hours (336 = 14 days) |

## EXECUTE Entry

Logged when the pipeline identifies actionable signals. One entry per EXECUTE signal (a single cycle can produce multiple entries if multiple signals pass all 7 gates). Includes live entry prices and queues automatic 14-day outcome evaluation.

```json
{
  "schema": "pf-performance-log/v1",
  "cycle_key": "2026-04-01T14:00:00Z",
  "timestamp": "2026-04-01T14:02:31Z",
  "decision": "EXECUTE",
  "regime": "NEUTRAL",
  "regime_confidence": 72,
  "watchdog_verdict": "VALID",
  "signal_fidelity": "VALID",
  "regime_confidence_verdict": "VALID",
  "note": "NEUTRAL + CRYPTO_LEADS + ACTIONABLE | hit=82% avg_ret=+8.24% n=22",
  "signal_id": "a1b2c3d4e5f6",
  "pair": "NVDA/RNDR",
  "ticker": "RNDR",
  "semi_ticker": "NVDA",
  "action": "BUY",
  "signal_type": "CRYPTO_LEADS",
  "confidence": 0.82,
  "hit_rate": 0.82,
  "avg_return": 8.24,
  "conviction": 85,
  "entry_price_crypto": 12.45,
  "entry_price_semi": 142.30,
  "entry_timestamp": "2026-04-01T14:02:31Z",
  "eval_due": "2026-04-15T14:02:31Z",
  "eval_status": "PENDING",
  "eval_price_crypto": null,
  "eval_price_semi": null,
  "actual_14d_return": null,
  "hit": null,
  "horizon_hours": 336
}
```

| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | string | Deterministic 12-char hex hash of `pair:cycle_key`. Stable across reruns. |
| `pair` | string | Semi/crypto pair (e.g. `NVDA/RNDR`). Native pipeline field. |
| `ticker` | string | SPI field — crypto symbol extracted from pair (e.g. `RNDR`) |
| `semi_ticker` | string | Semiconductor ticker extracted from pair (e.g. `NVDA`) |
| `action` | string | SPI field — `BUY` for EXECUTE entries |
| `signal_type` | string | Signal classification: `CRYPTO_LEADS`, `SEMI_LEADS`, `FULL_DECOUPLE` |
| `confidence` | float | SPI field — maps to `hit_rate` (backtested hit rate for this regime+type combo) |
| `hit_rate` | float | Backtested hit rate (e.g. 0.82 = 82%) |
| `avg_return` | float | Backtested average 14-day return (%) |
| `conviction` | int | Signal conviction score (0-100) |
| `entry_price_crypto` | float/null | Live crypto price at entry (CoinGecko) |
| `entry_price_semi` | float/null | Live semi price at entry (Yahoo Finance) |
| `entry_timestamp` | string | When the entry price was captured |
| `eval_due` | string | When the 14-day evaluation should run (`entry + 336h`) |
| `eval_status` | string | `PENDING` → `EVALUATED` after 14-day check |
| `eval_price_crypto` | float/null | Crypto price at evaluation time |
| `eval_price_semi` | float/null | Semi price at evaluation time |
| `actual_14d_return` | float/null | Actual return: `((eval_price - entry_price) / entry_price) * 100` |
| `hit` | bool/null | `true` if `actual_14d_return > 0`, `false` otherwise |
| `horizon_hours` | int | SPI field — always 336 (14 days) |

## Evaluation Lifecycle

1. **EXECUTE entry created** — live prices captured from CoinGecko (crypto) and Yahoo Finance (semi), `eval_status: "PENDING"`, `eval_due` set to entry + 14 days
2. **Every 15-min cron cycle** — ledger scans all PENDING entries. If `now > eval_due`, fetches current prices and computes `actual_14d_return`
3. **Entry evaluated** — `eval_status` flips to `"EVALUATED"`, `eval_price_crypto/semi` populated, `actual_14d_return` and `hit` computed
4. **Summary recomputed** — hit rate, avg return, streaks, regime distribution all recalculated from scratch every cycle

If price fetch fails at evaluation time, the entry stays PENDING and retries next cycle.

NO_TRADE entries skip evaluation entirely — they have no prices to compare. Their value is proving the system correctly declined to trade during adverse conditions.

## Summary Object

Recomputed from all entries every cycle:

```json
{
  "total_entries": 48,
  "no_trade_count": 45,
  "execute_count": 3,
  "evaluated_count": 1,
  "pending_count": 2,
  "hits": 1,
  "misses": 0,
  "hit_rate": 1.0,
  "avg_return": 8.24,
  "no_trade_streak": 0,
  "regime_distribution": {
    "SYSTEMIC": 45,
    "NEUTRAL": 3
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_entries` | int | Total entries in the ledger |
| `no_trade_count` | int | Entries with `decision: "NO_TRADE"` |
| `execute_count` | int | Entries with `decision: "EXECUTE"` |
| `evaluated_count` | int | EXECUTE entries past their 14-day horizon that have been evaluated |
| `pending_count` | int | EXECUTE entries still waiting for 14-day evaluation |
| `hits` | int | Evaluated entries with positive return |
| `misses` | int | Evaluated entries with negative return |
| `hit_rate` | float/null | `hits / evaluated_count`. Null if no entries evaluated yet. |
| `avg_return` | float/null | Average `actual_14d_return` across evaluated entries. Null if none. |
| `no_trade_streak` | int | Consecutive NO_TRADE entries from the tail of the log. Proves sustained capital preservation. |
| `regime_distribution` | object | Count of entries per regime state |

## b1e55ed SPI Field Mapping

The ledger includes fields aligned to the [b1e55ed Standard Producer Interface](https://github.com/P-U-C/b1e55ed/tree/develop/docs/producers) so downstream consumers can ingest entries without translation:

| SPI Field | Ledger Field | Source | Notes |
|-----------|-------------|--------|-------|
| `ticker` | `ticker` | Extracted from `pair` (right side, e.g. `RNDR` from `NVDA/RNDR`) | Crypto asset being signaled |
| `action` | `action` | Derived from decision | `BUY` for EXECUTE, `NO_TRADE` for declined |
| `confidence` | `confidence` | Maps to `hit_rate` | Backtested hit rate as probability (0.0-1.0) |
| `horizon_hours` | `horizon_hours` | Always 336 | 14-day evaluation window |

Native pipeline fields (`pair`, `signal_type`, `regime`, `conviction`) are preserved alongside SPI fields. Consumers that need the full context can read both. The `signal_id` is deterministic (SHA-256 of `pair:cycle_key`, truncated to 12 hex chars) so b1e55ed attribution can link outcomes back to exact signal receipts.

For the full producer integration (emitting `SIGNAL_TRADFI_V1` and `FORECAST_V1` events), see [`INTEGRATION_B1E55ED.md`](INTEGRATION_B1E55ED.md) and `integration/regime_scanner_producer.py`.

## Dedup Guarantees

- `cycle_key` is the dedup key — timestamp rounded down to the nearest 15-minute boundary
- Only one entry per cycle_key is ever appended (checked before write)
- If the cron fires twice within the same 15-min window, the second run detects the existing entry and skips
- EXECUTE entries produce one entry per signal per cycle (multiple EXECUTE signals in the same cycle get different entries but the same cycle_key pattern — dedup is per-cycle, not per-signal)

## Atomic Write

All writes use `tempfile.mkstemp()` + `os.replace()` to prevent partial writes from corrupting the JSON file. If the process crashes mid-write, the previous valid version remains intact.
