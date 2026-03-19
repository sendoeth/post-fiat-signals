#!/usr/bin/env python3
"""Tests for the Optimal Re-Entry Timing Model.

Mirrors the computeOptimalReEntry() function in signal_api.js.
The model synthesizes 4 upstream modules (regime proximity, transition forecast,
hit rate decay, capital preservation decomposition) into a prescriptive
expected-value surface that tells downstream consumers when entry turns positive.

Core equation:
  E[R|d] = P(NEUTRAL|d) * E[R|NEUTRAL] + P(SYSTEMIC|d) * [h_adj(d) * R_win + (1-h_adj(d)) * R_loss]

Where:
  P(NEUTRAL|d) = uniform CDF between optimistic and pessimistic transition bounds
  h_adj(d)     = neutralRate * exp(-lambda * (curDur + d))
  R_win, R_loss = cross-regime decomposition per signal type
"""

import unittest
import math

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
HORIZON_DAYS = 14
RISK_FREE_APY = 0.04
FORECAST_DAYS = 30


# ── Helper functions (mirror JS implementation) ────────────────────────────────

def risk_free_14d():
    """14-day yield from 4% APY."""
    return (math.pow(1 + RISK_FREE_APY, HORIZON_DAYS / 365) - 1) * 100


def cross_regime_decomposition(sig_type):
    """Solve for win/loss returns using NEUTRAL and SYSTEMIC data."""
    N = REGIME_RETURNS['NEUTRAL'][sig_type]
    S = REGIME_RETURNS['SYSTEMIC'][sig_type]
    if N['hitRate'] == S['hitRate']:
        return None
    R_loss = (N['hitRate'] * S['avgRet'] - S['hitRate'] * N['avgRet']) / (N['hitRate'] - S['hitRate'])
    R_win = (N['avgRet'] - (1 - N['hitRate']) * R_loss) / N['hitRate']
    return {'Rw': R_win, 'Rl': R_loss}


def compute_decay_constant(neutral_rate, systemic_rate, median_duration=8.5):
    """Lambda for exponential decay model."""
    return math.log(neutral_rate / systemic_rate) / median_duration


def p_neutral(d, opt_days, pess_days):
    """Uniform CDF survival function: P(NEUTRAL by forward day d)."""
    if opt_days is None or pess_days is None:
        return 0
    if opt_days >= pess_days:
        return 1.0 if d >= opt_days else 0.0
    if d <= opt_days:
        return 0.0
    if d >= pess_days:
        return 1.0
    return (d - opt_days) / (pess_days - opt_days)


def expected_return(d, sig_type, cur_dur, opt_days, pess_days, hl_mult=1.0):
    """Blended E[R] at forward day d for one signal type."""
    decomp = cross_regime_decomposition(sig_type)
    if decomp is None:
        return None
    N = REGIME_RETURNS['NEUTRAL'][sig_type]
    S = REGIME_RETURNS['SYSTEMIC'][sig_type]
    lam = compute_decay_constant(N['hitRate'], S['hitRate'])
    hl = (math.log(2) / lam) * hl_mult
    lam_adj = math.log(2) / hl

    fd = cur_dur + d  # total regime day
    h_adj = max(0, N['hitRate'] * math.exp(-lam_adj * fd))
    pN = p_neutral(d, opt_days, pess_days)
    pS = 1 - pN

    ev_neutral = N['hitRate'] * decomp['Rw'] + (1 - N['hitRate']) * decomp['Rl']
    ev_systemic = h_adj * decomp['Rw'] + (1 - h_adj) * decomp['Rl']
    return pN * ev_neutral + pS * ev_systemic


