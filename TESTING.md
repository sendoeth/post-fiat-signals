# Testing — End-to-End Pipeline Integration

**Last run**: 2026-03-13 | **Result**: 15/15 passed | **Duration**: 0.56s

This document summarizes the integration test suite that exercises the full signal pipeline path from regime engine through Granger pipeline through circuit breaker to final SDK output. The tests prove that the safety story is tested, not just documented.

## Test File

[`tests/test_full_pipeline_integration.py`](tests/test_full_pipeline_integration.py)

Run with:

```bash
python -m pytest tests/test_full_pipeline_integration.py -v
# or
python tests/test_full_pipeline_integration.py
```

No external dependencies. Uses a built-in mock HTTP server with injectable response data.

## Three Scenarios

### Scenario 1: HEALTHY — Pipeline produces actionable output

**Setup**: NEUTRAL regime, CRYPTO_LEADS intact (2.4% drop), no types decaying, fresh data (120s old).

**What it proves**: When market conditions support the trading edge, the pipeline correctly identifies actionable signals and returns EXECUTE (exit 0).

| Test | Assertion |
|------|-----------|
| `test_healthy_pipeline_produces_execute` | Overall decision = EXECUTE, exit code = 0 |
| `test_healthy_watchdog_all_valid` | All 3 watchdog dimensions = VALID |
| `test_healthy_scanner_finds_actionable_signals` | 2 CRYPTO_LEADS signals pass all 7 gates |
| `test_healthy_suppresses_semi_leads` | SEMI_LEADS correctly blocked as anti-signal |
| `test_healthy_output_schema` | Output contains all required fields (watchdog, scanner, overall) |

**Result**: 5/5 passed

### Scenario 2: DEGRADED — Pipeline emits warning metadata and degrades gracefully

**Setup**: NEUTRAL regime, data age 1000s (past 900s warning threshold), last_error set, SEMI_LEADS decaying (1/3 types, below STOP threshold of 2).

**What it proves**: Under partial degradation, the pipeline does not false-halt. It still finds actionable signals but downgrades to EXECUTE_REDUCED (exit 1) with warning metadata explaining the degraded conditions.

| Test | Assertion |
|------|-----------|
| `test_degraded_pipeline_produces_execute_reduced` | Decision = EXECUTE_REDUCED, exit code = 1 |
| `test_degraded_watchdog_detects_warnings` | Verdict = DEGRADED (not STOP), signal_fidelity != STOP |
| `test_degraded_scanner_still_finds_signals` | Scanner runs (not short-circuited), finds EXECUTE signals |
| `test_degraded_has_warning_metadata` | Watchdog reason strings contain substantive warning details |

**Result**: 4/4 passed

### Scenario 3: HALT — Pipeline halts with human-readable explanation

**Setup**: SYSTEMIC regime (77% confidence), 3/3 signal types decaying, CRYPTO_LEADS dropped 50.6% (past 40% STOP threshold), regime alert triggered. Mock data matches actual live API payloads from the [STOP state diagnostic](docs/STOP_STATE_DIAGNOSTIC.md).

**What it proves**: When signal integrity is compromised, the pipeline correctly halts with NO_TRADE (exit 2), skips the scanner entirely (short-circuits at the watchdog STOP gate), and provides a human-readable explanation of why trading is blocked.

| Test | Assertion |
|------|-----------|
| `test_halt_pipeline_produces_no_trade` | Decision = NO_TRADE, exit code = 2, execute_count = 0 |
| `test_halt_watchdog_fires_stop` | Verdict = STOP, signal_fidelity = STOP |
| `test_halt_skips_scanner` | Scanner output is None (correctly short-circuited) |
| `test_halt_provides_human_readable_explanation` | STOP reason references decaying signal types |
| `test_halt_note_explains_integrity` | Position note mentions signal integrity |
| `test_halt_matches_live_stop_behavior` | Mock data matches real live STOP payloads from 2026-03-13 |

**Result**: 6/6 passed

## Full Test Output

```
test_degraded_has_warning_metadata ... ok
test_degraded_pipeline_produces_execute_reduced ... ok
test_degraded_scanner_still_finds_signals ... ok
test_degraded_watchdog_detects_warnings ... ok
test_halt_matches_live_stop_behavior ... ok
test_halt_note_explains_integrity ... ok
test_halt_pipeline_produces_no_trade ... ok
test_halt_provides_human_readable_explanation ... ok
test_halt_skips_scanner ... ok
test_halt_watchdog_fires_stop ... ok
test_healthy_output_schema ... ok
test_healthy_pipeline_produces_execute ... ok
test_healthy_scanner_finds_actionable_signals ... ok
test_healthy_suppresses_semi_leads ... ok
test_healthy_watchdog_all_valid ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.557s

OK
```

## Related Artifacts

- **System health surface**: [`status.json`](status.json) — auto-updated every 15 minutes
- **STOP state diagnostic**: [`docs/STOP_STATE_DIAGNOSTIC.md`](docs/STOP_STATE_DIAGNOSTIC.md) — root cause analysis
- **Stress tests**: [`tests/test_stress.py`](tests/test_stress.py) — 18 degraded-API scenarios (transport/model/health failures)
- **Stress test results**: [`STRESS_TEST_RESULTS.md`](STRESS_TEST_RESULTS.md) — failure mode reference table

## What This Addresses

This test suite closes [Contradiction #7](docs/STOP_STATE_DIAGNOSTIC.md) from the context document: "One missing artifact may still matter: deeper integration testing." The overall reliability posture now includes:

1. **18 stress tests** covering degraded API failure modes (transport errors, malformed JSON, timeouts)
2. **15 integration tests** covering the full pipeline path across HEALTHY, DEGRADED, and HALT states
3. **A public STOP state diagnostic** documenting the root cause of the current live HALT state
4. **An auto-updating system health surface** reporting real-time subsystem status

Together, these make the safety story visible and testable, not just claimed.
