"""Post Fiat Regime Scanner — b1e55ed Producer Integration.

Drop this file into b1e55ed's engine/producers/ directory. The @register
decorator auto-discovers it in the producer registry.

Requires:
    PF_REGIME_API_URL  env var pointing to a running Post Fiat signal API
                       (e.g. http://localhost:8080)

Optional:
    PF_REGIME_API_TIMEOUT  request timeout in seconds (default 10)

Signal flow:
    /signals/filtered API  -->  collect()  -->  normalize()  -->  run()
    raw JSON signals        signal dicts     SIGNAL_TRADFI_V1    FORECAST_V1
                                              events              via interpreter
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── b1e55ed framework imports (available inside their repo) ───────────────────
from engine.base import BaseProducer, ProducerContext
from engine.events import Event, EventType
from engine.interpreters import Interpreter
from engine.registry import register
from engine.types import (
    ForecastPayload,
    RegimeMatrix,
    TradFiSignalPayload,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Our regime states  -->  b1e55ed regime tags
REGIME_MAP: Dict[str, str] = {
    "NEUTRAL":    "BULL",        # signals actionable, risk-on
    "SYSTEMIC":   "CRISIS",      # everything suppressed
    "EARNINGS":   "TRANSITION",  # ambiguous, cautious
    "DIVERGENCE": "TRANSITION",  # ambiguous, cautious
}

# Signal type classifications
ACTIONABLE_TYPE = "CRYPTO_LEADS"
ANTI_SIGNAL_TYPES = {"SEMI_LEADS"}
AMBIGUOUS_TYPES = {"FULL_DECOUPLE"}

DEFAULT_TIMEOUT = 10


# ── Regime Matrix ─────────────────────────────────────────────────────────────

@dataclass
class RegimeMultiplier:
    confidence_multiplier: float = 1.0
    min_confidence: float = 0.0
    abstain: bool = False


REGIME_MATRIX: Dict[str, RegimeMultiplier] = {
    "BULL":       RegimeMultiplier(confidence_multiplier=1.1),
    "BEAR":       RegimeMultiplier(confidence_multiplier=0.7, min_confidence=0.5),
    "CRISIS":     RegimeMultiplier(abstain=True),
    "TRANSITION": RegimeMultiplier(confidence_multiplier=0.85),
}


# ── Interpreter ───────────────────────────────────────────────────────────────

class RegimeScannerInterpreter(Interpreter):
    """Maps Post Fiat regime-filtered signals to b1e55ed forecasts.

    Only CRYPTO_LEADS + ACTIONABLE classification produces a long forecast.
    SEMI_LEADS is an anti-signal (12% hit, -14.60% avg ret) — always abstain.
    FULL_DECOUPLE is ambiguous (n=5) — always abstain.
    """

    name = "regime-scanner"
    regime_matrix = REGIME_MATRIX

    def interpret(
        self,
        symbol: str,
        signals: List[Dict[str, Any]],
        regime_tag: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[ForecastPayload]:
        """Produce a forecast for *symbol* given the current signal set.

        Args:
            symbol: crypto asset to forecast (e.g. "RNDR")
            signals: list of normalized signal dicts from our API
            regime_tag: b1e55ed regime tag (BULL/BEAR/CRISIS/TRANSITION)
            config: optional runtime config overrides

        Returns:
            ForecastPayload or None (abstain)
        """
        # Check regime matrix first
        regime_cfg = self.regime_matrix.get(regime_tag, RegimeMultiplier())
        if regime_cfg.abstain:
            logger.info(
                "regime-scanner: abstain on %s — regime=%s (CRISIS/suppressed)",
                symbol, regime_tag,
            )
            return None

        # Find signals matching this symbol (crypto side of the pair)
        matching = [
            s for s in signals
            if s.get("crypto_symbol", "").upper() == symbol.upper()
        ]

        if not matching:
            return None

        for sig in matching:
            sig_type = sig.get("type", "")
            classification = sig.get("regime_filter", "")

            # Anti-signal: SEMI_LEADS — always abstain
            if sig_type in ANTI_SIGNAL_TYPES:
                logger.info(
                    "regime-scanner: abstain on %s — %s is anti-signal "
                    "(hit=%.0f%%, avg_ret=%.2f%%)",
                    symbol, sig_type,
                    sig.get("hit_rate", 0) * 100,
                    sig.get("avg_ret", 0),
                )
                return ForecastPayload(
                    action="abstain",
                    confidence=0.0,
                    signal_reason=f"REGIME_FILTERED: {sig_type} is anti-signal "
                                  f"(hit={sig.get('hit_rate', 0):.0%}, "
                                  f"avg_ret={sig.get('avg_ret', 0):+.2f}%)",
                )

            # Ambiguous: FULL_DECOUPLE — insufficient sample size
            if sig_type in AMBIGUOUS_TYPES:
                logger.info(
                    "regime-scanner: abstain on %s — %s is ambiguous (n=%d)",
                    symbol, sig_type, sig.get("n", 0),
                )
                return ForecastPayload(
                    action="abstain",
                    confidence=0.0,
                    signal_reason=f"INSUFFICIENT_DATA: {sig_type} ambiguous "
                                  f"(n={sig.get('n', 0)})",
                )

            # Actionable: CRYPTO_LEADS + ACTIONABLE
            if sig_type == ACTIONABLE_TYPE and classification == "ACTIONABLE":
                raw_confidence = sig.get("hit_rate", 0.82)
                adjusted = raw_confidence * regime_cfg.confidence_multiplier

                if regime_cfg.min_confidence and adjusted < regime_cfg.min_confidence:
                    logger.info(
                        "regime-scanner: abstain on %s — adjusted confidence "
                        "%.2f < min %.2f",
                        symbol, adjusted, regime_cfg.min_confidence,
                    )
                    return None

                reason = (
                    f"{sig.get('pair', '?')} | {sig_type} | "
                    f"hit={raw_confidence:.0%} | avg_ret={sig.get('avg_ret', 0):+.2f}% | "
                    f"n={sig.get('n', 0)} | regime={sig.get('regime_id', '?')}"
                )

                return ForecastPayload(
                    action="long",
                    confidence=round(adjusted, 4),
                    signal_reason=reason,
                )

        return None


# ── Producer ──────────────────────────────────────────────────────────────────

@register("regime-scanner", domain="tradfi")
class RegimeScannerProducer(BaseProducer):
    """Ingests Post Fiat regime-filtered semi->crypto divergence signals.

    Signal source: POST FIAT signal API /signals/filtered endpoint.
    Domain: tradfi (signal originates from semiconductor stock analysis).
    Schedule: every 15 minutes (matches API cache refresh cadence).
    """

    name = "regime-scanner"
    schedule = "*/15 * * * *"
    interpreter_cls = RegimeScannerInterpreter

    configurable_fields = {
        "PF_REGIME_API_URL": {
            "required": True,
            "description": "Base URL for Post Fiat signal API (e.g. http://localhost:8080)",
        },
        "PF_REGIME_API_TIMEOUT": {
            "required": False,
            "default": DEFAULT_TIMEOUT,
            "description": "HTTP request timeout in seconds",
        },
    }

    def __init__(self, ctx: ProducerContext):
        super().__init__(ctx)
        self.api_url = os.environ.get("PF_REGIME_API_URL", "")
        self.timeout = int(os.environ.get("PF_REGIME_API_TIMEOUT", DEFAULT_TIMEOUT))
        self.interpreter = RegimeScannerInterpreter()

    # ── collect ───────────────────────────────────────────────────────────────

    def collect(self) -> List[Dict[str, Any]]:
        """Fetch filtered signals from the Post Fiat API.

        Returns:
            List of raw signal dicts from /signals/filtered response,
            or empty list on failure (sets health to DEGRADED).
        """
        if not self.api_url:
            logger.error("regime-scanner: PF_REGIME_API_URL not set")
            self.set_health("ERROR", "PF_REGIME_API_URL environment variable not set")
            return []

        url = f"{self.api_url.rstrip('/')}/signals/filtered"

        try:
            data = self.ctx.client.request_json(url, timeout=self.timeout)
        except ConnectionError as e:
            logger.warning("regime-scanner: connection error — %s", e)
            self.set_health("DEGRADED", f"Connection error: {e}")
            return []
        except TimeoutError as e:
            logger.warning("regime-scanner: timeout after %ds — %s", self.timeout, e)
            self.set_health("DEGRADED", f"Timeout: {e}")
            return []
        except Exception as e:
            logger.exception("regime-scanner: unexpected error during collect")
            self.set_health("ERROR", f"Unexpected: {e}")
            return []

        if not isinstance(data, dict):
            logger.warning("regime-scanner: unexpected response type %s", type(data))
            self.set_health("DEGRADED", "Unexpected response format")
            return []

        # Attach top-level regime info to each signal for downstream use
        regime_id = data.get("regimeId", "UNKNOWN")
        regime_confidence = data.get("regimeConfidence", 0)
        timestamp = data.get("timestamp", "")

        signals = data.get("signals", [])
        for sig in signals:
            sig["_regime_id"] = regime_id
            sig["_regime_confidence"] = regime_confidence
            sig["_timestamp"] = timestamp

        self.set_health("HEALTHY", f"Collected {len(signals)} signals")
        return signals

    # ── normalize ─────────────────────────────────────────────────────────────

    def normalize(self, raw_signals: List[Dict[str, Any]]) -> List[Event]:
        """Convert raw API signals to SIGNAL_TRADFI_V1 events.

        Each signal becomes one event. The crypto side of the pair
        (e.g. "RNDR" from "NVDA/RNDR") is used as the symbol.
        """
        events = []

        for sig in raw_signals:
            pair = sig.get("pair", "")
            parts = pair.split("/")
            crypto_symbol = parts[1].strip() if len(parts) == 2 else pair
            semi_symbol = parts[0].strip() if len(parts) == 2 else ""

            sig_type = sig.get("type", "")
            classification = sig.get("regimeFilter", "")
            hit_rate = sig.get("regimeFilterHitRate", 0.0)
            avg_ret = sig.get("regimeFilterAvgRet", 0.0)
            n = sig.get("regimeFilterN", 0)
            regime_id = sig.get("_regime_id", "UNKNOWN")
            timestamp = sig.get("_timestamp", "")

            # Determine direction
            if sig_type == ACTIONABLE_TYPE and classification == "ACTIONABLE":
                direction = "long"
                confidence = hit_rate
            else:
                direction = "flat"
                confidence = 0.0

            # Build signal reason string
            signal_reason = (
                f"{pair} | {sig_type} | {classification} | "
                f"hit={hit_rate:.0%} | avg_ret={avg_ret:+.2f}% | "
                f"n={n} | regime={regime_id}"
            )

            # Dedupe key: unique per signal+symbol+timestamp
            ts_int = int(
                time.mktime(time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ"))
            ) if timestamp else int(time.time())
            dedupe_key = f"signal.tradfi.v1:regime-scanner:{crypto_symbol}:{ts_int}"

            payload = TradFiSignalPayload(
                symbol=crypto_symbol,
                source_symbol=semi_symbol,
                direction=direction,
                confidence=confidence,
                signal_reason=signal_reason,
                pair=pair,
                conviction=sig.get("conviction", 0),
                reliability=sig.get("reliability", 0),
                reliability_label=sig.get("reliabilityLabel", ""),
            )

            event = Event(
                event_type=EventType.SIGNAL_TRADFI_V1,
                producer=self.name,
                payload=payload,
                dedupe_key=dedupe_key,
                timestamp=timestamp,
            )

            events.append(event)

            # Store normalized fields back for interpreter use
            sig["crypto_symbol"] = crypto_symbol
            sig["semi_symbol"] = semi_symbol
            sig["regime_filter"] = classification
            sig["hit_rate"] = hit_rate
            sig["avg_ret"] = avg_ret
            sig["n"] = n
            sig["regime_id"] = regime_id

        return events

    # ── run ────────────────────────────────────────────────────────────────────

    def run(self) -> List[Event]:
        """Full producer cycle: collect -> normalize -> interpret -> publish.

        Returns:
            List of all emitted events (signals + forecasts).
        """
        all_events: List[Event] = []

        # Step 1: collect raw signals from API
        raw_signals = self.collect()
        if not raw_signals:
            logger.info("regime-scanner: no signals collected, skipping cycle")
            return all_events

        # Step 2: normalize to SIGNAL_TRADFI_V1 events
        signal_events = self.normalize(raw_signals)
        all_events.extend(signal_events)

        # Step 3: determine regime tag for interpreter
        regime_id = raw_signals[0].get("_regime_id", "UNKNOWN") if raw_signals else "UNKNOWN"
        regime_tag = REGIME_MAP.get(regime_id, "TRANSITION")

        # Step 4: emit forecasts via interpreter for each universe symbol
        universe_symbols = self.ctx.config.get("universe", {}).get("symbols", [])

        for symbol in universe_symbols:
            forecast = self.interpreter.interpret(
                symbol=symbol,
                signals=raw_signals,
                regime_tag=regime_tag,
            )

            if forecast is None:
                continue

            if forecast.action == "abstain":
                logger.info(
                    "regime-scanner: %s -> abstain (%s)",
                    symbol, forecast.signal_reason,
                )
                continue

            forecast_event = Event(
                event_type=EventType.FORECAST_V1,
                producer=self.name,
                payload=forecast,
                dedupe_key=f"forecast.v1:regime-scanner:{symbol}:{int(time.time())}",
                timestamp=raw_signals[0].get("_timestamp", ""),
            )

            all_events.append(forecast_event)
            logger.info(
                "regime-scanner: %s -> %s (confidence=%.4f)",
                symbol, forecast.action, forecast.confidence,
            )

        # Step 5: publish all events
        for event in all_events:
            self.ctx.publish(event)

        return all_events