def kelly_fraction(d, sig_type, cur_dur, opt_days, pess_days, hl_mult=1.0):
    """Kelly fraction at forward day d for one signal type."""
    decomp = cross_regime_decomposition(sig_type)
    if decomp is None:
        return 0
    N = REGIME_RETURNS['NEUTRAL'][sig_type]
    S = REGIME_RETURNS['SYSTEMIC'][sig_type]
    lam = compute_decay_constant(N['hitRate'], S['hitRate'])
    hl = (math.log(2) / lam) * hl_mult
    lam_adj = math.log(2) / hl

    fd = cur_dur + d
    h_adj = max(0, N['hitRate'] * math.exp(-lam_adj * fd))
    pN = p_neutral(d, opt_days, pess_days)
    pS = 1 - pN

    eff_hit = pN * N['hitRate'] + pS * h_adj
    Rw, Rl = abs(decomp['Rw']), abs(decomp['Rl'])
    b = Rw / Rl if Rl > 0 else 0
    raw_k = (eff_hit * b - (1 - eff_hit)) / b if b > 0 else 0
    overlap = max(1, fd / HORIZON_DAYS)
    serial_adj = min(1.0, 1.0 / math.sqrt(overlap))
    return max(0, min(1, raw_k * serial_adj))


def find_crossover(sig_type, cur_dur, opt_days, pess_days, hl_mult=1.0):
    """Find first day where EV exceeds risk-free rate for a type."""
    rf = risk_free_14d()
    for d in range(0, FORECAST_DAYS + 1):
        ev = expected_return(d, sig_type, cur_dur, opt_days, pess_days, hl_mult)
        if ev is not None and ev > rf:
            return d
    return None


# ── Test Classes ───────────────────────────────────────────────────────────────

class TestRiskFreeRate(unittest.TestCase):
    """Test risk-free rate calculation."""

    def test_risk_free_14d_value(self):
        """14-day yield from 4% APY should be ~0.150%."""
        rf = risk_free_14d()
        self.assertGreater(rf, 0.14)
        self.assertLess(rf, 0.16)

    def test_risk_free_is_small(self):
        """Risk-free rate should be trivially small vs signal returns."""
        rf = risk_free_14d()
        self.assertLess(rf, 1.0)  # < 1%


class TestSurvivalFunction(unittest.TestCase):
    """Test uniform CDF for P(NEUTRAL|d)."""

    def test_before_optimistic(self):
        """P(NEUTRAL) = 0 before optimistic bound."""
        self.assertEqual(p_neutral(3, 5, 15), 0.0)

    def test_at_optimistic(self):
        """P(NEUTRAL) = 0 at exactly optimistic bound."""
        self.assertEqual(p_neutral(5, 5, 15), 0.0)

    def test_midpoint(self):
        """P(NEUTRAL) = 0.5 at midpoint."""
        self.assertAlmostEqual(p_neutral(10, 5, 15), 0.5, places=4)

    def test_at_pessimistic(self):
        """P(NEUTRAL) = 1.0 at pessimistic bound."""
        self.assertEqual(p_neutral(15, 5, 15), 1.0)

    def test_after_pessimistic(self):
        """P(NEUTRAL) = 1.0 after pessimistic bound."""
        self.assertEqual(p_neutral(20, 5, 15), 1.0)

    def test_none_bounds(self):
        """P(NEUTRAL) = 0 when bounds are None."""
        self.assertEqual(p_neutral(10, None, None), 0)
        self.assertEqual(p_neutral(10, 5, None), 0)
        self.assertEqual(p_neutral(10, None, 15), 0)

    def test_equal_bounds(self):
        """Step function when opt == pess."""
        self.assertEqual(p_neutral(4, 5, 5), 0.0)
        self.assertEqual(p_neutral(5, 5, 5), 1.0)

    def test_monotonic_increasing(self):
        """P(NEUTRAL) should be monotonically non-decreasing."""
        prev = 0
        for d in range(0, 25):
            pn = p_neutral(d, 5, 15)
            self.assertGreaterEqual(pn, prev)
            prev = pn


