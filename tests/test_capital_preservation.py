#!/usr/bin/env python3
"""Tests for the Capital Preservation / Counterfactual PnL model."""

import unittest
import math
import json
import os

# ── Static regime returns (mirrors REGIME_RETURNS in signal_api.js) ──
REGIME_RETURNS = {
    'NEUTRAL': {
        'CRYPTO_LEADS':  {'hitRate': 0.82, 'avgRet': 8.24,  'n': 17},
        'SEMI_LEADS':    {'hitRate': 0.12, 'avgRet': -14.60, 'n': 8},
        'FULL_DECOUPLE': {'hitRate': 0.50, 'avgRet': -6.55,  'n': 6},
    },
    'SYSTEMIC': {
        'CRYPTO_LEADS':  {'hitRate': 0.20, 'avgRet': -9.80,  'n': 5},
        'SEMI_LEADS':    {'hitRate': 0.10, 'avgRet': -14.60, 'n': 10},
        'FULL_DECOUPLE': {'hitRate': 0.25, 'avgRet': -8.30,  'n': 4},
    }
}

SIG_TYPES = ['CRYPTO_LEADS', 'SEMI_LEADS', 'FULL_DECOUPLE']


def cross_regime_decomposition(sig_type):
    """Solve for win/loss returns using NEUTRAL and SYSTEMIC data."""
    N = REGIME_RETURNS['NEUTRAL'][sig_type]
    S = REGIME_RETURNS['SYSTEMIC'][sig_type]
    if N['hitRate'] == S['hitRate']:
        return None
    R_loss = (N['hitRate'] * S['avgRet'] - S['hitRate'] * N['avgRet']) / (N['hitRate'] - S['hitRate'])
    R_win = (N['avgRet'] - (1 - N['hitRate']) * R_loss) / N['hitRate']
    return {'winReturn': round(R_win, 2), 'lossReturn': round(R_loss, 2)}


def adjusted_confidence(neutral_rate, decay_constant, day):
    """Compute duration-decayed hit rate."""
    return neutral_rate * math.exp(-decay_constant * day)


def adjusted_expected_return(adj_conf, win_return, loss_return):
    """Expected return using decayed probability."""
    return adj_conf * win_return + (1 - adj_conf) * loss_return


def compute_decay_constant(neutral_rate, systemic_rate, median_duration=8.5):
    """Compute lambda for exponential decay."""
    return math.log(neutral_rate / systemic_rate) / median_duration


class TestCrossRegimeDecomposition(unittest.TestCase):
    """Test the 2-equation system that separates win/loss returns."""

    def test_crypto_leads_decomposition(self):
        d = cross_regime_decomposition('CRYPTO_LEADS')
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d['winReturn'], 13.48, places=1)
        self.assertAlmostEqual(d['lossReturn'], -15.62, places=1)

    def test_crypto_leads_verifies_neutral(self):
        """Decomposed returns should reconstruct NEUTRAL avg return."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        N = REGIME_RETURNS['NEUTRAL']['CRYPTO_LEADS']
        reconstructed = N['hitRate'] * d['winReturn'] + (1 - N['hitRate']) * d['lossReturn']
        self.assertAlmostEqual(reconstructed, N['avgRet'], places=1)

    def test_crypto_leads_verifies_systemic(self):
        """Decomposed returns should reconstruct SYSTEMIC avg return."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        S = REGIME_RETURNS['SYSTEMIC']['CRYPTO_LEADS']
        reconstructed = S['hitRate'] * d['winReturn'] + (1 - S['hitRate']) * d['lossReturn']
        self.assertAlmostEqual(reconstructed, S['avgRet'], places=1)

    def test_semi_leads_anti_signal(self):
        """SEMI_LEADS win/loss returns should be nearly identical (anti-signal)."""
        d = cross_regime_decomposition('SEMI_LEADS')
        self.assertIsNotNone(d)
        # Win and loss returns within 0.5% of each other
        self.assertAlmostEqual(d['winReturn'], d['lossReturn'], delta=0.5)
        # Both should be negative (anti-signal)
        self.assertLess(d['winReturn'], 0)
        self.assertLess(d['lossReturn'], 0)

    def test_semi_leads_verifies_both_regimes(self):
        d = cross_regime_decomposition('SEMI_LEADS')
        for regime in ['NEUTRAL', 'SYSTEMIC']:
            R = REGIME_RETURNS[regime]['SEMI_LEADS']
            reconstructed = R['hitRate'] * d['winReturn'] + (1 - R['hitRate']) * d['lossReturn']
            self.assertAlmostEqual(reconstructed, R['avgRet'], places=1)

    def test_full_decouple_decomposition(self):
        d = cross_regime_decomposition('FULL_DECOUPLE')
        self.assertIsNotNone(d)
        # Win return should be negative but less negative than loss return
        self.assertLess(d['winReturn'], 0)
        self.assertLess(d['lossReturn'], d['winReturn'])

    def test_full_decouple_verifies_both_regimes(self):
        d = cross_regime_decomposition('FULL_DECOUPLE')
        for regime in ['NEUTRAL', 'SYSTEMIC']:
            R = REGIME_RETURNS[regime]['FULL_DECOUPLE']
            reconstructed = R['hitRate'] * d['winReturn'] + (1 - R['hitRate']) * d['lossReturn']
            self.assertAlmostEqual(reconstructed, R['avgRet'], places=1)


