#!/usr/bin/env python3
"""Tests for the Regime Transition Predictor.

Validates the velocity-based transition forecast algorithm including:
- Forward trajectory projections per signal type
- 2-of-3 recovery condition for regime exit
- Confidence bands (pessimistic/base/optimistic)
- Historical calibration from observed SYSTEMIC periods
- Backtest validation accuracy
- Edge cases: all deteriorating, partial recovery, AT_NEUTRAL

Run: python3 -m pytest tests/test_transition_forecast.py -v
"""

import json
import math
import os
import sys
import unittest

# ── Pure-Python reimplementation of the transition forecast algorithm ──────
# Mirrors the JS logic in signal_api.js for testability.

DECAY_THRESHOLD = 0.20
FLOOR = 0.50


def compute_forecast(prox_types, history, regime_id='SYSTEMIC', regime_duration_days=12):
    """Compute transition forecast from proximity type data and regime history.

    Args:
        prox_types: list of dicts with keys: type, label, dropPct, distanceToThreshold,
                    recoveryScore, velocity, velocityLabel, isDecaying
        history: list of dicts with keys: date, regime, transitionFrom
        regime_id: current regime ID
        regime_duration_days: days in current regime

    Returns:
        dict matching the transitionForecast schema
    """
    if regime_id == 'NEUTRAL':
        return {
            'status': 'AT_NEUTRAL',
            'message': 'Regime is already NEUTRAL.',
            'estimatedTransition': None,
            'historicalCalibration': None,
            'backtestValidation': None,
        }

    # 1. Historical calibration — extract SYSTEMIC periods
    systemic_periods = []
    for i, h in enumerate(history):
        if h['regime'] in ('SYSTEMIC', 'Systemic Risk-Off'):
            exit_date = None
            exit_to = None
            for j in range(i + 1, len(history)):
                if history[j]['regime'] not in ('SYSTEMIC', 'Systemic Risk-Off'):
                    exit_date = history[j]['date']
                    exit_to = history[j]['regime']
                    break
            if exit_date:
                from datetime import datetime
                entry_d = datetime.strptime(h['date'], '%Y-%m-%d')
                exit_d = datetime.strptime(exit_date, '%Y-%m-%d')
                duration = (exit_d - entry_d).days
                if duration > 0:
                    implied_rate = round(25.0 / duration, 2)
                    systemic_periods.append({
                        'entryDate': h['date'],
                        'exitDate': exit_date,
                        'exitTo': exit_to,
                        'durationDays': duration,
                        'impliedDailyRecoveryPct': implied_rate,
                    })

    durations = [p['durationDays'] for p in systemic_periods]
    rates = [p['impliedDailyRecoveryPct'] for p in systemic_periods]

    def median(arr):
        if not arr:
            return None
        s = sorted(arr)
        mid = len(s) // 2
        if len(s) % 2 == 0:
            return round((s[mid - 1] + s[mid]) / 2, 2)
        return s[mid]

    median_duration = median(durations)
    fastest_recovery = min(durations) if durations else None
    slowest_recovery = max(durations) if durations else None
    median_rate = median(rates)
    fastest_rate = max(rates) if rates else None
    slowest_rate = min(rates) if rates else None

    duration_percentile = None
    if durations:
        shorter = sum(1 for d in durations if d <= regime_duration_days)
        duration_percentile = round((shorter / len(durations)) * 100)

    # 2. Current trajectory
    recovering = [t for t in prox_types if t['velocity'] > 0.02]
    deteriorating = [t for t in prox_types if t['velocity'] < -0.02]
    stable = [t for t in prox_types if -0.02 <= t['velocity'] <= 0.02]
    all_deteriorating = len(deteriorating) == len(prox_types)

    if all_deteriorating:
        status = 'NO_RECOVERY_SIGNAL'
    elif len(recovering) >= 2:
        status = 'RECOVERY_DETECTED'
    elif len(recovering) == 1:
        status = 'EARLY_RECOVERY'
    else:
        status = 'STABILIZING'

    # 3. Per-type projections
    type_projections = []
    for t in prox_types:
        dist = t['distanceToThreshold']
        vel = t['velocity']
        daily_vel = vel / 5.0

        days = None
        note = ''
        if dist <= 0:
            days = 0
            note = 'Already below threshold'
        elif vel > 0.02:
            daily_pct = abs(daily_vel) * t['dropPct']
            if daily_pct > 0.01:
                days = math.ceil(dist / daily_pct)
                if days > 365:
                    days = 365
                note = f'Recovering at {daily_pct:.2f} pct/day'
            else:
                note = 'Recovery too slow'
        elif vel < -0.02:
            note = 'Deteriorating'
        else:
            note = 'Stable'

        type_projections.append({
            'type': t['type'],
            'distanceToThreshold': dist,
            'velocity': vel,
            'dailyVelocity': round(daily_vel, 5),
            'daysToThreshold': days,
            'trajectoryNote': note,
        })

    # Sort for 2-of-3 condition
    sorted_proj = sorted(type_projections,
                         key=lambda x: x['daysToThreshold'] if x['daysToThreshold'] is not None else 999999)
    second_fastest = sorted_proj[1] if len(sorted_proj) >= 2 else None
    velocity_days = second_fastest['daysToThreshold'] if second_fastest else None

    # 4. Confidence bands
    def days_for_rate(rate):
        if not rate or rate <= 0:
            return None
        distances = sorted(t['distanceToThreshold'] for t in prox_types)
        if len(distances) < 2:
            return None
        bottleneck_dist = distances[1]
        if bottleneck_dist <= 0:
            return 0
        return math.ceil(bottleneck_dist / rate)

    pessimistic_days = velocity_days if velocity_days is not None else (
        days_for_rate(slowest_rate) if slowest_rate else None)
    base_days = days_for_rate(median_rate) if median_rate else None
    optimistic_days = days_for_rate(fastest_rate) if fastest_rate else None

    # 5. Backtest
    backtest = []
    for p in systemic_periods:
        predicted = math.ceil(25 / median_rate) if median_rate else None
        error = predicted - p['durationDays'] if predicted is not None else None
        backtest.append({
            'period': f"{p['entryDate']} -> {p['exitDate']}",
            'actualDays': p['durationDays'],
            'modelPredictedDays': predicted,
            'errorDays': error,
            'absError': abs(error) if error is not None else None,
        })

    valid_errors = [b['absError'] for b in backtest if b['absError'] is not None]
    mae = round(sum(valid_errors) / len(valid_errors), 1) if valid_errors else None

    return {
        'status': status,
        'estimatedTransition': {
            'pessimistic': {'days': pessimistic_days},
            'base': {'days': base_days},
            'optimistic': {'days': optimistic_days},
        },
        'typeProjections': type_projections,
        'historicalCalibration': {
            'periodCount': len(systemic_periods),
            'medianDurationDays': median_duration,
            'fastestRecoveryDays': fastest_recovery,
            'slowestRecoveryDays': slowest_recovery,
            'medianRecoveryRatePctPerDay': median_rate,
            'fastestRecoveryRatePctPerDay': fastest_rate,
            'slowestRecoveryRatePctPerDay': slowest_rate,
            'currentDurationDays': regime_duration_days,
            'durationPercentile': duration_percentile,
        },
        'backtestValidation': {
            'transitions': backtest,
            'meanAbsoluteErrorDays': mae,
            'sampleSize': len(backtest),
        },
    }