class TestExpectedReturn(unittest.TestCase):
    """Test expected return computation for individual signal types."""

    def test_crypto_leads_day0_deep_systemic(self):
        """At day 0, deep SYSTEMIC (dur=13), CRYPTO_LEADS should be negative."""
        ev = expected_return(0, 'CRYPTO_LEADS', 13, 5, 15)
        self.assertLess(ev, 0)

    def test_crypto_leads_positive_at_neutral(self):
        """If P(NEUTRAL)=1, CRYPTO_LEADS should have positive expected return."""
        # Forward day 30 with opt=5, pess=15 → P(NEUTRAL)=1.0
        ev = expected_return(30, 'CRYPTO_LEADS', 13, 5, 15)
        self.assertGreater(ev, 0)
        # Should be close to NEUTRAL avg return of 8.24%
        self.assertAlmostEqual(ev, 8.24, delta=0.1)

    def test_semi_leads_never_positive(self):
        """SEMI_LEADS expected return should be negative regardless of day."""
        for d in range(0, 31):
            ev = expected_return(d, 'SEMI_LEADS', 13, 5, 15)
            self.assertLess(ev, 0,
                            msg=f'SEMI_LEADS should be negative at day {d}')

    def test_full_decouple_negative_even_neutral(self):
        """FULL_DECOUPLE has negative EV even under NEUTRAL (avgRet=-6.55%)."""
        ev = expected_return(30, 'FULL_DECOUPLE', 13, 5, 15)
        # E[R|NEUTRAL] for FULL_DECOUPLE = 0.50*Rw + 0.50*Rl
        # = 0.50*(-3.05) + 0.50*(-10.05) = -6.55
        self.assertLess(ev, 0)

    def test_ev_improves_after_transition_starts(self):
        """EV should improve once P(NEUTRAL) starts increasing (past opt bound).
        Before opt bound, EV may dip because decay continues while P(NEUTRAL)=0."""
        # After optimistic bound (day 5), transition probability ramps up
        prev = -999
        for d in range(5, 31):
            ev = expected_return(d, 'CRYPTO_LEADS', 13, 5, 15)
            self.assertGreaterEqual(ev, prev - 0.01,
                                    msg=f'EV should improve at day {d}')
            prev = ev

    def test_ev_dips_before_transition(self):
        """Before optimistic bound, EV dips as decay continues with no transition hope."""
        ev_d0 = expected_return(0, 'CRYPTO_LEADS', 13, 5, 15)
        ev_d4 = expected_return(4, 'CRYPTO_LEADS', 13, 5, 15)
        # Day 4 should be worse than day 0 (more decay, P(NEUTRAL) still 0)
        self.assertLess(ev_d4, ev_d0)

    def test_no_transition_bounds_all_systemic(self):
        """Without transition bounds, EV = pure SYSTEMIC (decaying) forever."""
        ev_d0 = expected_return(0, 'CRYPTO_LEADS', 13, None, None)
        ev_d30 = expected_return(30, 'CRYPTO_LEADS', 13, None, None)
        # Both should be negative (pure systemic)
        self.assertLess(ev_d0, 0)
        self.assertLess(ev_d30, 0)
        # Day 30 should be even worse (deeper decay, no transition hope)
        self.assertLess(ev_d30, ev_d0)


class TestCrossoverDay(unittest.TestCase):
    """Test re-entry crossover day detection."""

    def test_crypto_leads_crosses_first(self):
        """CRYPTO_LEADS should cross before other types."""
        x_cl = find_crossover('CRYPTO_LEADS', 13, 5, 15)
        x_sl = find_crossover('SEMI_LEADS', 13, 5, 15)
        x_fd = find_crossover('FULL_DECOUPLE', 13, 5, 15)
        self.assertIsNotNone(x_cl)
        # SEMI_LEADS and FULL_DECOUPLE should NOT cross
        self.assertIsNone(x_sl)
        self.assertIsNone(x_fd)

    def test_crossover_is_within_forecast_window(self):
        """CRYPTO_LEADS crossover should be between day 5 and day 20."""
        x = find_crossover('CRYPTO_LEADS', 13, 5, 15)
        self.assertIsNotNone(x)
        self.assertGreater(x, 5)
        self.assertLess(x, 20)

    def test_crossover_after_optimistic_bound(self):
        """Crossover cant happen before optimistic transition starts."""
        x = find_crossover('CRYPTO_LEADS', 13, 5, 15)
        self.assertIsNotNone(x)
        # Can only cross after P(NEUTRAL) starts increasing
        self.assertGreaterEqual(x, 5)

    def test_no_crossover_without_transition(self):
        """Without transition bounds, CRYPTO_LEADS should never cross."""
        x = find_crossover('CRYPTO_LEADS', 13, None, None)
        self.assertIsNone(x)

    def test_crossover_earlier_with_faster_transition(self):
        """Faster transition bounds should yield earlier crossover."""
        x_fast = find_crossover('CRYPTO_LEADS', 13, 3, 10)
        x_slow = find_crossover('CRYPTO_LEADS', 13, 10, 25)
        if x_fast is not None and x_slow is not None:
            self.assertLessEqual(x_fast, x_slow)

    def test_crossover_day0_if_shallow_systemic(self):
        """If regime just started (day 1), CRYPTO_LEADS may cross immediately."""
        # At day 1, hit rate barely decayed, transition hope exists
        x = find_crossover('CRYPTO_LEADS', 1, 2, 8)
        # With very short regime and close transition, crossover could be day 0
        if x is not None:
            self.assertLessEqual(x, 5)

    def test_no_crossover_semi_leads_any_scenario(self):
        """SEMI_LEADS should never cross in any scenario."""
        for opt, pess in [(2, 5), (5, 15), (3, 10)]:
            x = find_crossover('SEMI_LEADS', 5, opt, pess)
            self.assertIsNone(x, msg=f'SEMI_LEADS crossed with opt={opt}, pess={pess}')


