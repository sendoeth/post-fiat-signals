#!/usr/bin/env python3
"""Lightweight mock API server for testing the Post Fiat Signals SDK.

Serves realistic responses for all 6 API endpoints so you can run every
USE_CASES.md snippet locally without needing access to a live API.

Usage:
    python3 examples/mock_server.py              # starts on port 8080
    python3 examples/mock_server.py --port 9090  # custom port

Then in another terminal:
    export PF_API_URL=http://localhost:8080
    python3 examples/regime_scanner.py
    python3 examples/watchdog.py

Zero external dependencies — stdlib only.
"""

import json
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8080

# ── Mock data ──────────────────────────────────────────────────────────────────
# All field names match the camelCase keys that the SDK's from_dict() expects.

def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _regime_proximity_neutral():
    """Proximity data for NEUTRAL regime — already at target, score ~1.0."""
    return {
        "score": 1.0,
        "label": "AT_NEUTRAL",
        "scale": "0.0 = deep SYSTEMIC, 1.0 = transition imminent",
        "regime": "NEUTRAL",
        "regimeDurationDays": 45,
        "transitionsNeeded": 0,
        "leader": {
            "type": "CRYPTO_LEADS", "label": "Crypto Leads",
            "dropPct": 3.3, "distanceToThreshold": 0.0,
            "recoveryScore": 1.0, "velocity": 0.01, "velocityLabel": "STABLE",
        },
        "bottleneck": {
            "type": "FULL_DECOUPLE", "label": "Full Decouple",
            "dropPct": 12.9, "distanceToThreshold": 0.0,
            "recoveryScore": 1.0, "velocity": 0.0, "velocityLabel": "STABLE",
        },
        "perType": {
            "CRYPTO_LEADS": {
                "dropPct": 3.3, "distanceToThreshold": 0.0,
                "recoveryScore": 1.0, "velocity": 0.01, "velocityLabel": "STABLE", "isDecaying": False,
            },
            "FULL_DECOUPLE": {
                "dropPct": 12.9, "distanceToThreshold": 0.0,
                "recoveryScore": 1.0, "velocity": 0.0, "velocityLabel": "STABLE", "isDecaying": False,
            },
            "SEMI_LEADS": {
                "dropPct": 42.3, "distanceToThreshold": 22.3,
                "recoveryScore": 0.257, "velocity": -0.12, "velocityLabel": "DETERIORATING", "isDecaying": True,
            },
        },
        "ifLeaderRecovers": "NEUTRAL",
        "interpretation": "Regime is NEUTRAL — signals are live and actionable. No transition needed.",
    }


def _regime_proximity_systemic():
    """Proximity data for SYSTEMIC regime — deep risk-off, all types decaying."""
    return {
        "score": 0.012,
        "label": "ENTRENCHED",
        "scale": "0.0 = deep SYSTEMIC, 1.0 = transition imminent",
        "regime": "SYSTEMIC",
        "regimeDurationDays": 12,
        "transitionsNeeded": 2,
        "leader": {
            "type": "SEMI_LEADS", "label": "Semi Leads",
            "dropPct": 44.1, "distanceToThreshold": 24.1,
            "recoveryScore": 0.196, "velocity": -0.41, "velocityLabel": "DETERIORATING",
        },
        "bottleneck": {
            "type": "FULL_DECOUPLE", "label": "Full Decouple",
            "dropPct": 48.0, "distanceToThreshold": 28.0,
            "recoveryScore": 0.067, "velocity": -0.45, "velocityLabel": "DETERIORATING",
        },
        "perType": {
            "SEMI_LEADS": {
                "dropPct": 44.1, "distanceToThreshold": 24.1,
                "recoveryScore": 0.196, "velocity": -0.41, "velocityLabel": "DETERIORATING", "isDecaying": True,
            },
            "FULL_DECOUPLE": {
                "dropPct": 48.0, "distanceToThreshold": 28.0,
                "recoveryScore": 0.067, "velocity": -0.45, "velocityLabel": "DETERIORATING", "isDecaying": True,
            },
            "CRYPTO_LEADS": {
                "dropPct": 50.6, "distanceToThreshold": 30.6,
                "recoveryScore": 0.0, "velocity": -0.47, "velocityLabel": "DETERIORATING", "isDecaying": True,
            },
        },
        "ifLeaderRecovers": "EARNINGS",
        "interpretation": "All 3 signal types are significantly below their all-time reliability scores. The system is deeply entrenched in SYSTEMIC. Recovery requires at least 2 types to rebuild above the 20% decay threshold — typically weeks of sustained market stabilization.",
    }


def _transition_forecast_neutral():
    """Transition forecast when already in NEUTRAL — no forecast needed."""
    return {
        "status": "AT_NEUTRAL",
        "message": "Regime is already NEUTRAL — signals are live and actionable. No transition forecast needed.",
        "currentTrajectory": None,
        "estimatedTransition": None,
        "recoveryRequirements": None,
        "projectedRegime": "NEUTRAL",
        "historicalCalibration": None,
        "backtestValidation": None,
    }