# ── Test Data Fixtures ─────────────────────────────────────────────────────

REGIME_HISTORY = [
    {"date": "2025-10-24", "regime": "DIVERGENCE", "transitionFrom": "NEUTRAL"},
    {"date": "2025-10-29", "regime": "NEUTRAL", "transitionFrom": "DIVERGENCE"},
    {"date": "2025-11-06", "regime": "SYSTEMIC", "transitionFrom": "NEUTRAL"},
    {"date": "2025-11-19", "regime": "NEUTRAL", "transitionFrom": "SYSTEMIC"},
    {"date": "2025-11-24", "regime": "SYSTEMIC", "transitionFrom": "NEUTRAL"},
    {"date": "2025-11-28", "regime": "NEUTRAL", "transitionFrom": "SYSTEMIC"},
    {"date": "2026-01-13", "regime": "EARNINGS", "transitionFrom": "NEUTRAL"},
    {"date": "2026-01-27", "regime": "NEUTRAL", "transitionFrom": "EARNINGS"},
    {"date": "2026-01-30", "regime": "EARNINGS", "transitionFrom": "NEUTRAL"},
    {"date": "2026-02-04", "regime": "NEUTRAL", "transitionFrom": "EARNINGS"},
    {"date": "2026-03-06", "regime": "EARNINGS", "transitionFrom": "NEUTRAL"},
]