class TestKellyFraction(unittest.TestCase):
    """Test Kelly criterion with serial correlation adjustment."""

    def test_kelly_bounded_0_1(self):
        """Kelly fraction should always be in [0, 1]."""
        for d in range(0, 31):
            k = kelly_fraction(d, 'CRYPTO_LEADS', 13, 5, 15)
            self.assertGreaterEqual(k, 0)
            self.assertLessEqual(k, 1)

    def test_kelly_zero_for_semi_leads(self):
        """SEMI_LEADS should have zero kelly fraction (anti-signal)."""
        for d in range(0, 31):
            k = kelly_fraction(d, 'SEMI_LEADS', 13, 5, 15)
            self.assertEqual(k, 0, msg=f'SEMI_LEADS kelly should be 0 at day {d}')

    def test_kelly_increases_with_transition(self):
        """Kelly should increase as P(NEUTRAL) increases."""
        k_early = kelly_fraction(3, 'CRYPTO_LEADS', 13, 5, 15)
        k_late = kelly_fraction(20, 'CRYPTO_LEADS', 13, 5, 15)
        self.assertGreater(k_late, k_early)

    def test_serial_correlation_reduces_kelly(self):
        """Kelly with serial adj should be <= raw kelly at positive EV."""
        # At day 30 (pure NEUTRAL), kelly should be positive but reduced
        k = kelly_fraction(30, 'CRYPTO_LEADS', 13, 5, 15)
        # At regime day 43, overlap factor = 43/14 = 3.07
        # serial adj = 1/sqrt(3.07) = 0.571
        self.assertGreater(k, 0)
        self.assertLess(k, 1)

    def test_kelly_zero_without_transition(self):
        """Without transition, deep systemic kelly should be zero."""
        k = kelly_fraction(15, 'CRYPTO_LEADS', 13, None, None)
        self.assertEqual(k, 0)


class TestSensitivityBands(unittest.TestCase):
    """Test that sensitivity band scenarios produce ordered results."""

    def test_optimistic_crosses_earliest(self):
        """Optimistic scenario (fast transition + slow decay) should cross earliest."""
        # Optimistic: transition bounds * 0.7, hl_mult = 1.3
        x_opt = find_crossover('CRYPTO_LEADS', 13, round(5 * 0.7), round(15 * 0.7), 1.3)
        # Base: calibrated
        x_base = find_crossover('CRYPTO_LEADS', 13, 5, 15, 1.0)
        # Pessimistic: transition bounds * 1.5, hl_mult = 0.7
        x_pess = find_crossover('CRYPTO_LEADS', 13, round(5 * 1.5), round(15 * 1.5), 0.7)

        if x_opt is not None and x_base is not None:
            self.assertLessEqual(x_opt, x_base)
        if x_base is not None and x_pess is not None:
            self.assertLessEqual(x_base, x_pess)

    def test_pessimistic_may_not_cross(self):
        """Pessimistic scenario with very slow transition may never cross."""
        x_pess = find_crossover('CRYPTO_LEADS', 13, round(5 * 1.5), round(15 * 1.5), 0.7)
        # This might be None — that's valid for a deeply pessimistic scenario
        # Just verify it's either None or > base
        x_base = find_crossover('CRYPTO_LEADS', 13, 5, 15, 1.0)
        if x_pess is not None and x_base is not None:
            self.assertGreaterEqual(x_pess, x_base)

    def test_slower_decay_improves_ev(self):
        """Slower decay (hl_mult=1.3) should give better EV at same day."""
        ev_fast = expected_return(10, 'CRYPTO_LEADS', 13, 5, 15, 0.7)
        ev_base = expected_return(10, 'CRYPTO_LEADS', 13, 5, 15, 1.0)
        ev_slow = expected_return(10, 'CRYPTO_LEADS', 13, 5, 15, 1.3)
        self.assertGreater(ev_slow, ev_base)
        self.assertGreater(ev_base, ev_fast)