class TestAdjustedExpectedReturn(unittest.TestCase):
    """Test expected return computation with decayed probabilities."""

    def test_crypto_leads_day0(self):
        """At day 0, adjusted expected return = NEUTRAL avg return."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        N = REGIME_RETURNS['NEUTRAL']['CRYPTO_LEADS']
        ret = adjusted_expected_return(N['hitRate'], d['winReturn'], d['lossReturn'])
        self.assertAlmostEqual(ret, N['avgRet'], places=1)

    def test_crypto_leads_day13_worse_than_static(self):
        """At day 13, adjusted expected return should be worse than static SYSTEMIC."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 13)
        ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        # Should be more negative than static -9.8%
        self.assertLess(ret, -9.8)
        self.assertAlmostEqual(ret, -12.86, places=1)

    def test_crypto_leads_monotonic_decay(self):
        """Expected return should get worse (more negative) over time."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        prev_ret = 100  # start high
        for day in range(0, 20):
            adj_conf = adjusted_confidence(0.82, lam, day)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            self.assertLessEqual(ret, prev_ret)
            prev_ret = ret

    def test_semi_leads_barely_changes(self):
        """SEMI_LEADS expected return should barely change with duration (anti-signal)."""
        d = cross_regime_decomposition('SEMI_LEADS')
        lam = compute_decay_constant(0.12, 0.10)
        ret_day0 = adjusted_expected_return(0.12, d['winReturn'], d['lossReturn'])
        adj_conf_13 = adjusted_confidence(0.12, lam, 13)
        ret_day13 = adjusted_expected_return(adj_conf_13, d['winReturn'], d['lossReturn'])
        # Difference should be < 0.1% (nearly identical win/loss returns)
        self.assertAlmostEqual(ret_day0, ret_day13, delta=0.1)

    def test_full_decouple_day13(self):
        """FULL_DECOUPLE at day 13 should be worse than static."""
        d = cross_regime_decomposition('FULL_DECOUPLE')
        lam = compute_decay_constant(0.50, 0.25)
        adj_conf = adjusted_confidence(0.50, lam, 13)
        ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        self.assertLess(ret, -8.3)  # worse than static -8.3%


class TestCounterfactualLoss(unittest.TestCase):
    """Test per-entry counterfactual loss computation."""

    def test_loss_is_positive(self):
        """counterfactualLoss should be positive (loss AVOIDED)."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 13)
        ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        counterfactual_loss = -ret  # positive = loss avoided
        self.assertGreater(counterfactual_loss, 0)

    def test_loss_increases_with_duration(self):
        """Later entries (deeper into SYSTEMIC) should have higher counterfactual loss."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        loss_day4 = -adjusted_expected_return(
            adjusted_confidence(0.82, lam, 4), d['winReturn'], d['lossReturn'])
        loss_day13 = -adjusted_expected_return(
            adjusted_confidence(0.82, lam, 13), d['winReturn'], d['lossReturn'])
        self.assertGreater(loss_day13, loss_day4)

    def test_weighted_portfolio_loss(self):
        """Portfolio-weighted loss should combine all types."""
        # Simulate 8 CL, 3 SL, 6 FD = 17 signals
        weights = {'CRYPTO_LEADS': 8/17, 'SEMI_LEADS': 3/17, 'FULL_DECOUPLE': 6/17}
        total_exp_return = 0
        for t in SIG_TYPES:
            d = cross_regime_decomposition(t)
            lam = compute_decay_constant(
                REGIME_RETURNS['NEUTRAL'][t]['hitRate'],
                REGIME_RETURNS['SYSTEMIC'][t]['hitRate'])
            adj_conf = adjusted_confidence(REGIME_RETURNS['NEUTRAL'][t]['hitRate'], lam, 13)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            total_exp_return += weights[t] * ret
        counterfactual_loss = -total_exp_return
        # Should be positive and in the 10-13% range
        self.assertGreater(counterfactual_loss, 9)
        self.assertLess(counterfactual_loss, 14)


class TestInverseSignal(unittest.TestCase):
    """Test inverse (short) signal viability analysis."""

    def test_crypto_leads_short_is_positive(self):
        """Short expected return for CRYPTO_LEADS should be positive at day 13."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 13)
        long_ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        short_ret = -long_ret
        self.assertGreater(short_ret, 10)  # > 10% expected return on short

    def test_crypto_leads_viability_at_day13(self):
        """At day 13 (adj conf ~9.5%), should be VIABLE_WITH_CAVEATS."""
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 13)
        self.assertLess(adj_conf, 0.10)
        viability = 'VIABLE_WITH_CAVEATS' if adj_conf < 0.10 else 'MARGINAL'
        self.assertEqual(viability, 'VIABLE_WITH_CAVEATS')

    def test_semi_leads_not_viable_inverse(self):
        """SEMI_LEADS should NOT be viable as inverse signal."""
        d = cross_regime_decomposition('SEMI_LEADS')
        # Both win and loss returns are negative, so short side is also ~14.6%
        # But this is meaningless — the signal has no directional information
        self.assertAlmostEqual(d['winReturn'], d['lossReturn'], delta=0.5)

    def test_inverse_at_early_systemic(self):
        """At day 2, CRYPTO_LEADS still has ~50% hit rate — short NOT viable."""
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 2)
        self.assertGreater(adj_conf, 0.20)
        viability = 'NOT_VIABLE' if adj_conf >= 0.20 else 'MARGINAL'
        self.assertEqual(viability, 'NOT_VIABLE')