def _transition_forecast_systemic():
    """Transition forecast during SYSTEMIC — full predictor output."""
    return {
        "status": "NO_RECOVERY_SIGNAL",
        "message": "All 3 signal types are deteriorating. No velocity-based recovery estimate available. Historical calibration: SYSTEMIC periods lasted 4-13 days (median 8.5d, current: 12d). If recovery begins now, confidence bands: optimistic ~5d, base ~7d, pessimistic ~15d. Key bottleneck: Full Decouple (28 pct above threshold).",
        "currentTrajectory": {
            "allDeteriorating": True,
            "anyRecovering": False,
            "recoveringCount": 0,
            "deterioratingCount": 3,
            "stableCount": 0,
            "fastestRecovering": {"type": "SEMI_LEADS", "velocity": -0.41},
            "slowestRecovering": {"type": "CRYPTO_LEADS", "velocity": -0.47},
        },
        "estimatedTransition": {
            "pessimistic": {
                "days": 15,
                "date": "2026-04-02",
                "scenario": "Slowest observed SYSTEMIC recovery rate (1.92 pct/day)",
            },
            "base": {
                "days": 7,
                "date": "2026-03-25",
                "scenario": "Median historical SYSTEMIC recovery rate (4.08 pct/day) applied to current distances",
            },
            "optimistic": {
                "days": 5,
                "date": "2026-03-23",
                "scenario": "Fastest observed SYSTEMIC recovery rate (6.25 pct/day) applied to current distances",
            },
        },
        "recoveryRequirements": {
            "condition": "2 of 3 signal types must recover below 20% decay threshold",
            "targetHorizonDays": 14,
            "perType": [
                {"type": "SEMI_LEADS", "label": "Semi Leads", "currentVelocity": -0.41, "requiredVelocity": 0.195, "requiredDailyRecoveryPct": 1.72, "velocityGap": 0.605, "feasibility": "REVERSED"},
                {"type": "FULL_DECOUPLE", "label": "Full Decouple", "currentVelocity": -0.45, "requiredVelocity": 0.208, "requiredDailyRecoveryPct": 2.0, "velocityGap": 0.658, "feasibility": "REVERSED"},
                {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "currentVelocity": -0.47, "requiredVelocity": 0.216, "requiredDailyRecoveryPct": 2.19, "velocityGap": 0.686, "feasibility": "REVERSED"},
            ],
            "leader": {"type": "SEMI_LEADS", "label": "Semi Leads", "distanceToThreshold": 24.1},
            "bottleneck": {"type": "FULL_DECOUPLE", "label": "Full Decouple", "distanceToThreshold": 28.0},
        },
        "projectedRegime": "EARNINGS",
        "typeProjections": [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "distanceToThreshold": 24.1, "velocity": -0.41, "dailyVelocity": -0.082, "daysToThreshold": None, "trajectoryNote": "Deteriorating — moving away from threshold"},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "distanceToThreshold": 28.0, "velocity": -0.45, "dailyVelocity": -0.09, "daysToThreshold": None, "trajectoryNote": "Deteriorating — moving away from threshold"},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "distanceToThreshold": 30.6, "velocity": -0.47, "dailyVelocity": -0.094, "daysToThreshold": None, "trajectoryNote": "Deteriorating — moving away from threshold"},
        ],
        "historicalCalibration": {
            "observedSystemicPeriods": [
                {"entryDate": "2025-11-06", "exitDate": "2025-11-19", "exitTo": "NEUTRAL", "durationDays": 13, "impliedDailyRecoveryPct": 1.92},
                {"entryDate": "2025-11-24", "exitDate": "2025-11-28", "exitTo": "NEUTRAL", "durationDays": 4, "impliedDailyRecoveryPct": 6.25},
            ],
            "periodCount": 2,
            "medianDurationDays": 8.5,
            "fastestRecoveryDays": 4,
            "slowestRecoveryDays": 13,
            "medianRecoveryRatePctPerDay": 4.08,
            "fastestRecoveryRatePctPerDay": 6.25,
            "slowestRecoveryRatePctPerDay": 1.92,
            "currentDurationDays": 12,
            "durationPercentile": 50,
            "durationContext": "Current SYSTEMIC period (12d) is longer than 50% of historical periods",
        },
        "backtestValidation": {
            "method": "Retrospective analysis: apply median recovery rate to historical SYSTEMIC entry distances, compare predicted vs actual exit dates",
            "limitation": "Historical per-type velocity snapshots are not stored. Backtest uses implied recovery rates computed from observed transition durations rather than actual velocity data at time of prediction.",
            "transitions": [
                {"period": "2025-11-06 -> 2025-11-19", "exitTo": "NEUTRAL", "actualDays": 13, "modelPredictedDays": 7, "errorDays": -6, "absError": 6, "impliedRecoveryRate": 1.92, "note": "Within normal range for velocity-based prediction"},
                {"period": "2025-11-24 -> 2025-11-28", "exitTo": "NEUTRAL", "actualDays": 4, "modelPredictedDays": 7, "errorDays": 3, "absError": 3, "impliedRecoveryRate": 6.25, "note": "Rapid recovery — likely driven by sudden market reversal"},
            ],
            "meanAbsoluteErrorDays": 4.5,
            "sampleSize": 2,
            "assessment": "Moderate predictive accuracy (MAE 3-7 days)",
        },
    }