class TestNeutralPassthrough(unittest.TestCase):
    """Test behavior when regime is already NEUTRAL."""

    def test_ev_equals_neutral_avg(self):
        """At P(NEUTRAL)=1 forever, EV should equal NEUTRAL avg return."""
        # Simulate NEUTRAL: cur_dur=0, opt=0, pess=0 → P(NEUTRAL)=1 for d>=0
        ev = expected_return(0, 'CRYPTO_LEADS', 0, 0, 0)
        N = REGIME_RETURNS['NEUTRAL']['CRYPTO_LEADS']
        decomp = cross_regime_decomposition('CRYPTO_LEADS')
        ev_n = N['hitRate'] * decomp['Rw'] + (1 - N['hitRate']) * decomp['Rl']
        self.assertAlmostEqual(ev, ev_n, places=2)

    def test_crossover_day0_at_neutral(self):
        """CRYPTO_LEADS should cross at day 0 under NEUTRAL."""
        x = find_crossover('CRYPTO_LEADS', 0, 0, 0)
        self.assertEqual(x, 0)

    def test_semi_leads_no_crossover_even_neutral(self):
        """SEMI_LEADS stays negative even under NEUTRAL."""
        x = find_crossover('SEMI_LEADS', 0, 0, 0)
        self.assertIsNone(x)


class TestDeepSystemic(unittest.TestCase):
    """Test behavior deep into SYSTEMIC (day 30+)."""

    def test_ev_floor_near_loss_return(self):
        """At very deep SYSTEMIC (no transition), EV approaches pure loss."""
        # At regime day 50, adjusted hit rate should be ~0
        ev = expected_return(0, 'CRYPTO_LEADS', 50, None, None)
        decomp = cross_regime_decomposition('CRYPTO_LEADS')
        # Should be close to R_loss (hit rate ~0)
        self.assertAlmostEqual(ev, decomp['Rl'], delta=0.5)

    def test_deep_systemic_still_negative(self):
        """All types negative at regime day 30 without transition."""
        for t in SIG_TYPES:
            ev = expected_return(0, t, 30, None, None)
            self.assertLess(ev, 0, msg=f'{t} should be negative at day 30')


class TestModelDependencyChain(unittest.TestCase):
    """Test error propagation from upstream modules."""

    def test_decay_overstatement_shifts_earlier(self):
        """If decay half-life overstated (hl_mult=1.3), crossover is earlier."""
        x_base = find_crossover('CRYPTO_LEADS', 13, 5, 15, 1.0)
        x_slow = find_crossover('CRYPTO_LEADS', 13, 5, 15, 1.3)
        if x_base is not None and x_slow is not None:
            self.assertLessEqual(x_slow, x_base)

    def test_decay_understatement_shifts_later(self):
        """If decay half-life understated (hl_mult=0.7), crossover is later."""
        x_base = find_crossover('CRYPTO_LEADS', 13, 5, 15, 1.0)
        x_fast = find_crossover('CRYPTO_LEADS', 13, 5, 15, 0.7)
        if x_base is not None and x_fast is not None:
            self.assertGreaterEqual(x_fast, x_base)

    def test_transition_bounds_wider_delays_crossover(self):
        """Wider transition bounds (more uncertain) generally delay crossover."""
        x_narrow = find_crossover('CRYPTO_LEADS', 13, 5, 10)
        x_wide = find_crossover('CRYPTO_LEADS', 13, 5, 25)
        if x_narrow is not None and x_wide is not None:
            self.assertLessEqual(x_narrow, x_wide)


