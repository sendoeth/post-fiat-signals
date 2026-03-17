# First Cross-Builder Integration Case Study

**Post Fiat Regime-Gated Signal Pipeline x External Attribution Engine**
**March 2026**

> **Privacy note**: All communication referenced in this document has been abstracted from private Post Fiat network messages. No direct quotes, exact timestamps, or channel identifiers are included. The external builder is identified only by their public on-ledger wallet address. Public artifacts (GitHub repos, documentation, YAML configs) are referenced directly as they are already openly accessible.

---

## 1. Discovery Narrative

### How it started

In mid-March 2026, an external Post Fiat contributor (`rsS2Y6CK9dz9dVFjJvRyD2gBdoLPqjaXRZ`) initiated contact through the Post Fiat network messaging system. They introduced themselves as a builder working on a complementary project within the PF ecosystem and asked about what we were building.

We described the semi-to-crypto divergence signal pipeline: a system that detects lead-lag relationships between semiconductor stocks and crypto AI tokens, filters them through a regime detection engine, and exposes the results via a public API and SDK. We pointed them to the public repo ([sendoeth/post-fiat-signals](https://github.com/sendoeth/post-fiat-signals)) which contains the full pipeline — SDK, regime scanner, circuit breaker watchdog, mock server, and 49 integration tests.

### What they were building

The external builder described their project as a self-improving trading intelligence system that attributes every P&L outcome back to the exact signals that drove it. Over time, signals that generate alpha receive more weight and those that dont receive less. Their endgame involves a token-incentivized system where signal producers stake reputation (karma) to emit signals, an on-chain attribution oracle verifies which signals generated alpha, and profitable producers earn a share of the trades they influenced.

They also mentioned contributing to PF network infrastructure — including contributor leveling systems and extraction detection mechanisms.

### The complementarity

Both sides recognized the fit immediately. Our pipeline produces regime-gated signals with documented statistical properties (82% hit rate, +8.24% avg 14-day return for CRYPTO_LEADS under NEUTRAL regime, n=22). Their system needs structured signal inputs with verifiable track records to feed the attribution oracle. Our regime filter acts as a pre-filter that determines *when* signals are actionable. Their attribution engine determines *whether* those signals actually made money.

Together: regime-gated signal generation + P&L attribution = a verifiable signal economy where quality is measured, not claimed.

### Discovery mechanism

This integration was not the result of outbound pitching. The builder found us through the Post Fiat network — they saw our activity, initiated contact, and identified the complementarity themselves. This is the first validated instance of our distribution thesis: **ship public artifacts, let inbound pull do the work.**

For context, two earlier outbound attempts (direct outreach to other PF builders via GitHub issues and Discord) produced no active integrations. This organic inbound contact produced a working integration spec within 48 hours.

---

## 2. Integration Timeline

All dates are approximate (week-level granularity).

| Phase | Timeframe | What happened |
|-------|-----------|---------------|
| First contact | Mid-March 2026 | Builder reaches out through PF messaging, describes their attribution engine |
| Mutual discovery | Same day | Both sides share project descriptions, identify signal-attribution complementarity |
| Repo shared | Same day | We point them to the public SDK repo; they share their repo and draft tokenomics |
| Producer adapter shipped | ~24 hours later | We build `regime_scanner_producer.py` conforming to their BaseProducer interface, push `INTEGRATION_B1E55ED.md` with full schema mapping |
| SPI spec published | ~48 hours after first contact | Builder publishes Standard Producer Interface docs with our pipeline as the reference example for external adapter integrations |

**Total time from first message to being featured as reference producer: under 48 hours.**

This speed was possible because both sides had already shipped functional, documented infrastructure. There was no "can you build an API" or "can you write docs" phase — the artifacts already existed.

---

## 3. SPI Adapter Spec — Technical Breakdown

The builder formalized two integration modes under their Standard Producer Interface (SPI):

### Mode A — Push (Native)

The producer directly POSTs signals to the attribution engine via `POST /api/v1/spi/signals`. Requires an API key, sends standardized payloads (`symbol`, `direction`, `confidence`, `horizon_hours`). Signals enter a lifecycle: onboarding (0-5 signals) → shadow (5-10, karma tracking begins) → active (10+ resolved, karma >= 0.55). Karma is computed via Brier scoring with weekly epoch updates.

### Mode B — Pull (Adapter-Mediated)

An operator writes a YAML specification that tells the engine how to poll an external producers API. The engine handles polling, field mapping, confidence normalization, and routing through the standard signal acceptance pipeline. The producer doesnt need to modify anything — they just keep their API running.

**Our pipeline was used as the complete reference example for Mode B.**

### The YAML Adapter Config

From [spi-adapter.mdx](https://github.com/P-U-C/b1e55ed/blob/develop/docs/producers/spi-adapter.mdx):

```yaml
name: post-fiat-signals
version: "1.0.0"
domain: tradfi
base_url: "${POST_FIAT_SIGNALS_URL}"
poll_interval_sec: 60
min_confidence: 0.55
stale_threshold_sec: 300

health_endpoint:
  path: /health
  method: GET
  timeout_sec: 5

signals_endpoint:
  path: /signals/filtered
  method: GET
  params:
    filter: ACTIONABLE
  timeout_sec: 10

items_path: "signals"

field_mapping:
  symbol: "ticker"
  direction: "action"
  confidence: "confidence"
  horizon_hours: "168"
  observed_at: "timestamp"
  regime: "regime"
  signal_type: "signal_type"
  hit_rate: "hit_rate"
  avg_return: "avg_return"
  is_stale: "is_stale"
  source_assertion: "action"

direction_mapping:
  BUY: bullish
  SELL: bearish
  HOLD: neutral

confidence_normalization:
  strategy: direct
```

Key design decisions in the adapter:

- **`domain: tradfi`** — correct, our signal originates from semiconductor stock analysis even though the trade target is crypto
- **`poll_interval_sec: 60`** — they poll every 60 seconds; our API cache refreshes every 15 minutes
- **`confidence_normalization: direct`** — they recognized our hit rates are already in a usable range, no rescaling needed
- **`horizon_hours: 168`** — literal value (7 days), approximating our 14-day evaluation window at a more standard horizon
- **`stale_threshold_sec: 300`** — signals older than 5 minutes are skipped
- **`min_confidence: 0.55`** — their floor threshold; our CRYPTO_LEADS signals emit at 0.82, well above this

### How Signals Flow Through the Adapter

```
Our API (/signals/filtered)
    ↓ poll every 60s
YAML field mapping
    ↓ ticker, direction, confidence extracted
Direction normalization
    ↓ BUY→bullish, SELL→bearish, HOLD→neutral
Confidence check
    ↓ >= 0.55? pass : discard
Staleness check
    ↓ < 300s old? pass : skip
accept_signal() pipeline
    ↓ standard SPI routing
Attribution oracle
    ↓ Brier scoring at horizon close
Karma ledger update
```

---

## 4. Gap Analysis

The YAML adapter spec was written as a reference template. Several field names in the spec dont match our current API response format. These need resolution before live adapter testing.

### Schema Mismatches

| SPI adapter expects | Our API returns | Severity | Proposed resolution |
|---|---|---|---|
| `ticker` (string, e.g. "BTC") | `pair` (string, e.g. "NVDA/RNDR") | **P0 — Blocking** | Add `ticker` field to API response (crypto side of pair, e.g. "RNDR"). Or update YAML to use `pair` and add a transform step. |
| `action` (BUY/SELL/HOLD) | No `action` field exists | **P0 — Blocking** | Add `action` field to API response. Logic: CRYPTO_LEADS + ACTIONABLE → "BUY", SEMI_LEADS → "HOLD" (anti-signal), SYSTEMIC regime → "HOLD". |
| `confidence` (float, 0.55-0.99) | `regimeFilterHitRate` (float, 0.0-1.0) | **P1 — Field rename** | Add `confidence` as alias for `regimeFilterHitRate`. Our values already fall in [0.55, 0.99] for actionable signals. |
| `regime` (string) | `regimeId` (top-level, not per-signal) | **P1 — Structural** | Flatten `regimeId` into each signal object as `regime`. Currently requires the adapter to read a top-level field separately. |
| `signal_type` | `type` | **P2 — Minor rename** | Add `signal_type` alias or update YAML to reference `type`. |
| `hit_rate` | `regimeFilterHitRate` | **P2 — Minor rename** | Add `hit_rate` alias. |
| `avg_return` | `regimeFilterAvgRet` | **P2 — Minor rename** | Add `avg_return` alias. |
| `is_stale` | Not present | **P2 — Missing field** | Add `is_stale` boolean. Logic: `true` if signal timestamp > 5 minutes old. Low priority — the adapter has its own `stale_threshold_sec` check. |
| `timestamp` (per-signal) | `timestamp` (top-level only) | **P1 — Structural** | Flatten top-level `timestamp` into each signal object as `observed_at` or `timestamp`. |

### Severity Definitions

- **P0 — Blocking**: Adapter will fail or produce incorrect signals without this fix. Must resolve before live testing.
- **P1 — Field rename/structural**: Adapter may work with YAML adjustments, but cleaner to fix on our side for all future consumers.
- **P2 — Minor**: Cosmetic or redundant. Can be resolved with YAML field_mapping alone.

### Resolution Strategy

Two paths:

**Option A — API compatibility endpoint**: Add a `/signals/spi` endpoint that returns signals in the exact format the adapter expects. Keeps `/signals/filtered` unchanged for existing consumers. ~30 lines of transformation logic.

**Option B — Extend `/signals/filtered` response**: Add alias fields (`ticker`, `action`, `confidence`, `regime`, `signal_type`, `hit_rate`, `avg_return`, `is_stale`) alongside existing fields. Non-breaking — existing consumers ignore the new fields.

**Recommendation**: Option B. One endpoint, additive change, no breaking changes. The YAML adapter references `/signals/filtered` directly, and adding aliases means the adapter works without any YAML modifications.

---

## 5. Ecosystem Implications

### What this proves

**1. Cross-builder composability works in Post Fiat.**

Two independent contributors, building different systems (signal generation vs. attribution engine), were able to identify complementarity, share artifacts, and produce a working integration spec — all within the PF ecosystem, in under 48 hours. Neither side needed to modify their core system. The integration happened at the API boundary.

**2. Inbound pull from shipped artifacts outperforms outbound pitching.**

Our distribution thesis was: ship public, documented infrastructure and let builders find it. This integration is the first proof point. Two earlier outbound attempts (direct GitHub issues + Discord messages to other PF builders) produced zero active integrations. This organic inbound contact — initiated by the builder after seeing our network activity — produced a reference-level integration.

| Approach | Attempts | Result |
|----------|----------|--------|
| Outbound (GitHub issues, Discord) | 2 | No active integration |
| Inbound (builder-initiated after seeing shipped artifacts) | 1 | Reference producer in SPI spec within 48 hours |

**3. The Hive Mind thesis has a concrete instantiation.**

The Post Fiat Hive Mind concept proposes that network participants build complementary infrastructure that compounds in value. This integration is the first concrete example: our regime-gated signals feed their attribution oracle, their Brier scoring validates our signal quality claims, and the combined system produces something neither could alone — a verifiable signal economy with both statistical pre-filtering and realized P&L attribution.

**4. The adapter pattern sets a template for future producers.**

By formalizing the SPI (Standard Producer Interface) with our pipeline as the reference, the builder created a repeatable integration path. Any future PF contributor who ships a signal API can write a YAML adapter spec following the same pattern. The cost of the next integration just dropped from "build a custom producer class" to "write a config file."

### What comes next

1. **Resolve P0 field mapping gaps** — add `ticker`, `action`, and `confidence` fields to our API response so the adapter works without YAML workarounds.
2. **Ship the Signal Performance Ledger** — a public log of every pipeline decision (NO_TRADE during STOP, full signal metadata during EXECUTE) with 14-day outcome evaluation. This gives the attribution engine structured receipts it can score.
3. **Live adapter testing** — once the builder finishes core functionality work on their side, run the adapter against our live API and verify signals flow through the full pipeline.
4. **Karma tracking baseline** — our 82% hit rate at 0.82 confidence should produce healthy Brier scores (avg ~0.03 on correct calls). Monitor the first epoch to confirm calibration holds in production.

---

## Appendix: Our Integration Artifacts

| Artifact | Location | Purpose |
|----------|----------|---------|
| `regime_scanner_producer.py` | [integration/](https://github.com/sendoeth/post-fiat-signals/tree/main/integration) | Mode A (Native) drop-in producer for their engine |
| `INTEGRATION_B1E55ED.md` | [repo root](https://github.com/sendoeth/post-fiat-signals/blob/main/INTEGRATION_B1E55ED.md) | Schema mapping, install guide, regime table |
| `test_regime_scanner_producer.py` | [tests/](https://github.com/sendoeth/post-fiat-signals/tree/main/tests) | 16 standalone tests (mocks framework interfaces) |
| SPI adapter spec | [b1e55ed docs](https://github.com/P-U-C/b1e55ed/blob/develop/docs/producers/spi-adapter.mdx) | Mode B (Pull) YAML config using our API as reference |
| SPI interface spec | [b1e55ed docs](https://github.com/P-U-C/b1e55ed/blob/develop/docs/producers/spi-interface.mdx) | Formal signal schema, Brier scoring, karma ledger |
| Full pipeline demo | [examples/](https://github.com/sendoeth/post-fiat-signals/tree/main/examples) | 3-stage pipeline: watchdog → scanner → trade decision |
| Public system status | [status.json](https://github.com/sendoeth/post-fiat-signals/blob/main/status.json) | Auto-updated every 15 min, 3-component health surface |

---

*This case study documents the first cross-builder integration in the Post Fiat ecosystem. All private communications have been abstracted — no direct quotes, exact timestamps, or channel identifiers are included. The external builder is identified only by their public on-ledger wallet address (`rsS2Y6CK9dz9dVFjJvRyD2gBdoLPqjaXRZ`). Public GitHub repos and documentation are referenced directly as they are openly accessible.*
