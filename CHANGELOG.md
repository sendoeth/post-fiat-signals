# Changelog

All notable changes to the Post Fiat Signals SDK.

---

## v0.3.0 — 2026-03-09

### Added
- **Stress test suite** (`tests/test_stress.py`) — 18 degraded-API scenarios against a mock HTTP server. Covers malformed JSON, empty bodies, HTTP 500/502/503, connection refused, timeouts, partial responses, stale data.
- `STRESS_TEST_RESULTS.md` with failure mode reference table and consumer reliability contract.
- `PRODUCT.md` — elevator pitch, core use case walkthrough, component architecture, target audience.
- `USE_CASES.md` — 3 persona-specific paste-and-run code snippets (regime-gated bot, decay-aware position sizer, regime shift alert monitor).
- Integration blueprint for hit0ri1 Hive Mind pipeline (`docs/blueprints/hit0ri1_hive_mind.md`).

### Changed
- **Error handling hardened across all 6 public methods.** Every `from_dict()` call is now wrapped in `try/except (KeyError, TypeError)` — consumers never see raw parse errors from malformed API responses.
- `_request()` now catches `OSError` for socket-level failures (connection reset, broken pipe) in addition to `URLError`.
- `get_health()` has granular error handlers for timeout, connection failure, and malformed JSON — each raises the appropriate SDK exception.

### Fixed
- `from_dict()` `KeyError` on partial API responses no longer crashes the client — raises `RegimeAPIError` with the offending endpoint name.

---

## v0.2.0 — 2026-03-09

### Added
- **Circuit breaker watchdog** (`examples/watchdog.py`) — pre-trade signal integrity check with 3 independent health dimensions (system health, signal fidelity, regime confidence). Returns VALID/DEGRADED/STOP with exit codes 0/1/2.
- `get_filtered_signals()` method and `FilteredSignalReport` model — regime-conditional signal filter with ACTIONABLE/SUPPRESS/AMBIGUOUS classification per signal.
- `FilteredSignal` dataclass with `.is_actionable` and `.is_suppressed` properties.
- `FilterRule` dataclass for per-signal-type regime filter rules.
- Safety & Validation section in README with watchdog usage docs and verdict table.

### Changed
- Watchdog refactored from simple health check to full circuit breaker pattern with calibrated thresholds from 264-day backtest.
- README expanded with Decision Logic table (7-gate decision tree), Data Contract section, and Error Handling examples.

---

## v0.1.0 — 2026-03-09

### Added
- **Initial release** of the Post Fiat Signals SDK.
- `RegimeClient` with 6 public methods mapping to all API endpoints: `get_regime_state()`, `get_rebalance_queue()`, `get_signal_scores()`, `get_filtered_signals()`, `get_regime_history()`, `get_health()`.
- Typed dataclasses for all API responses: `RegimeState`, `RebalanceQueue`, `ReliabilityReport`, `FilteredSignalReport`, `RegimeHistory`, `HealthStatus`, plus nested types (`SignalState`, `BacktestContext`, `RebalanceEntry`, `SignalReliability`, `RegimeEvent`).
- Auto-retry with exponential backoff on 5xx and timeout errors.
- SDK exception hierarchy: `RegimeAPIError`, `ConnectionError`, `StaleDataError`, `WarmingError`, `TimeoutError`, `RetryExhaustedError`.
- Configurable timeout, retry count, backoff base, and stale-data behavior.
- **Regime scanner** (`examples/regime_scanner.py`) — 7-gate EXECUTE/WAIT decision engine encoding 264 days of backtested research.
- Zero external dependencies — pure Python stdlib (3.10+).
- MIT license.