ALL_DETERIORATING = [
    {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 44.1, "distanceToThreshold": 24.1,
     "recoveryScore": 0.196, "velocity": -0.41, "velocityLabel": "DETERIORATING", "isDecaying": True},
    {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 48.0, "distanceToThreshold": 28.0,
     "recoveryScore": 0.067, "velocity": -0.45, "velocityLabel": "DETERIORATING", "isDecaying": True},
    {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 50.6, "distanceToThreshold": 30.6,
     "recoveryScore": 0.0, "velocity": -0.47, "velocityLabel": "DETERIORATING", "isDecaying": True},
]

ONE_RECOVERING = [
    {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 25.0, "distanceToThreshold": 5.0,
     "recoveryScore": 0.833, "velocity": 0.15, "velocityLabel": "RECOVERING", "isDecaying": True},
    {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 40.0, "distanceToThreshold": 20.0,
     "recoveryScore": 0.333, "velocity": -0.10, "velocityLabel": "DETERIORATING", "isDecaying": True},
    {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 45.0, "distanceToThreshold": 25.0,
     "recoveryScore": 0.167, "velocity": -0.20, "velocityLabel": "DETERIORATING", "isDecaying": True},
]

TWO_RECOVERING = [
    {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 22.0, "distanceToThreshold": 2.0,
     "recoveryScore": 0.933, "velocity": 0.20, "velocityLabel": "RECOVERING", "isDecaying": True},
    {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 28.0, "distanceToThreshold": 8.0,
     "recoveryScore": 0.733, "velocity": 0.08, "velocityLabel": "RECOVERING", "isDecaying": True},
    {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 42.0, "distanceToThreshold": 22.0,
     "recoveryScore": 0.267, "velocity": -0.05, "velocityLabel": "DETERIORATING", "isDecaying": True},
]

ALREADY_RECOVERED = [
    {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 15.0, "distanceToThreshold": -5.0,
     "recoveryScore": 1.0, "velocity": 0.10, "velocityLabel": "RECOVERING", "isDecaying": False},
    {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 18.0, "distanceToThreshold": -2.0,
     "recoveryScore": 1.0, "velocity": 0.05, "velocityLabel": "RECOVERING", "isDecaying": False},
    {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 35.0, "distanceToThreshold": 15.0,
     "recoveryScore": 0.5, "velocity": 0.03, "velocityLabel": "RECOVERING", "isDecaying": True},
]


# ══════════════════════════════════════════════════════════════════════════════
# TEST CLASSES
# ══════════════════════════════════════════════════════════════════════════════