def regime_current():
    return {
        "state": "NEUTRAL",
        "id": "NEUTRAL",
        "confidence": 72,
        "isAlert": False,
        "action": "Hold current allocations — no regime-driven rebalancing required.",
        "targetWeights": {
            "NVDA": 0.25, "AMD": 0.20, "AVGO": 0.20,
            "MRVL": 0.15, "ASML": 0.20,
        },
        "signals": {
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "currentScore": 45,
                "allTimeScore": 78,
                "dropPct": 42.3,
                "decaying": True,
            },
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "currentScore": 88,
                "allTimeScore": 91,
                "dropPct": 3.3,
                "decaying": False,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "currentScore": 61,
                "allTimeScore": 70,
                "dropPct": 12.9,
                "decaying": False,
            },
        },
        "backtestContext": {
            "optimalWindow": 60,
            "accuracy": 60,
            "avgLeadTime": 27.0,
            "fpRate": 40,
        },
        "regimeProximity": _regime_proximity_neutral(),
        "transitionForecast": _transition_forecast_neutral(),
        "hitRateDecayModel": _hit_rate_decay_neutral(),
        "capitalPreservation": _capital_preservation_neutral(),
        "optimalReEntry": _optimal_reentry_neutral(),
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def rebalancing_queue():
    return {
        "regimeState": "NEUTRAL",
        "confidence": 72,
        "trades": [
            {
                "asset": "RNDR",
                "direction": "BUY",
                "currentPct": 5.0,
                "targetPct": 12.0,
                "deltaPct": 7.0,
                "urgency": "immediate",
                "urgencyLabel": "Immediate — CRYPTO_LEADS divergence active",
                "drivingSignal": "CRYPTO_LEADS",
                "regime": "NEUTRAL",
            },
            {
                "asset": "TAO",
                "direction": "BUY",
                "currentPct": 3.0,
                "targetPct": 8.0,
                "deltaPct": 5.0,
                "urgency": "immediate",
                "urgencyLabel": "Immediate — CRYPTO_LEADS divergence active",
                "drivingSignal": "CRYPTO_LEADS",
                "regime": "NEUTRAL",
            },
            {
                "asset": "AKT",
                "direction": "HOLD",
                "currentPct": 6.0,
                "targetPct": 6.0,
                "deltaPct": 0.0,
                "urgency": "watch",
                "urgencyLabel": "Watch — no active divergence",
                "drivingSignal": "NONE",
                "regime": "NEUTRAL",
            },
        ],
        "tradeCount": 3,
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def signals_reliability():
    return {
        "window": 30,
        "regimeAlert": {
            "triggered": False,
            "count": 1,
            "types": ["SEMI_LEADS"],
            "msg": "1 signal type shows reliability decay",
        },
        "types": {
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "score": 45,
                "reliabilityLabel": "DEGRADED",
                "allTimeScore": 78.0,
                "currentRolling": 45.0,
                "dropPct": 42.3,
                "isDecaying": True,
                "freshness": "Stale",
                "firstDecayDate": "2026-02-20",
            },
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "score": 88,
                "reliabilityLabel": "STRONG",
                "allTimeScore": 91.0,
                "currentRolling": 88.0,
                "dropPct": 3.3,
                "isDecaying": False,
                "freshness": "Fresh",
                "firstDecayDate": None,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "score": 61,
                "reliabilityLabel": "MODERATE",
                "allTimeScore": 70.0,
                "currentRolling": 61.0,
                "dropPct": 12.9,
                "isDecaying": False,
                "freshness": "Recent",
                "firstDecayDate": None,
            },
        },
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def _hit_rate_decay_neutral():
    """Hit rate decay model under NEUTRAL — no decay needed."""
    return {
        "status": "AT_NEUTRAL",
        "message": "Regime is NEUTRAL — hit rates are at baseline. No duration decay adjustment needed.",
        "modelVersion": "exponential-duration-v1",
        "regimeDurationDays": 45,
        "medianHistoricalDuration": None,
        "historicalPeriodCount": 0,
        "perType": None,
        "sensitivityBands": None,
        "calibration": None,
    }