class TestEdgeCases(unittest.TestCase):
    """Test boundary conditions and edge cases."""

    def test_zero_duration_regime(self):
        """Regime just started (day 0): CRYPTO_LEADS still has full edge."""
        ev = expected_return(0, 'CRYPTO_LEADS', 0, 5, 15)
        # At regime day 0, h_adj = neutralRate * exp(0) = neutralRate
        # P(NEUTRAL) = 0 at day 0 with opt=5
        # So EV = pure SYSTEMIC day 0 = N.hitRate * Rw + (1-N.hitRate) * Rl
        # which equals NEUTRAL avg return
        decomp = cross_regime_decomposition('CRYPTO_LEADS')
        N = REGIME_RETURNS['NEUTRAL']['CRYPTO_LEADS']
        ev_expected = N['hitRate'] * decomp['Rw'] + (1 - N['hitRate']) * decomp['Rl']
        self.assertAlmostEqual(ev, ev_expected, places=1)

    def test_very_long_regime_duration(self):
        """At regime day 100, decay drives all hit rates to ~0."""
        ev = expected_return(0, 'CRYPTO_LEADS', 100, None, None)
        decomp = cross_regime_decomposition('CRYPTO_LEADS')
        # h_adj ≈ 0, so EV ≈ R_loss
        self.assertAlmostEqual(ev, decomp['Rl'], delta=0.01)

    def test_equal_transition_bounds(self):
        """When opt == pess, P(NEUTRAL) is a step function."""
        pn_before = p_neutral(4, 5, 5)
        pn_at = p_neutral(5, 5, 5)
        self.assertEqual(pn_before, 0.0)
        self.assertEqual(pn_at, 1.0)

    def test_decomposition_reconstruction(self):
        """Decomposed Rw/Rl should reconstruct both regime avg returns."""
        for t in SIG_TYPES:
            decomp = cross_regime_decomposition(t)
            self.assertIsNotNone(decomp, msg=f'{t} decomposition failed')
            for regime in ['NEUTRAL', 'SYSTEMIC']:
                R = REGIME_RETURNS[regime][t]
                reconstructed = R['hitRate'] * decomp['Rw'] + (1 - R['hitRate']) * decomp['Rl']
                self.assertAlmostEqual(reconstructed, R['avgRet'], places=1,
                                       msg=f'{t}/{regime} reconstruction failed')

    def test_ev_converges_to_neutral_at_day30(self):
        """By day 30 with transition bounds 5-15, EV should be near NEUTRAL."""
        ev = expected_return(30, 'CRYPTO_LEADS', 13, 5, 15)
        # P(NEUTRAL) = 1.0 at day 30 (well past pessimistic)
        N = REGIME_RETURNS['NEUTRAL']['CRYPTO_LEADS']
        self.assertAlmostEqual(ev, N['avgRet'], delta=0.1)