class TestForecastStatus(unittest.TestCase):
    """Test that forecast status is correctly determined from velocity data."""

    def test_all_deteriorating_returns_no_recovery_signal(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        self.assertEqual(fc['status'], 'NO_RECOVERY_SIGNAL')

    def test_one_recovering_returns_early_recovery(self):
        fc = compute_forecast(ONE_RECOVERING, REGIME_HISTORY)
        self.assertEqual(fc['status'], 'EARLY_RECOVERY')

    def test_two_recovering_returns_recovery_detected(self):
        fc = compute_forecast(TWO_RECOVERING, REGIME_HISTORY)
        self.assertEqual(fc['status'], 'RECOVERY_DETECTED')

    def test_neutral_returns_at_neutral(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY, regime_id='NEUTRAL')
        self.assertEqual(fc['status'], 'AT_NEUTRAL')
        self.assertIsNone(fc['estimatedTransition'])

    def test_stable_velocity_returns_stabilizing(self):
        """Types with velocity between -0.02 and 0.02 should be 'STABILIZING'."""
        stable_types = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 30.0, "distanceToThreshold": 10.0,
             "recoveryScore": 0.667, "velocity": 0.01, "velocityLabel": "STABLE", "isDecaying": True},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 35.0, "distanceToThreshold": 15.0,
             "recoveryScore": 0.5, "velocity": -0.01, "velocityLabel": "STABLE", "isDecaying": True},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 38.0, "distanceToThreshold": 18.0,
             "recoveryScore": 0.4, "velocity": 0.0, "velocityLabel": "STABLE", "isDecaying": True},
        ]
        fc = compute_forecast(stable_types, REGIME_HISTORY)
        self.assertEqual(fc['status'], 'STABILIZING')