def _hit_rate_decay_systemic():
    """Hit rate decay model under SYSTEMIC — active decay with 12-day duration."""
    return {
        "status": "ACTIVE",
        "message": "Hit rate decay model active for 12-day SYSTEMIC period. 3 of 3 applicable signal types have decayed below their static aggregate rates. All applicable types are below aggregate — static confidence values on this API overstate current hit probability.",
        "modelVersion": "exponential-duration-v1",
        "regimeDurationDays": 12,
        "medianHistoricalDuration": 8.5,
        "historicalPeriodCount": 2,
        "noiseThreshold": 0.10,
        "perType": {
            "CRYPTO_LEADS": {
                "label": "Crypto Leads",
                "neutralRate": 0.82,
                "systemicAggregate": 0.20,
                "adjustedConfidence": 0.1124,
                "halfLifeDays": 4.17,
                "decayConstant": 0.1661,
                "decayVelocityPerDay": -0.018672,
                "daysToNoise": 12.7,
                "daysToNoiseRemaining": 0.7,
                "aggregateBias": {
                    "pct": 78.0,
                    "direction": "OVERSTATED",
                    "explanation": "Static rate (20%) overstates current hit probability by 78%. The aggregate includes early-period observations when signals still carried residual edge.",
                },
                "decayApplicable": True,
                "backtestPredictions": [
                    {"day": 4, "predicted": 0.4173, "staticRate": 0.20, "delta": 0.2173, "note": "Above aggregate — early-period signal retention"},
                    {"day": 13, "predicted": 0.0920, "staticRate": 0.20, "delta": -0.1080, "note": "Below aggregate — extended-period decay"},
                ],
                "nNeutral": 17,
                "nSystemic": 5,
            },
            "SEMI_LEADS": {
                "label": "Semi Leads",
                "neutralRate": 0.12,
                "systemicAggregate": 0.10,
                "adjustedConfidence": 0.0928,
                "halfLifeDays": 32.28,
                "decayConstant": 0.02147,
                "decayVelocityPerDay": -0.001992,
                "daysToNoise": 8.5,
                "daysToNoiseRemaining": 0.0,
                "aggregateBias": {
                    "pct": 7.8,
                    "direction": "OVERSTATED",
                    "explanation": "Static rate (10%) overstates current hit probability by 8%. The aggregate includes early-period observations when signals still carried residual edge.",
                },
                "decayApplicable": True,
                "backtestPredictions": [
                    {"day": 4, "predicted": 0.1101, "staticRate": 0.10, "delta": 0.0101, "note": "Above aggregate — early-period signal retention"},
                    {"day": 13, "predicted": 0.0904, "staticRate": 0.10, "delta": -0.0096, "note": "Below aggregate — extended-period decay"},
                ],
                "nNeutral": 8,
                "nSystemic": 10,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decouple",
                "neutralRate": 0.50,
                "systemicAggregate": 0.25,
                "adjustedConfidence": 0.1879,
                "halfLifeDays": 8.50,
                "decayConstant": 0.08155,
                "decayVelocityPerDay": -0.015323,
                "daysToNoise": 13.8,
                "daysToNoiseRemaining": 1.8,
                "aggregateBias": {
                    "pct": 33.1,
                    "direction": "OVERSTATED",
                    "explanation": "Static rate (25%) overstates current hit probability by 33%. The aggregate includes early-period observations when signals still carried residual edge.",
                },
                "decayApplicable": True,
                "backtestPredictions": [
                    {"day": 4, "predicted": 0.3611, "staticRate": 0.25, "delta": 0.1111, "note": "Above aggregate — early-period signal retention"},
                    {"day": 13, "predicted": 0.1726, "staticRate": 0.25, "delta": -0.0774, "note": "Below aggregate — extended-period decay"},
                ],
                "nNeutral": 6,
                "nSystemic": 4,
            },
        },
        "sensitivityBands": {
            "CRYPTO_LEADS": {
                "conservative": {"label": "Faster decay (half-life -30%)", "halfLifeDays": 2.92, "adjustedConfidence": 0.0637},
                "base": {"label": "Calibrated estimate", "halfLifeDays": 4.17, "adjustedConfidence": 0.1124},
                "optimistic": {"label": "Slower decay (half-life +30%)", "halfLifeDays": 5.42, "adjustedConfidence": 0.1729},
            },
            "SEMI_LEADS": {
                "conservative": {"label": "Faster decay (half-life -30%)", "halfLifeDays": 22.60, "adjustedConfidence": 0.0891},
                "base": {"label": "Calibrated estimate", "halfLifeDays": 32.28, "adjustedConfidence": 0.0928},
                "optimistic": {"label": "Slower decay (half-life +30%)", "halfLifeDays": 41.96, "adjustedConfidence": 0.0955},
            },
            "FULL_DECOUPLE": {
                "conservative": {"label": "Faster decay (half-life -30%)", "halfLifeDays": 5.95, "adjustedConfidence": 0.1173},
                "base": {"label": "Calibrated estimate", "halfLifeDays": 8.50, "adjustedConfidence": 0.1879},
                "optimistic": {"label": "Slower decay (half-life +30%)", "halfLifeDays": 11.05, "adjustedConfidence": 0.2565},
            },
        },
        "calibration": {
            "model": "exponential-duration-v1",
            "formula": "adjustedConfidence(t) = neutralRate * exp(-lambda * t), where lambda = ln(neutralRate / systemicRate) / medianDuration",
            "calibrationProperty": "At the median historical SYSTEMIC duration (8.5d), the model output equals the empirical SYSTEMIC aggregate hit rate.",
            "medianDurationDays": 8.5,
            "limitations": [
                "Only 2 historical SYSTEMIC periods available for calibration.",
                "Per-period hit rates are not separable from the aggregate.",
                "Zero-floor assumption: adjusted confidence approaches 0 for very long durations.",
                "Onset lag: the model assumes instantaneous decay at SYSTEMIC onset.",
            ],
            "biasAnalysis": "The aggregate SYSTEMIC hit rates are duration-blind averages that overstate current hit probability for extended durations (>9d).",
        },
    }


def _optimal_reentry_neutral():
    """Optimal re-entry timing under NEUTRAL — entry available now."""
    return {
        "status": "AT_NEUTRAL",
        "modelVersion": "optimal-reentry-v1",
        "message": "Regime is NEUTRAL — signals are already actionable. No re-entry timing needed.",
        "riskFreeRate14d": 0.150,
        "riskFreeRateDescription": "USDC yield at 4% APY over 14-day horizon",
        "optimalEntryDay": 0,
        "crossoverDay": None,
        "crossoverDate": None,
        "crossoverMessage": "Already at NEUTRAL — entry available now.",
        "firstTypeToCross": None,
        "entryThreshold": None,
        "perType": None,
        "aggregateCurve": None,
        "sensitivityBands": None,
        "upstreamDependencies": None,
        "limitations": None,
    }