class TestAggregateWeightedEV(unittest.TestCase):
    """Test portfolio-weighted EV across all signal types."""

    def test_weighted_ev_is_negative_early(self):
        """Weighted EV should be negative in early SYSTEMIC."""
        # Simulate 8 CL + 3 SL + 6 FD = 17 signals (typical portfolio)
        weights = {'CRYPTO_LEADS': 8/17, 'SEMI_LEADS': 3/17, 'FULL_DECOUPLE': 6/17}
        wev = 0
        for t in SIG_TYPES:
            ev = expected_return(0, t, 13, 5, 15)
            wev += weights[t] * ev
        self.assertLess(wev, 0)

    def test_weighted_ev_improves_after_transition_starts(self):
        """Weighted EV should improve once P(NEUTRAL) ramps up (past opt bound).
        Before opt bound, EV dips because decay continues while P(NEUTRAL)=0."""
        weights = {'CRYPTO_LEADS': 8/17, 'SEMI_LEADS': 3/17, 'FULL_DECOUPLE': 6/17}
        prev = -999
        for d in range(5, 31):
            wev = 0
            for t in SIG_TYPES:
                ev = expected_return(d, t, 13, 5, 15)
                wev += weights[t] * ev
            self.assertGreaterEqual(wev, prev - 0.01,
                                    msg=f'Weighted EV should improve at day {d}')
            prev = wev

    def test_weighted_ev_dragged_by_non_tradeable(self):
        """SEMI_LEADS + FULL_DECOUPLE drag weighted EV below CRYPTO_LEADS alone."""
        weights = {'CRYPTO_LEADS': 8/17, 'SEMI_LEADS': 3/17, 'FULL_DECOUPLE': 6/17}
        d = 15  # past transition for CL
        wev = 0
        for t in SIG_TYPES:
            ev = expected_return(d, t, 13, 5, 15)
            wev += weights[t] * ev
        ev_cl = expected_return(d, 'CRYPTO_LEADS', 13, 5, 15)
        # CRYPTO_LEADS alone should be higher than weighted average
        self.assertGreater(ev_cl, wev)

    def test_aggregate_may_not_cross(self):
        """Weighted aggregate may never cross risk-free if non-tradeable types dominate."""
        weights = {'CRYPTO_LEADS': 2/10, 'SEMI_LEADS': 4/10, 'FULL_DECOUPLE': 4/10}
        rf = risk_free_14d()
        crossed = False
        for d in range(0, 31):
            wev = 0
            for t in SIG_TYPES:
                ev = expected_return(d, t, 13, 5, 15)
                wev += weights[t] * ev
            if wev > rf:
                crossed = True
                break
        # With heavy non-tradeable weighting, aggregate likely wont cross
        # (this validates the live observation where aggregate crossover = None)
        self.assertFalse(crossed)


class TestCryptoLeadsDominance(unittest.TestCase):
    """Test that CRYPTO_LEADS is the only reliably tradeable type."""

    def test_only_crypto_leads_has_positive_neutral_ev(self):
        """Only CRYPTO_LEADS should have positive E[R|NEUTRAL]."""
        for t in SIG_TYPES:
            decomp = cross_regime_decomposition(t)
            N = REGIME_RETURNS['NEUTRAL'][t]
            ev = N['hitRate'] * decomp['Rw'] + (1 - N['hitRate']) * decomp['Rl']
            if t == 'CRYPTO_LEADS':
                self.assertGreater(ev, 0, msg=f'{t} should have positive NEUTRAL EV')
            else:
                self.assertLess(ev, 0, msg=f'{t} should have negative NEUTRAL EV')

    def test_semi_leads_anti_signal_confirmed(self):
        """SEMI_LEADS Rw ≈ Rl confirms it carries no directional information."""
        decomp = cross_regime_decomposition('SEMI_LEADS')
        self.assertAlmostEqual(decomp['Rw'], decomp['Rl'], delta=0.5)

    def test_full_decouple_both_returns_negative(self):
        """FULL_DECOUPLE has negative win AND loss returns."""
        decomp = cross_regime_decomposition('FULL_DECOUPLE')
        self.assertLess(decomp['Rw'], 0)
        self.assertLess(decomp['Rl'], 0)


class TestNumericalStability(unittest.TestCase):
    """Test numerical edge cases for stability."""

    def test_extreme_regime_duration(self):
        """Model should not produce NaN or Inf at extreme durations."""
        for dur in [0, 1, 50, 100, 200]:
            ev = expected_return(0, 'CRYPTO_LEADS', dur, 5, 15)
            self.assertFalse(math.isnan(ev), msg=f'NaN at dur={dur}')
            self.assertFalse(math.isinf(ev), msg=f'Inf at dur={dur}')

    def test_kelly_no_nan(self):
        """Kelly should not produce NaN."""
        for d in range(0, 31):
            for t in SIG_TYPES:
                k = kelly_fraction(d, t, 13, 5, 15)
                self.assertFalse(math.isnan(k), msg=f'NaN kelly for {t} at d={d}')

    def test_p_neutral_always_bounded(self):
        """P(NEUTRAL) should always be in [0, 1]."""
        for d in range(-5, 40):
            pn = p_neutral(d, 5, 15)
            self.assertGreaterEqual(pn, 0.0, msg=f'P(NEUTRAL) < 0 at d={d}')
            self.assertLessEqual(pn, 1.0, msg=f'P(NEUTRAL) > 1 at d={d}')


if __name__ == '__main__':
    unittest.main()
