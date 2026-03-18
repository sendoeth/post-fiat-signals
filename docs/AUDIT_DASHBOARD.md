# Forward-Test Audit Dashboard

**Live URL**: [https://sendoeth.github.io/validator/audit.html](https://sendoeth.github.io/validator/audit.html)
**Source**: [`audit_dashboard.html`](../audit_dashboard.html) — single-file, zero dependencies
**Schema**: `pf-system-status/v1` + `pf-performance-log/v1`

## Overview

Real-time 6-panel dashboard that polls the live signal API every 60 seconds to provide a continuous audit surface for forward-test verification. Designed for PF reviewers, ecosystem builders, and the operator to monitor signal pipeline health, ledger accumulation progress, and consumer activity at a glance.

## Panels

### 1. Decision Status
- Current pipeline decision: `NO_TRADE` or `TRADE`
- Decision reason with regime context
- Signal counts: actionable / suppressed / total
- Regime ID and confidence percentage

### 2. Regime Proximity Gradient
- Continuous 0-1 proximity score with label (ENTRENCHED → AT_NEUTRAL)
- Per-type recovery bars (SEMI_LEADS, CRYPTO_LEADS, FULL_DECOUPLE)
- Velocity indicators per signal type (RECOVERING / STABLE / DETERIORATING)
- Bottleneck identification with drop percentage
- Sparkline trend chart (persisted in localStorage, up to 24 data points)
- Estimated recovery days when velocity is positive

### 3. Ledger Accumulation
- Live entry count with accumulation rate
- 7-day milestone progress bar with target date
- NO_TRADE streak counter
- First/last entry timestamps
- Decision breakdown (NO_TRADE vs TRADE counts)

### 4. System Health
- Overall health badge: HEALTHY / DEGRADED / HALT
- Per-component status: Regime Engine, Granger Pipeline, Circuit Breaker
- Human-readable status messages explaining each component state

### 5. Regime Timeline
- Current regime with 90-day transition count
- Visual timeline bar showing regime duration proportions
- Chronological transition log with from → to badges

### 6. Consumer Activity
- External request count and unique consumer count
- Server uptime
- Endpoint breakdown with horizontal bar chart
- Recent external request log with timestamps, paths, and source IPs (redacted)

## API Endpoints Polled

| Endpoint | Panel | Data |
|----------|-------|------|
| `/signals/filtered` | Decision Status | decision, reason, signal counts |
| `/regime/current` | Regime Proximity | proximity score, per-type breakdown |
| `/ledger/summary` | Ledger Accumulation | entry counts, milestone, streak |
| `/system/status` | System Health | overall health, component states |
| `/regime/history` | Regime Timeline | 90-day transitions |
| `/consumer/activity` | Consumer Activity | request log, endpoint breakdown |

## Technical Details

- **Refresh**: 60-second auto-poll with countdown timer
- **Error handling**: Graceful degradation — if one endpoint fails, other panels keep updating. Stale data banner on total disconnect.
- **Loading states**: Skeleton shimmer placeholders before first data load
- **Responsive**: 3-column grid on desktop, 2 on tablet, 1 on mobile
- **Sparkline persistence**: Proximity history stored in `localStorage` (survives page refreshes)
- **CORS**: API serves `Access-Control-Allow-Origin: *`
- **API URL override**: Add `?api=http://your-api-host:port` query parameter

## Deployment

Deployed to GitHub Pages via the [sendoeth/validator](https://github.com/sendoeth/validator) repo. Updates require:

```bash
# Copy updated dashboard
cp audit_dashboard.html /path/to/validator-repo/audit.html
cd /path/to/validator-repo
git add audit.html && git commit -m "Update audit dashboard" && git push
```

GitHub Pages automatically rebuilds within 1-2 minutes of push.

## Consumer Usage

Reviewers and builders can verify forward-test claims by:

1. Opening the [live dashboard](https://sendoeth.github.io/validator/audit.html)
2. Checking the **Ledger Accumulation** panel for days covered and entry rate
3. Verifying **NO_TRADE Streak** matches expectations for SYSTEMIC regime
4. Confirming **System Health** shows correct halt behavior (HALT = signals suppressed, not broken)
5. Monitoring **Regime Proximity** for early signs of regime transition

## b1e55ed Integration

The dashboard provides visual confirmation of the SPI data contract:
- Decision Status shows the same `decision`/`decisionReason` fields consumed by b1e55ed's adapter
- Consumer Activity tracks b1e55ed API hits in real time
- Regime Proximity shows the recovery metrics b1e55ed can use for forecast confidence weighting