def _optimal_reentry_systemic():
    """Optimal re-entry timing under SYSTEMIC — full prescriptive output."""
    return {
        "status": "CONDITIONAL",
        "modelVersion": "optimal-reentry-v1",
        "message": "Re-entry timing model active (CONDITIONAL — all velocities negative, using historical transition rates). Regime day 13.",
        "methodology": "E[R|d] = P(NEUTRAL|d) * E[R|NEUTRAL] + P(SYSTEMIC|d) * [h_adj(d) * R_win + (1-h_adj(d)) * R_loss]. P(NEUTRAL|d) from uniform CDF over transition bounds. h_adj(d) from exponential decay. R_win/R_loss from cross-regime decomposition. Crossover = first day where E[R] > risk-free rate (0.15% per 14d).",
        "regimeDurationDays": 13,
        "riskFreeRate14d": 0.150,
        "riskFreeRateDescription": "USDC yield at 4% APY over 14-day horizon",
        "transitionBounds": {
            "optimisticDays": 5,
            "baseDays": 7,
            "pessimisticDays": 15,
            "isConditional": True,
            "survivalModel": "Uniform CDF: P(NEUTRAL|d) = 0 for d <= optimistic, linear ramp to 1.0 at pessimistic",
        },
        "optimalEntryDay": None,
        "crossoverDay": None,
        "crossoverDate": None,
        "crossoverMessage": "No crossover in 30-day forecast window. All signal types currently deteriorating — transition bounds are conditional on recovery beginning. Monitor for velocity reversal.",
        "firstTypeToCross": {
            "type": "CRYPTO_LEADS",
            "label": "Crypto Leads",
            "crossoverDay": 12,
            "crossoverDate": "2026-03-31",
            "reason": "CRYPTO_LEADS has the fastest confidence recovery on regime flip due to its decay dynamics (half-life 4.17d).",
        },
        "entryThreshold": {
            "minimumProximityScore": 0.15,
            "currentProximityScore": 0.012,
            "currentProximityLabel": "ENTRENCHED",
            "recommendation": "Proximity below threshold — re-entry not imminent. Check back when proximity reaches 0.15.",
        },
        "perType": {
            "CRYPTO_LEADS": {
                "label": "Crypto Leads",
                "neutralExpectedReturn": 8.24,
                "currentExpectedReturn": -13.073,
                "decomposition": {"winReturn": 13.48, "lossReturn": -15.62},
                "halfLifeDays": 4.17,
                "crossoverDay": 12,
                "crossoverDate": "2026-03-31",
                "crossoverMessage": "EV crosses risk-free on forward day 12 (regime day 25)",
                "sampleCurve": [
                    {"day": 0, "regimeDay": 13, "pNeutral": 0.0, "adjustedHitRate": 0.0917, "expectedReturn": -13.073, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 5, "regimeDay": 18, "pNeutral": 0.0, "adjustedHitRate": 0.0267, "expectedReturn": -14.844, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 7, "regimeDay": 20, "pNeutral": 0.2, "adjustedHitRate": 0.1752, "expectedReturn": -10.462, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 10, "regimeDay": 23, "pNeutral": 0.5, "adjustedHitRate": 0.4127, "expectedReturn": -3.258, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 14, "regimeDay": 27, "pNeutral": 0.9, "adjustedHitRate": 0.7390, "expectedReturn": 6.503, "kellyFraction": 0.2214, "exceedsRiskFree": True},
                    {"day": 21, "regimeDay": 34, "pNeutral": 1.0, "adjustedHitRate": 0.82, "expectedReturn": 8.24, "kellyFraction": 0.2108, "exceedsRiskFree": True},
                ],
            },
            "SEMI_LEADS": {
                "label": "Semi Leads",
                "neutralExpectedReturn": -14.60,
                "currentExpectedReturn": -14.60,
                "decomposition": {"winReturn": -14.60, "lossReturn": -14.60},
                "halfLifeDays": 32.28,
                "crossoverDay": None,
                "crossoverDate": None,
                "crossoverMessage": "No crossover in 30-day window — negative EV even in NEUTRAL (not a tradeable signal type)",
                "sampleCurve": [
                    {"day": 0, "regimeDay": 13, "pNeutral": 0.0, "adjustedHitRate": 0.0928, "expectedReturn": -14.60, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 14, "regimeDay": 27, "pNeutral": 0.9, "adjustedHitRate": 0.1148, "expectedReturn": -14.60, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 30, "regimeDay": 43, "pNeutral": 1.0, "adjustedHitRate": 0.12, "expectedReturn": -14.60, "kellyFraction": 0.0, "exceedsRiskFree": False},
                ],
            },
            "FULL_DECOUPLE": {
                "label": "Full Decouple",
                "neutralExpectedReturn": -6.55,
                "currentExpectedReturn": -8.842,
                "decomposition": {"winReturn": -3.05, "lossReturn": -10.05},
                "halfLifeDays": 8.50,
                "crossoverDay": None,
                "crossoverDate": None,
                "crossoverMessage": "No crossover in 30-day window — negative EV even in NEUTRAL (not a tradeable signal type)",
                "sampleCurve": [
                    {"day": 0, "regimeDay": 13, "pNeutral": 0.0, "adjustedHitRate": 0.1708, "expectedReturn": -8.842, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 14, "regimeDay": 27, "pNeutral": 0.9, "adjustedHitRate": 0.4547, "expectedReturn": -6.806, "kellyFraction": 0.0, "exceedsRiskFree": False},
                    {"day": 30, "regimeDay": 43, "pNeutral": 1.0, "adjustedHitRate": 0.50, "expectedReturn": -6.55, "kellyFraction": 0.0, "exceedsRiskFree": False},
                ],
            },
        },
        "aggregateCurve": [
            {"day": 0, "regimeDay": 13, "pNeutral": 0.0, "weightedExpectedReturn": -11.758, "weightedKellyFraction": 0.0, "weightedHitRate": 0.0775, "exceedsRiskFree": False},
            {"day": 7, "regimeDay": 20, "pNeutral": 0.2, "weightedExpectedReturn": -10.186, "weightedKellyFraction": 0.0, "weightedHitRate": 0.1088, "exceedsRiskFree": False},
            {"day": 14, "regimeDay": 27, "pNeutral": 0.9, "weightedExpectedReturn": -5.012, "weightedKellyFraction": 0.065, "weightedHitRate": 0.3692, "exceedsRiskFree": False},
            {"day": 21, "regimeDay": 34, "pNeutral": 1.0, "weightedExpectedReturn": -4.826, "weightedKellyFraction": 0.062, "weightedHitRate": 0.3923, "exceedsRiskFree": False},
            {"day": 30, "regimeDay": 43, "pNeutral": 1.0, "weightedExpectedReturn": -4.685, "weightedKellyFraction": 0.059, "weightedHitRate": 0.4012, "exceedsRiskFree": False},
        ],
        "sensitivityBands": {
            "optimistic": {
                "scenario": "Faster transition (-30%) + slower decay (+30% half-life) — earliest plausible re-entry",
                "transitionBounds": {"optimistic": 4, "pessimistic": 11},
                "aggregateCrossoverDay": None,
                "aggregateCrossoverDate": None,
                "perType": {
                    "CRYPTO_LEADS": {"crossoverDay": 9, "crossoverDate": "2026-03-28", "halfLifeDays": 5.42},
                    "SEMI_LEADS": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 41.96},
                    "FULL_DECOUPLE": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 11.05},
                },
            },
            "base": {
                "scenario": "Calibrated estimate — median historical rates",
                "transitionBounds": {"optimistic": 5, "pessimistic": 15},
                "aggregateCrossoverDay": None,
                "aggregateCrossoverDate": None,
                "perType": {
                    "CRYPTO_LEADS": {"crossoverDay": 12, "crossoverDate": "2026-03-31", "halfLifeDays": 4.17},
                    "SEMI_LEADS": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 32.28},
                    "FULL_DECOUPLE": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 8.50},
                },
            },
            "pessimistic": {
                "scenario": "Slower transition (+50%) + faster decay (-30% half-life) — latest plausible re-entry",
                "transitionBounds": {"optimistic": 8, "pessimistic": 23},
                "aggregateCrossoverDay": None,
                "aggregateCrossoverDate": None,
                "perType": {
                    "CRYPTO_LEADS": {"crossoverDay": 17, "crossoverDate": "2026-04-05", "halfLifeDays": 2.92},
                    "SEMI_LEADS": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 22.60},
                    "FULL_DECOUPLE": {"crossoverDay": None, "crossoverDate": None, "halfLifeDays": 5.95},
                },
            },
        },
        "upstreamDependencies": {
            "hitRateDecayModel": {
                "status": "ACTIVE",
                "available": True,
                "regimeDurationDays": 13,
                "biasDirection": "If half-life OVERSTATED (actual decay faster), re-entry estimate is TOO EARLY by ~1-2 days. If UNDERSTATED, estimate is too late.",
                "impact": "Controls h_adj(d) — the adjusted hit rate at each future day",
            },
            "transitionForecast": {
                "status": "NO_RECOVERY_SIGNAL",
                "available": True,
                "isConditional": True,
                "estimatedDays": {"optimistic": 5, "base": 7, "pessimistic": 15},
                "biasDirection": "CONDITIONAL — historical rates only. If current period exceeds historical median, estimate is TOO OPTIMISTIC.",
                "impact": "Determines P(NEUTRAL|d) via uniform CDF between transition bounds",
            },
            "regimeProximity": {
                "status": "ENTRENCHED",
                "available": True,
                "score": 0.012,
                "biasDirection": "Advisory only — does not affect EV calculation. Informs entryThreshold.",
                "impact": "Used for entryThreshold recommendation",
            },
            "capitalPreservationDecomposition": {
                "status": "DERIVED",
                "available": True,
                "biasDirection": "Assumes regime-invariant win/loss magnitudes. If SYSTEMIC losses are fatter-tailed, model UNDERSTATES cost of early entry.",
                "impact": "Provides R_win and R_loss per type via cross-regime 2-equation system",
            },
        },
        "limitations": {
            "summary": "Synthesizes 4 upstream modules — errors compound across the dependency chain. Net bias direction: PREMATURE.",
            "netBiasDirection": "PREMATURE",
            "netBiasExplanation": "Decay overstatement -> earlier re-entry. Velocity extrapolation -> optimistic transition -> earlier re-entry. Regime-invariant returns -> understated SYSTEMIC losses -> earlier re-entry. 3 of 4 upstream biases push re-entry earlier. Treat the crossover date as a LOWER BOUND (earliest plausible), not a point estimate.",
            "cascadeRisk": "A 20% decay overstatement + 15% kelly oversize + 10% transition timing error = ~35-45% combined overstatement of entry attractiveness. Sensitivity bands attempt to bound this.",
            "specificLimitations": [
                "P(NEUTRAL|d) modeled as uniform CDF between optimistic (5d) and pessimistic (15d) transition bounds. With n=2 historical SYSTEMIC periods, the distribution shape is unconstrained.",
                "Kelly fraction assumes independent bets but regime-conditioned returns have serial correlation (14d horizon with overlapping windows).",
                "Cross-regime decomposition assumes regime-invariant win/loss return magnitudes. SYSTEMIC losses may exhibit fatter tails.",
                "Velocity measured over ~5-day window. Sudden market reversal would not update P(NEUTRAL) until the next 15-minute refresh cycle.",
                "Model optimizes for a single 14-day horizon. Shorter/longer horizons shift the crossover date.",
                "CONDITIONAL FORECAST: All velocities currently negative. Transition bounds use historical recovery rates, not live velocity.",
            ],
        },
    }