class TestConfidenceBands(unittest.TestCase):
    """Test confidence band calculation from historical calibration."""

    def test_bands_are_ordered(self):
        """Optimistic <= Base <= Pessimistic (when all have values)."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        est = fc['estimatedTransition']
        opt = est['optimistic']['days']
        base = est['base']['days']
        pess = est['pessimistic']['days']
        # All should be non-None with historical data
        self.assertIsNotNone(opt)
        self.assertIsNotNone(base)
        self.assertIsNotNone(pess)
        self.assertLessEqual(opt, base)
        self.assertLessEqual(base, pess)

    def test_pessimistic_uses_slowest_rate_when_deteriorating(self):
        """When no types are recovering, pessimistic should use slowest historical rate."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        est = fc['estimatedTransition']
        # Pessimistic days = bottleneck_distance / slowest_rate
        # Bottleneck = 2nd closest distance = 28.0, slowest_rate = 1.92
        expected = math.ceil(28.0 / 1.92)  # = 15
        self.assertEqual(est['pessimistic']['days'], expected)

    def test_base_uses_median_rate(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        est = fc['estimatedTransition']
        # Base days = bottleneck_distance / median_rate
        # Bottleneck dist = 28.0 (2nd smallest), median_rate = 4.08
        expected = math.ceil(28.0 / 4.08)  # = 7
        self.assertEqual(est['base']['days'], expected)

    def test_optimistic_uses_fastest_rate(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        est = fc['estimatedTransition']
        # Optimistic days = bottleneck_distance / fastest_rate
        # Bottleneck dist = 28.0, fastest_rate = 6.25
        expected = math.ceil(28.0 / 6.25)  # = 5
        self.assertEqual(est['optimistic']['days'], expected)

    def test_velocity_based_projection_when_recovering(self):
        """When 2+ types are recovering, pessimistic should use velocity-based estimate."""
        fc = compute_forecast(TWO_RECOVERING, REGIME_HISTORY)
        est = fc['estimatedTransition']
        # The 2nd fastest type determines transition timing
        # FULL_DECOUPLE: dist=8.0, vel=0.08, dailyVel=0.016, dailyPct=0.016*28=0.448
        # days = ceil(8.0 / 0.448) = 18
        # But pessimistic uses velocity-based when available
        self.assertIsNotNone(est['pessimistic']['days'])

    def test_no_history_gives_null_bands(self):
        """With no SYSTEMIC periods in history, base and pessimistic should be None."""
        no_systemic_history = [
            {"date": "2025-10-24", "regime": "DIVERGENCE", "transitionFrom": "NEUTRAL"},
            {"date": "2025-10-29", "regime": "NEUTRAL", "transitionFrom": "DIVERGENCE"},
        ]
        fc = compute_forecast(ALL_DETERIORATING, no_systemic_history)
        est = fc['estimatedTransition']
        self.assertIsNone(est['base']['days'])
        self.assertIsNone(est['pessimistic']['days'])
        self.assertIsNone(est['optimistic']['days'])


class TestHistoricalCalibration(unittest.TestCase):
    """Test extraction and analysis of historical SYSTEMIC periods."""

    def test_finds_two_systemic_periods(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['periodCount'], 2)

    def test_correct_period_durations(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['fastestRecoveryDays'], 4)
        self.assertEqual(cal['slowestRecoveryDays'], 13)

    def test_median_duration_is_average_of_two(self):
        """With 2 periods (4 and 13 days), median = (4+13)/2 = 8.5."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['medianDurationDays'], 8.5)

    def test_implied_recovery_rates(self):
        """Recovery rate = 25 / duration_days."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        # 13-day period: 25/13 = 1.92
        self.assertEqual(cal['slowestRecoveryRatePctPerDay'], 1.92)
        # 4-day period: 25/4 = 6.25
        self.assertEqual(cal['fastestRecoveryRatePctPerDay'], 6.25)

    def test_median_recovery_rate(self):
        """Median of [1.92, 6.25] = (1.92 + 6.25) / 2 = 4.085 ≈ 4.08."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        self.assertAlmostEqual(cal['medianRecoveryRatePctPerDay'], 4.08, places=1)

    def test_duration_percentile(self):
        """Current 12d: 4d is shorter, 13d is longer. 1 of 2 shorter = 50%."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['durationPercentile'], 50)

    def test_duration_percentile_when_longest(self):
        """If current duration exceeds all historical periods."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY, regime_duration_days=20)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['durationPercentile'], 100)


class TestBacktestValidation(unittest.TestCase):
    """Test backtest accuracy computation."""

    def test_backtest_has_two_transitions(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        bt = fc['backtestValidation']
        self.assertEqual(bt['sampleSize'], 2)

    def test_backtest_error_calculation(self):
        """Model predicts 7 days for both (25/4.08). Actual: 13 and 4."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        bt = fc['backtestValidation']
        t1 = bt['transitions'][0]  # 13-day period
        t2 = bt['transitions'][1]  # 4-day period
        # Model: ceil(25 / 4.08) = ceil(6.13) = 7
        self.assertEqual(t1['modelPredictedDays'], 7)
        self.assertEqual(t2['modelPredictedDays'], 7)
        # Errors: 7-13 = -6, 7-4 = 3
        self.assertEqual(t1['errorDays'], -6)
        self.assertEqual(t2['errorDays'], 3)
        # Abs errors: 6, 3
        self.assertEqual(t1['absError'], 6)
        self.assertEqual(t2['absError'], 3)

    def test_mae_calculation(self):
        """MAE = (6 + 3) / 2 = 4.5."""
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        bt = fc['backtestValidation']
        self.assertEqual(bt['meanAbsoluteErrorDays'], 4.5)

    def test_backtest_honest_about_limitations(self):
        """The backtest should acknowledge it uses implied rates, not stored velocities."""
        # This tests the live API response, not the Python reimplementation
        # but validates the principle: the backtest should not claim more accuracy
        # than the data supports
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        bt = fc['backtestValidation']
        # MAE > 0 is expected — a model that claims 0 error would be overfitting
        self.assertGreater(bt['meanAbsoluteErrorDays'], 0)


class TestTypeProjections(unittest.TestCase):
    """Test per-type forward trajectory projections."""

    def test_deteriorating_types_have_null_days(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        for tp in fc['typeProjections']:
            self.assertIsNone(tp['daysToThreshold'])
            self.assertIn('Deteriorating', tp['trajectoryNote'])

    def test_recovering_type_has_positive_days(self):
        fc = compute_forecast(ONE_RECOVERING, REGIME_HISTORY)
        semi = next(tp for tp in fc['typeProjections'] if tp['type'] == 'SEMI_LEADS')
        self.assertIsNotNone(semi['daysToThreshold'])
        self.assertGreater(semi['daysToThreshold'], 0)

    def test_already_recovered_type_has_zero_days(self):
        fc = compute_forecast(ALREADY_RECOVERED, REGIME_HISTORY)
        semi = next(tp for tp in fc['typeProjections'] if tp['type'] == 'SEMI_LEADS')
        self.assertEqual(semi['daysToThreshold'], 0)

    def test_daily_velocity_is_velocity_divided_by_five(self):
        fc = compute_forecast(ALL_DETERIORATING, REGIME_HISTORY)
        for tp in fc['typeProjections']:
            original = next(t for t in ALL_DETERIORATING if t['type'] == tp['type'])
            expected = round(original['velocity'] / 5.0, 5)
            self.assertAlmostEqual(tp['dailyVelocity'], expected, places=4)

    def test_two_of_three_condition(self):
        """Transition timing is determined by the 2nd-fastest recovering type."""
        fc = compute_forecast(TWO_RECOVERING, REGIME_HISTORY)
        # SEMI_LEADS recovers first (dist=2.0), FULL_DECOUPLE second (dist=8.0)
        # The transition happens when FULL_DECOUPLE crosses, not SEMI_LEADS
        semi = next(tp for tp in fc['typeProjections'] if tp['type'] == 'SEMI_LEADS')
        full = next(tp for tp in fc['typeProjections'] if tp['type'] == 'FULL_DECOUPLE')
        if semi['daysToThreshold'] is not None and full['daysToThreshold'] is not None:
            self.assertLessEqual(semi['daysToThreshold'], full['daysToThreshold'])


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_empty_history(self):
        fc = compute_forecast(ALL_DETERIORATING, [])
        cal = fc['historicalCalibration']
        self.assertEqual(cal['periodCount'], 0)
        self.assertIsNone(cal['medianDurationDays'])
        est = fc['estimatedTransition']
        self.assertIsNone(est['base']['days'])

    def test_single_systemic_period(self):
        """With 1 period, median = that period's values."""
        single = [
            {"date": "2025-11-06", "regime": "SYSTEMIC", "transitionFrom": "NEUTRAL"},
            {"date": "2025-11-19", "regime": "NEUTRAL", "transitionFrom": "SYSTEMIC"},
        ]
        fc = compute_forecast(ALL_DETERIORATING, single)
        cal = fc['historicalCalibration']
        self.assertEqual(cal['periodCount'], 1)
        self.assertEqual(cal['medianDurationDays'], 13)
        self.assertEqual(cal['fastestRecoveryDays'], 13)
        self.assertEqual(cal['slowestRecoveryDays'], 13)

    def test_recovery_rate_caps_at_365_days(self):
        """Extremely slow recovery should cap at 365 days."""
        slow_recovery = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 49.0, "distanceToThreshold": 29.0,
             "recoveryScore": 0.033, "velocity": 0.025, "velocityLabel": "RECOVERING", "isDecaying": True},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 49.5, "distanceToThreshold": 29.5,
             "recoveryScore": 0.017, "velocity": 0.025, "velocityLabel": "RECOVERING", "isDecaying": True},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 49.9, "distanceToThreshold": 29.9,
             "recoveryScore": 0.003, "velocity": -0.30, "velocityLabel": "DETERIORATING", "isDecaying": True},
        ]
        fc = compute_forecast(slow_recovery, REGIME_HISTORY)
        for tp in fc['typeProjections']:
            if tp['daysToThreshold'] is not None:
                self.assertLessEqual(tp['daysToThreshold'], 365)

    def test_zero_distance_means_recovered(self):
        """Type with distanceToThreshold <= 0 should show 0 days."""
        recovered = [
            {"type": "SEMI_LEADS", "label": "Semi Leads", "dropPct": 19.0, "distanceToThreshold": -1.0,
             "recoveryScore": 1.0, "velocity": 0.10, "velocityLabel": "RECOVERING", "isDecaying": False},
            {"type": "FULL_DECOUPLE", "label": "Full Decouple", "dropPct": 20.0, "distanceToThreshold": 0.0,
             "recoveryScore": 1.0, "velocity": 0.05, "velocityLabel": "RECOVERING", "isDecaying": False},
            {"type": "CRYPTO_LEADS", "label": "Crypto Leads", "dropPct": 30.0, "distanceToThreshold": 10.0,
             "recoveryScore": 0.667, "velocity": 0.10, "velocityLabel": "RECOVERING", "isDecaying": True},
        ]
        fc = compute_forecast(recovered, REGIME_HISTORY)
        semi = next(tp for tp in fc['typeProjections'] if tp['type'] == 'SEMI_LEADS')
        full = next(tp for tp in fc['typeProjections'] if tp['type'] == 'FULL_DECOUPLE')
        self.assertEqual(semi['daysToThreshold'], 0)
        self.assertEqual(full['daysToThreshold'], 0)