class TestSensitivityBands(unittest.TestCase):
    """Test sensitivity analysis using decay model bands."""

    def test_conservative_worse_than_base(self):
        """Conservative (faster decay) should show higher counterfactual loss."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        # Conservative = 30% shorter half-life = faster decay = lower adj conf
        lam = compute_decay_constant(0.82, 0.20)
        half_life = 8.5 * math.log(2) / math.log(0.82 / 0.20)
        conservative_lambda = math.log(2) / (half_life * 0.7)
        base_lambda = lam

        adj_conf_cons = 0.82 * math.exp(-conservative_lambda * 13)
        adj_conf_base = 0.82 * math.exp(-base_lambda * 13)

        ret_cons = adjusted_expected_return(adj_conf_cons, d['winReturn'], d['lossReturn'])
        ret_base = adjusted_expected_return(adj_conf_base, d['winReturn'], d['lossReturn'])

        # Conservative (faster decay) should have worse (more negative) return
        self.assertLess(ret_cons, ret_base)

    def test_optimistic_better_than_base(self):
        """Optimistic (slower decay) should show lower counterfactual loss."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        half_life = 8.5 * math.log(2) / math.log(0.82 / 0.20)
        optimistic_lambda = math.log(2) / (half_life * 1.3)
        base_lambda = lam

        adj_conf_opt = 0.82 * math.exp(-optimistic_lambda * 13)
        adj_conf_base = 0.82 * math.exp(-base_lambda * 13)

        ret_opt = adjusted_expected_return(adj_conf_opt, d['winReturn'], d['lossReturn'])
        ret_base = adjusted_expected_return(adj_conf_base, d['winReturn'], d['lossReturn'])

        # Optimistic (slower decay) should have better (less negative) return
        self.assertGreater(ret_opt, ret_base)

    def test_semi_leads_bands_nearly_identical(self):
        """SEMI_LEADS sensitivity should show nearly zero variation."""
        d = cross_regime_decomposition('SEMI_LEADS')
        lam = compute_decay_constant(0.12, 0.10)
        half_life = 8.5 * math.log(2) / math.log(0.12 / 0.10)

        results = []
        for factor in [0.7, 1.0, 1.3]:
            adj_lambda = math.log(2) / (half_life * factor)
            adj_conf = 0.12 * math.exp(-adj_lambda * 13)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            results.append(ret)

        # All three should be within 0.1% of each other
        self.assertAlmostEqual(results[0], results[2], delta=0.1)