def _capital_preservation_neutral():
    return {
        "status": "AT_NEUTRAL",
        "message": "Capital preservation model only active during SYSTEMIC regime with decay model running.",
    }

def _capital_preservation_systemic():
    return {
        "status": "ACTIVE",
        "modelVersion": "counterfactual-pnl-v1",
        "methodology": "Cross-regime decomposition solves for per-type win/loss returns using NEUTRAL and SYSTEMIC (hitRate, avgReturn) as simultaneous equations. Duration-decayed hit rates from hitRateDecayModel compute adjusted expected returns at each NO_TRADE entry timestamp. counterfactualLoss = negative of adjusted expected return (positive = loss avoided by not trading).",
        "regimeDurationDays": 12,
        "regimeStartEstimate": "2025-11-06",
        "noTradeEntriesEvaluated": 48,
        "liveEntriesEvaluated": 40,
        "backfilledEntriesEvaluated": 8,
        "dateRange": {"first": "2025-11-07T00:16:01Z", "last": "2025-11-18T23:46:02Z"},
        "aggregate": {
            "totalDrawdownAvoided": 540.96,
            "avgCounterfactualLossPerEntry": 11.27,
            "liveAvgCounterfactualLoss": 11.35,
            "worstSingleEntry": 11.75,
            "bestSingleEntry": 9.20,
            "independentTradeWindows": 0.86,
            "positionAdjustedDrawdown": 9.69,
            "unit": "percent — expected 14-day return per equal-weight signal portfolio"
        },
        "perType": {
            "CRYPTO_LEADS": {
                "label": "Crypto Leads",
                "decomposition": {"winReturn": 13.48, "lossReturn": -15.62},
                "staticExpectedReturn": -9.80,
                "adjustedExpectedReturn": -12.50,
                "staticVsAdjustedBias": -21.6,
                "signalCount": 8,
                "weight": 0.471,
                "sampleSize": {"neutral": 17, "systemic": 5}
            },
            "SEMI_LEADS": {
                "label": "Semi Leads",
                "decomposition": {"winReturn": -14.60, "lossReturn": -14.60},
                "staticExpectedReturn": -14.60,
                "adjustedExpectedReturn": -14.60,
                "staticVsAdjustedBias": 0,
                "signalCount": 3,
                "weight": 0.176,
                "sampleSize": {"neutral": 8, "systemic": 10}
            },
            "FULL_DECOUPLE": {
                "label": "Full Decouple",
                "decomposition": {"winReturn": -3.05, "lossReturn": -10.05},
                "staticExpectedReturn": -8.30,
                "adjustedExpectedReturn": -8.70,
                "staticVsAdjustedBias": -4.6,
                "signalCount": 6,
                "weight": 0.353,
                "sampleSize": {"neutral": 6, "systemic": 4}
            }
        },
        "inverseSignal": {
            "type": "CRYPTO_LEADS",
            "regimeDay": 12,
            "adjustedHitRate": 0.1124,
            "longExpectedReturn": -12.50,
            "shortExpectedReturn": 12.50,
            "viability": "MARGINAL",
            "rationale": "At day 12 of SYSTEMIC, CRYPTO_LEADS long side has 11.2% hit rate. Short expected return of 12.50%.",
            "limitations": ["Only n=5 SYSTEMIC observations."]
        },
        "sampleEntries": [
            {
                "cycle_key": "2025-11-18T23:30:00Z",
                "timestamp": "2025-11-18T23:31:01Z",
                "regimeDay": 12.0,
                "counterfactualLoss": 11.27,
                "expectedReturnIfTraded": -11.27,
                "breakdown": {
                    "CRYPTO_LEADS": {"adjustedConfidence": 0.1124, "adjustedExpectedReturn": -12.50, "weight": 0.471},
                    "SEMI_LEADS": {"adjustedConfidence": 0.0920, "adjustedExpectedReturn": -14.60, "weight": 0.176},
                    "FULL_DECOUPLE": {"adjustedConfidence": 0.1900, "adjustedExpectedReturn": -8.70, "weight": 0.353}
                }
            }
        ],
        "calibration": {
            "crossRegimeMethod": "Solves N*Rw+(1-N)*Rl=Rn and S*Rw+(1-S)*Rl=Rs per signal type.",
            "decayDependency": "Inherits all limitations of hitRateDecayModel.",
            "limitations": [
                "Win/loss return magnitudes assumed regime-invariant.",
                "Entries are NOT independent — 14d horizon with 15-min cycles."
            ]
        }
    }