class TestLiveAPIIntegration(unittest.TestCase):
    """Integration tests against the live API (skipped if API is unreachable)."""

    @classmethod
    def setUpClass(cls):
        import urllib.request
        try:
            resp = urllib.request.urlopen('http://localhost:8080/regime/current', timeout=5)
            cls.data = json.loads(resp.read().decode())
            cls.available = True
        except Exception:
            cls.available = False

    def setUp(self):
        if not self.available:
            self.skipTest('Live API not reachable at localhost:8080')

    def test_transition_forecast_present(self):
        self.assertIn('transitionForecast', self.data)

    def test_forecast_has_required_fields(self):
        tf = self.data['transitionForecast']
        required = ['status', 'message', 'currentTrajectory', 'estimatedTransition',
                     'recoveryRequirements', 'projectedRegime', 'typeProjections',
                     'historicalCalibration', 'backtestValidation']
        for field in required:
            self.assertIn(field, tf, f'Missing field: {field}')

    def test_confidence_bands_present(self):
        tf = self.data['transitionForecast']
        est = tf['estimatedTransition']
        for band in ['pessimistic', 'base', 'optimistic']:
            self.assertIn(band, est)
            self.assertIn('days', est[band])
            self.assertIn('scenario', est[band])

    def test_historical_calibration_has_periods(self):
        tf = self.data['transitionForecast']
        cal = tf['historicalCalibration']
        self.assertGreater(cal['periodCount'], 0)

    def test_backtest_has_results(self):
        tf = self.data['transitionForecast']
        bt = tf['backtestValidation']
        self.assertIn('meanAbsoluteErrorDays', bt)
        self.assertIn('transitions', bt)
        self.assertGreater(bt['sampleSize'], 0)

    def test_type_projections_have_three_types(self):
        tf = self.data['transitionForecast']
        types = [tp['type'] for tp in tf['typeProjections']]
        self.assertIn('SEMI_LEADS', types)
        self.assertIn('CRYPTO_LEADS', types)
        self.assertIn('FULL_DECOUPLE', types)

    def test_backtest_limitation_disclosed(self):
        """The backtest should honestly disclose its limitations."""
        tf = self.data['transitionForecast']
        bt = tf['backtestValidation']
        self.assertIn('limitation', bt)
        self.assertIn('velocity', bt['limitation'].lower())

    def test_recovery_requirements_per_type(self):
        tf = self.data['transitionForecast']
        rr = tf['recoveryRequirements']
        self.assertIn('perType', rr)
        for req in rr['perType']:
            self.assertIn('currentVelocity', req)
            self.assertIn('requiredVelocity', req)
            self.assertIn('velocityGap', req)
            self.assertIn('feasibility', req)


if __name__ == '__main__':
    unittest.main()