class TestNonIndependence(unittest.TestCase):
    """Test non-independence adjustment calculations."""

    def test_independent_windows_calculation(self):
        """13-day regime with 14-day horizon = ~0.93 independent windows."""
        independent_windows = 13 / 14
        self.assertAlmostEqual(independent_windows, 0.93, places=1)

    def test_position_adjusted_less_than_total(self):
        """Position-adjusted drawdown should be much less than raw total."""
        # With 147 entries but only 0.93 independent windows,
        # position-adjusted should be much smaller than total
        avg_loss = 11.48  # from live API
        total = avg_loss * 147
        position_adjusted = avg_loss * 0.93
        self.assertLess(position_adjusted, total / 100)

    def test_overlap_ratio(self):
        """96 entries/day × 14 day horizon = massive overlap factor."""
        entries_per_day = 96  # 15-min cycles
        horizon_days = 14
        overlap_factor = entries_per_day * horizon_days
        self.assertEqual(overlap_factor, 1344)
        # Each "trade" overlaps with ~1344 other entries


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_day_zero_returns_neutral(self):
        """At day 0, expected return should equal NEUTRAL average."""
        for t in SIG_TYPES:
            d = cross_regime_decomposition(t)
            if d is None:
                continue
            N = REGIME_RETURNS['NEUTRAL'][t]
            ret = adjusted_expected_return(N['hitRate'], d['winReturn'], d['lossReturn'])
            self.assertAlmostEqual(ret, N['avgRet'], places=1,
                                   msg=f'{t} day 0 should equal NEUTRAL avg')

    def test_very_long_duration(self):
        """At day 100, all types should have near-floor expected returns."""
        for t in SIG_TYPES:
            d = cross_regime_decomposition(t)
            if d is None:
                continue
            lam = compute_decay_constant(
                REGIME_RETURNS['NEUTRAL'][t]['hitRate'],
                REGIME_RETURNS['SYSTEMIC'][t]['hitRate'])
            adj_conf = adjusted_confidence(REGIME_RETURNS['NEUTRAL'][t]['hitRate'], lam, 100)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            # Should be very close to the pure loss return (adj_conf near 0)
            self.assertAlmostEqual(ret, d['lossReturn'], delta=0.5)

    def test_all_types_negative_return_under_systemic(self):
        """All signal types should have negative expected returns at day 13."""
        for t in SIG_TYPES:
            d = cross_regime_decomposition(t)
            if d is None:
                continue
            lam = compute_decay_constant(
                REGIME_RETURNS['NEUTRAL'][t]['hitRate'],
                REGIME_RETURNS['SYSTEMIC'][t]['hitRate'])
            adj_conf = adjusted_confidence(REGIME_RETURNS['NEUTRAL'][t]['hitRate'], lam, 13)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            self.assertLess(ret, 0, msg=f'{t} should have negative return at day 13')

    def test_counterfactual_loss_range_extended(self):
        """Per-entry loss should be positive for extended SYSTEMIC (day 5+)."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        for day in range(5, 20):
            adj_conf = adjusted_confidence(0.82, lam, day)
            ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
            loss = -ret
            self.assertGreater(loss, 0, msg=f'Day {day} loss should be positive')
            self.assertLess(loss, 20, msg=f'Day {day} loss should be < 20%')

    def test_early_systemic_still_positive_return(self):
        """At day 1, CRYPTO_LEADS still has positive expected return (residual edge)."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 1)
        ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        # At day 1, adj_conf ~69%, still positive expected return
        self.assertGreater(ret, 0, msg='Day 1 should still have positive expected return')
        # This means NO_TRADE at day 1 actually costs you — preserving capital is only
        # beneficial after the crossover point where expected return goes negative


class TestStaticVsAdjustedBias(unittest.TestCase):
    """Test that adjusted returns correctly show the bias in static rates."""

    def test_crypto_leads_bias_direction(self):
        """Static SYSTEMIC return overstates how bad it is (less negative than adjusted)."""
        d = cross_regime_decomposition('CRYPTO_LEADS')
        lam = compute_decay_constant(0.82, 0.20)
        adj_conf = adjusted_confidence(0.82, lam, 13)
        static_ret = REGIME_RETURNS['SYSTEMIC']['CRYPTO_LEADS']['avgRet']
        adj_ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        # Adjusted should be MORE negative than static (static UNDERstates the loss)
        self.assertLess(adj_ret, static_ret)

    def test_full_decouple_bias_direction(self):
        """FULL_DECOUPLE adjusted return also more negative than static at day 13."""
        d = cross_regime_decomposition('FULL_DECOUPLE')
        lam = compute_decay_constant(0.50, 0.25)
        adj_conf = adjusted_confidence(0.50, lam, 13)
        static_ret = REGIME_RETURNS['SYSTEMIC']['FULL_DECOUPLE']['avgRet']
        adj_ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        self.assertLess(adj_ret, static_ret)

    def test_semi_leads_bias_negligible(self):
        """SEMI_LEADS bias should be negligible (anti-signal)."""
        d = cross_regime_decomposition('SEMI_LEADS')
        lam = compute_decay_constant(0.12, 0.10)
        adj_conf = adjusted_confidence(0.12, lam, 13)
        static_ret = REGIME_RETURNS['SYSTEMIC']['SEMI_LEADS']['avgRet']
        adj_ret = adjusted_expected_return(adj_conf, d['winReturn'], d['lossReturn'])
        self.assertAlmostEqual(adj_ret, static_ret, delta=0.1)


if __name__ == '__main__':
    unittest.main()