def signals_filtered():
    return {
        "decision": "TRADE",
        "decisionReason": "NEUTRAL regime — 2 actionable signals (CRYPTO_LEADS). Hit rate 82% with 8.2% avg return under this regime.",
        "regimeProximity": _regime_proximity_neutral(),
        "transitionForecast": _transition_forecast_neutral(),
        "hitRateDecayModel": _hit_rate_decay_neutral(),
        "capitalPreservation": _capital_preservation_neutral(),
        "optimalReEntry": _optimal_reentry_neutral(),
        "regimeId": "NEUTRAL",
        "regimeLabel": "Neutral — no systemic stress detected",
        "regimeConfidence": 72,
        "totalSignals": 5,
        "actionableCount": 2,
        "suppressedCount": 2,
        "ambiguousCount": 1,
        "filterRules": {
            "CRYPTO_LEADS": {
                "label": "Crypto Leads Semi",
                "classification": "ACTIONABLE",
                "hitRate": 0.82,
                "n": 22,
                "avgRet": 8.24,
            },
            "SEMI_LEADS": {
                "label": "Semi Leads Crypto",
                "classification": "SUPPRESS",
                "hitRate": 0.12,
                "n": 16,
                "avgRet": -14.60,
            },
            "FULL_DECOUPLE": {
                "label": "Full Decoupling",
                "classification": "AMBIGUOUS",
                "hitRate": 0.80,
                "n": 5,
                "avgRet": 3.83,
            },
        },
        "signals": [
            {
                "pair": "NVDA/RNDR",
                "type": "CRYPTO_LEADS",
                "typeLabel": "Crypto Leads Semi",
                "conviction": 85,
                "reliability": 88,
                "reliabilityLabel": "STRONG",
                "regimeFilter": "ACTIONABLE",
                "regimeFilterHitRate": 0.82,
                "regimeFilterN": 22,
                "regimeFilterAvgRet": 8.24,
                "ticker": "RNDR",
                "semiTicker": "NVDA",
                "action": "BUY",
                "signal_type": "CRYPTO_LEADS",
                "confidence": 0.82,
                "hit_rate": 0.82,
                "avg_return": 8.24,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
                "adjustedConfidence": 0.82,
                "decayHalfLifeDays": None,
                "daysToNoise": None,
            },
            {
                "pair": "AMD/TAO",
                "type": "CRYPTO_LEADS",
                "typeLabel": "Crypto Leads Semi",
                "conviction": 71,
                "reliability": 88,
                "reliabilityLabel": "STRONG",
                "regimeFilter": "ACTIONABLE",
                "regimeFilterHitRate": 0.82,
                "regimeFilterN": 22,
                "regimeFilterAvgRet": 8.24,
                "ticker": "TAO",
                "semiTicker": "AMD",
                "action": "BUY",
                "signal_type": "CRYPTO_LEADS",
                "confidence": 0.82,
                "hit_rate": 0.82,
                "avg_return": 8.24,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
                "adjustedConfidence": 0.82,
                "decayHalfLifeDays": None,
                "daysToNoise": None,
            },
            {
                "pair": "AVGO/AKT",
                "type": "SEMI_LEADS",
                "typeLabel": "Semi Leads Crypto",
                "conviction": 60,
                "reliability": 45,
                "reliabilityLabel": "DEGRADED",
                "regimeFilter": "SUPPRESS",
                "regimeFilterHitRate": 0.12,
                "regimeFilterN": 16,
                "regimeFilterAvgRet": -14.60,
                "ticker": "AKT",
                "semiTicker": "AVGO",
                "action": "AVOID",
                "signal_type": "SEMI_LEADS",
                "confidence": 0.12,
                "hit_rate": 0.12,
                "avg_return": -14.60,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
                "adjustedConfidence": 0.12,
                "decayHalfLifeDays": None,
                "daysToNoise": None,
            },
            {
                "pair": "MRVL/FET",
                "type": "SEMI_LEADS",
                "typeLabel": "Semi Leads Crypto",
                "conviction": 55,
                "reliability": 45,
                "reliabilityLabel": "DEGRADED",
                "regimeFilter": "SUPPRESS",
                "regimeFilterHitRate": 0.12,
                "regimeFilterN": 16,
                "regimeFilterAvgRet": -14.60,
                "ticker": "FET",
                "semiTicker": "MRVL",
                "action": "AVOID",
                "signal_type": "SEMI_LEADS",
                "confidence": 0.12,
                "hit_rate": 0.12,
                "avg_return": -14.60,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
                "adjustedConfidence": 0.12,
                "decayHalfLifeDays": None,
                "daysToNoise": None,
            },
            {
                "pair": "ASML/RNDR",
                "type": "FULL_DECOUPLE",
                "typeLabel": "Full Decoupling",
                "conviction": 40,
                "reliability": 61,
                "reliabilityLabel": "MODERATE",
                "regimeFilter": "AMBIGUOUS",
                "regimeFilterHitRate": 0.80,
                "regimeFilterN": 5,
                "regimeFilterAvgRet": 3.83,
                "ticker": "RNDR",
                "semiTicker": "ASML",
                "action": "HOLD",
                "signal_type": "FULL_DECOUPLE",
                "confidence": 0.80,
                "hit_rate": 0.80,
                "avg_return": 3.83,
                "regime": "NEUTRAL",
                "observed_at": _ts(),
                "adjustedConfidence": 0.80,
                "decayHalfLifeDays": None,
                "daysToNoise": None,
            },
        ],
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def regime_history():
    return {
        "windowDays": 90,
        "currentRegime": "NEUTRAL",
        "transitions": [
            {"date": "2026-01-15", "regime": "NEUTRAL", "transitionFrom": None},
            {"date": "2026-01-28", "regime": "EARNINGS", "transitionFrom": "NEUTRAL"},
            {"date": "2026-02-05", "regime": "NEUTRAL", "transitionFrom": "EARNINGS"},
            {"date": "2026-02-18", "regime": "DIVERGENCE", "transitionFrom": "NEUTRAL"},
            {"date": "2026-02-22", "regime": "NEUTRAL", "transitionFrom": "DIVERGENCE"},
        ],
        "transitionCount": 5,
        "timestamp": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
    }


def health():
    return {
        "status": "ok",
        "uptime": 86400,
        "uptimeHuman": "1d 0h",
        "lastRefresh": _ts(),
        "dataAgeSec": 120,
        "isStale": False,
        "refreshCount": 96,
        "dataFresh": True,
        "lastError": None,
        "schemaVersion": "v1.1.0",
    }


# ── Route map ──────────────────────────────────────────────────────────────────

ROUTES = {
    "/regime/current":       regime_current,
    "/rebalancing/queue":    rebalancing_queue,
    "/signals/reliability":  signals_reliability,
    "/signals/filtered":     signals_filtered,
    "/regime/history":       regime_history,
    "/health":               health,
}


# ── HTTP handler ───────────────────────────────────────────────────────────────

class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]  # strip query params
        handler = ROUTES.get(path)
        if handler:
            body = json.dumps(handler(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": f"Not found: {path}",
                "available": list(ROUTES.keys()),
            }).encode())

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[mock] {args[0]} {args[1]} {args[2]}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = PORT
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    try:
        server = HTTPServer(("127.0.0.1", port), MockHandler)
    except OSError as e:
        if e.errno == 98:
            print(f"Error: port {port} is already in use.")
            print(f"Try a different port:  python3 examples/mock_server.py --port 9090")
            print(f"Then set:              export PF_API_URL=http://localhost:9090")
        else:
            print(f"Error starting server: {e}")
        sys.exit(1)
    print(f"Mock API running on http://localhost:{port}")
    print(f"Endpoints: {', '.join(ROUTES.keys())}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
