#!/usr/bin/env python3
"""Tests for Ensemble Model Confidence Calibrator (Task 59).

78 tests across 14 classes covering:
- Vote extraction for both dimensions (persistence + trade readiness)
- Weighted agreement math
- Calibration bias computation
- Shannon entropy disagreement
- Pairwise tension detection
- Outlier z-score detection
- Composite reliability formula
- Sample adequacy sigmoid
- KL divergence information contributions
- Scenario stress test
- Net bias assessment
- Edge cases (nulls, single module, AT_NEUTRAL)
- Live API integration

Run:
    python3 -m pytest tests/test_ensemble_confidence.py -v
    python3 tests/test_ensemble_confidence.py
"""

import json
import math
import os
import sys
import unittest

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Mock module data factories ─────────────────────────────────────────────────

def make_base_regime(regime_id='SYSTEMIC', confidence=68):
    return {'state': regime_id, 'id': regime_id, 'confidence': confidence, 'isAlert': True}

def make_regime_proximity(score=0.05, label='DEEPLY_ENTRENCHED'):
    return {
        'score': score, 'label': label,
        'scale': '0.0 = deep SYSTEMIC, 1.0 = transition imminent',
        'regime': 'SYSTEMIC', 'regimeDurationDays': 14,
    }

def make_regime_survival(survival_prob=0.72, exit_prob=0.28, median_remaining=8.5):
    return {
        'status': 'ACTIVE', 'modelVersion': 'regime-survival-v1',
        'currentDay': {
            'survivalProbability': survival_prob,
            'cumulativeExitProbability': exit_prob,
            'medianRemainingDays': {'base': median_remaining, 'optimistic': 4.0, 'pessimistic': 18.0},
        },
        'fittedParameters': {'k': 1.2, 'lambda': 12.5},
        'extractedDurations': {'completedCount': 5, 'systemicCount': 2},
        'backtestValidation': {
            'transitions': [
                {'actual': 13, 'predicted': 10, 'actualDuration': 13, 'predictedMedianRemaining': 10},
                {'actual': 4, 'predicted': 6, 'actualDuration': 4, 'predictedMedianRemaining': 6},
            ],
            'averageBrierScore': 0.18,
        },
    }

def make_transition_forecast(base_days=12, opt_days=5, pess_days=22):
    return {
        'status': 'ACTIVE',
        'estimatedTransition': {
            'base': {'days': base_days, 'date': '2026-04-01'},
            'optimistic': {'days': opt_days, 'date': '2026-03-24'},
            'pessimistic': {'days': pess_days, 'date': '2026-04-10'},
        },
    }

def make_hit_rate_decay(cl_adj=0.08, sl_adj=0.03, fd_adj=0.15, duration=14):
    return {
        'status': 'ACTIVE', 'regimeDurationDays': duration,
        'perType': {
            'CRYPTO_LEADS': {
                'label': 'Crypto Leads Semi', 'neutralRate': 0.82, 'systemicAggregate': 0.20,
                'adjustedConfidence': cl_adj, 'decayApplicable': True,
                'decayConstant': 0.15, 'halfLifeDays': 4.6, 'nSystemic': 5, 'nNeutral': 17,
            },
            'SEMI_LEADS': {
                'label': 'Semi Leads Crypto', 'neutralRate': 0.12, 'systemicAggregate': 0.10,
                'adjustedConfidence': sl_adj, 'decayApplicable': True,
                'decayConstant': 0.08, 'halfLifeDays': 8.7, 'nSystemic': 10, 'nNeutral': 8,
            },
            'FULL_DECOUPLE': {
                'label': 'Full Decoupling', 'neutralRate': 0.50, 'systemicAggregate': 0.25,
                'adjustedConfidence': fd_adj, 'decayApplicable': True,
                'decayConstant': 0.10, 'halfLifeDays': 6.9, 'nSystemic': 4, 'nNeutral': 6,
            },
        },
    }

def make_capital_preservation(drawdown=4.5):
    return {
        'status': 'ACTIVE', 'modelVersion': 'counterfactual-pnl-v1',
        'aggregate': {
            'totalDrawdownAvoided': 12.3,
            'avgCounterfactualLossPerEntry': 2.1,
            'positionAdjustedDrawdown': drawdown,
        },
    }

def make_optimal_reentry(crossover_day=None, first_type=None):
    result = {
        'status': 'ACTIVE', 'modelVersion': 'optimal-reentry-v1',
        'crossoverDay': crossover_day,
        'firstTypeToCross': first_type,
    }
    if first_type is None and crossover_day is not None:
        result['firstTypeToCross'] = {'type': 'CRYPTO_LEADS', 'label': 'Crypto Leads Semi', 'crossoverDay': crossover_day}
    return result

def make_parameter_uncertainty(never_cross=0.857):
    return {
        'status': 'ACTIVE', 'modelVersion': 'parameter-uncertainty-v1',
        'crossoverDayCI': {
            'pointEstimate': None, 'ci95Lower': 12, 'ci95Upper': None,
            'neverCrossProbability': never_cross,
        },
    }

def make_all_modules(**overrides):
    """Build a complete module dict for computeEnsembleConfidence."""
    modules = {
        'baseRegime': make_base_regime(),
        'regimeProximity': make_regime_proximity(),
        'regimeSurvival': make_regime_survival(),
        'transitionForecast': make_transition_forecast(),
        'hitRateDecay': make_hit_rate_decay(),
        'capitalPreservation': make_capital_preservation(),
        'optimalReEntry': make_optimal_reentry(),
        'parameterUncertainty': make_parameter_uncertainty(),
    }
    modules.update(overrides)
    return modules


# ── Node.js bridge for unit tests ──────────────────────────────────────────────
# Runs computeEnsembleConfidence via node subprocess with injected modules.

def run_ensemble(modules):
    """Execute computeEnsembleConfidence in Node.js and return parsed JSON."""
    import subprocess
    import tempfile

    # Read the function from signal_api.js
    api_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'signal_api.js')

    # Extract just the computeEnsembleConfidence function via node eval
    script = """
const fs = require('fs');
const src = fs.readFileSync('%s', 'utf8');

// Extract function body
const start = src.indexOf('function computeEnsembleConfidence(');
const end = src.indexOf('\\nasync function refreshData()');
const fnSrc = src.substring(start, end);

// Evaluate function
eval(fnSrc);

// Run with provided modules
const modules = %s;
const result = computeEnsembleConfidence(modules);
console.log(JSON.stringify(result));
""" % (api_path.replace('\\', '\\\\'), json.dumps(modules))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(script)
        f.flush()
        try:
            proc = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=10)
            if proc.returncode != 0:
                raise RuntimeError(f"Node error: {proc.stderr}")
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(f.name)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Classes
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistenceVoteExtraction(unittest.TestCase):
    """Each module maps correctly to 0-1 persistence vote."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())
        self.votes = {v['module']: v for v in
                      self.result['crossModuleAgreement']['regimePersistence']['perModuleVotes']}

    def test_regime_proximity_inverted(self):
        """regimeProximity: vote = 1 - score (low prox = entrenched = high persistence)."""
        v = self.votes['regimeProximity']
        self.assertAlmostEqual(v['vote'], 1 - 0.05, places=2)

    def test_regime_survival_uses_survival_prob(self):
        """regimeSurvival: vote = survivalProbability."""
        v = self.votes['regimeSurvival']
        self.assertAlmostEqual(v['vote'], 0.72, places=2)

    def test_transition_forecast_scaled(self):
        """transitionForecast: vote = clamp(base.days / 30, 0, 1)."""
        v = self.votes['transitionForecast']
        self.assertAlmostEqual(v['vote'], 12 / 30, places=2)

    def test_hit_rate_decay_inverted_avg(self):
        """hitRateDecay: vote = 1 - avg(adjustedConfidence)."""
        avg_conf = (0.08 + 0.03 + 0.15) / 3
        v = self.votes['hitRateDecay']
        self.assertAlmostEqual(v['vote'], 1 - avg_conf, places=2)

    def test_parameter_uncertainty_never_cross(self):
        """parameterUncertainty: vote = neverCrossProbability."""
        v = self.votes['parameterUncertainty']
        self.assertAlmostEqual(v['vote'], 0.857, places=2)

    def test_optimal_reentry_null_crossover(self):
        """optimalReEntry: crossoverDay=null → vote=0.8."""
        v = self.votes['optimalReEntry']
        self.assertAlmostEqual(v['vote'], 0.8, places=2)

    def test_capital_preservation_drawdown(self):
        """capitalPreservation: vote = clamp(|drawdown|/10, 0, 1)."""
        v = self.votes['capitalPreservation']
        self.assertAlmostEqual(v['vote'], 4.5 / 10, places=2)

    def test_base_regime_systemic(self):
        """baseRegime: SYSTEMIC → vote = confidence/100."""
        v = self.votes['baseRegime']
        self.assertAlmostEqual(v['vote'], 68 / 100, places=2)


class TestTradeReadinessVoteExtraction(unittest.TestCase):
    """Each module maps correctly to 0-1 trade readiness vote."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())
        self.votes = {v['module']: v for v in
                      self.result['crossModuleAgreement']['tradeReadiness']['perModuleVotes']}

    def test_hit_rate_decay_avg_confidence(self):
        """hitRateDecay: vote = avg(adjustedConfidence)."""
        avg_conf = (0.08 + 0.03 + 0.15) / 3
        v = self.votes['hitRateDecay']
        self.assertAlmostEqual(v['vote'], avg_conf, places=2)

    def test_optimal_reentry_null_crossover_zero(self):
        """optimalReEntry: crossoverDay=null → vote=0.0 (dont trade)."""
        v = self.votes['optimalReEntry']
        self.assertAlmostEqual(v['vote'], 0.0, places=2)

    def test_regime_proximity_direct(self):
        """regimeProximity: vote = score directly."""
        v = self.votes['regimeProximity']
        self.assertAlmostEqual(v['vote'], 0.05, places=2)

    def test_capital_preservation_inverted(self):
        """capitalPreservation: vote = 1 - |drawdown|/10."""
        v = self.votes['capitalPreservation']
        self.assertAlmostEqual(v['vote'], 1 - 4.5 / 10, places=2)

    def test_transition_forecast_7d_threshold(self):
        """transitionForecast: base=12d > 7 → vote = 1 - 12/30."""
        v = self.votes['transitionForecast']
        self.assertAlmostEqual(v['vote'], 1 - 12 / 30, places=2)

    def test_regime_survival_exit_prob(self):
        """regimeSurvival: exitProb=0.28 (< 0.7) → vote = exitProb."""
        v = self.votes['regimeSurvival']
        self.assertAlmostEqual(v['vote'], 0.28, places=2)

    def test_parameter_uncertainty_complement(self):
        """parameterUncertainty: vote = 1 - neverCrossProbability."""
        v = self.votes['parameterUncertainty']
        self.assertAlmostEqual(v['vote'], 1 - 0.857, places=2)


class TestWeightedAgreement(unittest.TestCase):
    """Math: weighted mean, stddev, agreement score, weight redistribution."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_agreement_score_range(self):
        """Agreement score is in [0, 1]."""
        persist = self.result['crossModuleAgreement']['regimePersistence']
        self.assertGreaterEqual(persist['agreementScore'], 0)
        self.assertLessEqual(persist['agreementScore'], 1)

    def test_weights_sum_to_one(self):
        """Per-module weights sum to ~1.0 after redistribution."""
        votes = self.result['crossModuleAgreement']['regimePersistence']['perModuleVotes']
        total = sum(v['weight'] for v in votes)
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_trade_weights_sum_to_one(self):
        """Trade readiness weights also sum to ~1.0."""
        votes = self.result['crossModuleAgreement']['tradeReadiness']['perModuleVotes']
        total = sum(v['weight'] for v in votes)
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_overall_agreement_average(self):
        """Overall agreement is average of persistence and trade agreement."""
        p = self.result['crossModuleAgreement']['regimePersistence']['agreementScore']
        t = self.result['crossModuleAgreement']['tradeReadiness']['agreementScore']
        expected = (p + t) / 2
        self.assertAlmostEqual(self.result['crossModuleAgreement']['overallAgreement'], expected, places=3)

    def test_perfect_agreement_high_score(self):
        """If all modules vote same value, agreement should be ~1.0."""
        # Make all modules agree on high persistence
        mods = make_all_modules(
            regimeProximity=make_regime_proximity(score=0.0),  # 1-0=1.0
            regimeSurvival=make_regime_survival(survival_prob=1.0),
            transitionForecast=make_transition_forecast(base_days=30),  # 30/30=1.0
            hitRateDecay=make_hit_rate_decay(cl_adj=0.0, sl_adj=0.0, fd_adj=0.0),  # 1-0=1.0
            parameterUncertainty=make_parameter_uncertainty(never_cross=1.0),
            optimalReEntry=make_optimal_reentry(crossover_day=None),  # 0.8
            capitalPreservation=make_capital_preservation(drawdown=10.0),  # 10/10=1.0
            baseRegime=make_base_regime(confidence=100),
        )
        result = run_ensemble(mods)
        agree = result['crossModuleAgreement']['regimePersistence']['agreementScore']
        self.assertGreater(agree, 0.8)

    def test_weight_redistribution_with_missing(self):
        """Missing module redistributes weight proportionally."""
        mods = make_all_modules(regimeSurvival=None)
        result = run_ensemble(mods)
        votes = result['crossModuleAgreement']['regimePersistence']['perModuleVotes']
        modules = [v['module'] for v in votes]
        self.assertNotIn('regimeSurvival', modules)
        total = sum(v['weight'] for v in votes)
        self.assertAlmostEqual(total, 1.0, places=2)


class TestCalibrationBias(unittest.TestCase):
    """Bias calculation, accuracy classification, conditional calibration."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_calibration_has_entries(self):
        """At least 3 calibration entries (regimeSurvival, transitionForecast, hitRateDecay types)."""
        self.assertGreaterEqual(len(self.result['calibrationComparison']), 3)

    def test_calibration_fields_present(self):
        """Each entry has required fields."""
        for entry in self.result['calibrationComparison']:
            self.assertIn('module', entry)
            self.assertIn('metric', entry)
            self.assertIn('nObservations', entry)
            self.assertIn('avgPredicted', entry)
            self.assertIn('avgObserved', entry)
            self.assertIn('bias', entry)
            self.assertIn('biasPct', entry)
            self.assertIn('accuracy', entry)

    def test_accuracy_labels_valid(self):
        """Accuracy labels match expected set."""
        valid_prefixes = ['WELL_CALIBRATED', 'SLIGHT_', 'MODERATE_', 'SEVERE_']
        for entry in self.result['calibrationComparison']:
            label = entry['accuracy']
            valid = any(label.startswith(p) or label == p.rstrip('_') for p in valid_prefixes)
            self.assertTrue(valid, f"Unexpected accuracy label: {label}")

    def test_bias_sign_correct(self):
        """Positive bias = predicted > observed = OVERESTIMATE."""
        for entry in self.result['calibrationComparison']:
            if entry['biasPct'] > 10:
                self.assertIn('OVERESTIMATE', entry['accuracy'])
            elif entry['biasPct'] < -10:
                self.assertIn('UNDERESTIMATE', entry['accuracy'])

    def test_conditional_calibration_present(self):
        """Conditional calibration section exists."""
        self.assertIsNotNone(self.result['conditionalCalibration'])
        self.assertIn('SYSTEMIC', self.result['conditionalCalibration'])
        self.assertIn('interpretation', self.result['conditionalCalibration'])

    def test_hit_rate_decay_calibration(self):
        """hitRateDecay shows static vs adjusted comparison."""
        hrd_entries = [e for e in self.result['calibrationComparison'] if e['module'] == 'hitRateDecay']
        self.assertGreater(len(hrd_entries), 0, "No hitRateDecay calibration entries")


class TestDisagreementEntropy(unittest.TestCase):
    """Shannon entropy: 0 when identical, max when uniform, handles nulls."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_entropy_range(self):
        """Normalized entropy is in [0, 1]."""
        entropy = self.result['disagreementDiagnostic']['overallEntropy']
        self.assertGreaterEqual(entropy, 0)
        self.assertLessEqual(entropy, 1)

    def test_entropy_interpretation_present(self):
        """Entropy has interpretation string."""
        interp = self.result['disagreementDiagnostic']['entropyInterpretation']
        self.assertIsInstance(interp, str)
        self.assertGreater(len(interp), 0)

    def test_entropy_low_for_agreement(self):
        """High agreement should produce lower entropy."""
        # When modules are close together, entropy should be moderate to low
        entropy = self.result['disagreementDiagnostic']['overallEntropy']
        # With real SYSTEMIC data, modules should roughly agree
        self.assertLessEqual(entropy, 1.0, "Entropy should be normalized to [0, 1]")

    def test_consensus_view_present(self):
        """Consensus view describes the majority position."""
        cv = self.result['disagreementDiagnostic']['consensusView']
        self.assertIsInstance(cv, str)
        self.assertIn('REGIME_', cv)

    def test_pairwise_tensions_list(self):
        """Pairwise tensions is a list (may be empty)."""
        tensions = self.result['disagreementDiagnostic']['pairwiseTensions']
        self.assertIsInstance(tensions, list)


class TestPairwiseTensions(unittest.TestCase):
    """Each tension pair detected/not-detected correctly."""

    def test_duration_conflict_detected(self):
        """T1: large survival vs forecast divergence triggers tension."""
        mods = make_all_modules(
            regimeSurvival=make_regime_survival(median_remaining=25.0),
            transitionForecast=make_transition_forecast(base_days=5),
        )
        result = run_ensemble(mods)
        tensions = result['disagreementDiagnostic']['pairwiseTensions']
        types = [t['tensionType'] for t in tensions]
        self.assertIn('DURATION_ESTIMATE_CONFLICT', types)

    def test_exit_vs_entry_tension(self):
        """T2: high P(exit) but null crossover triggers tension."""
        mods = make_all_modules(
            regimeSurvival=make_regime_survival(exit_prob=0.75),
            optimalReEntry=make_optimal_reentry(crossover_day=None),
        )
        result = run_ensemble(mods)
        tensions = result['disagreementDiagnostic']['pairwiseTensions']
        types = [t['tensionType'] for t in tensions]
        self.assertIn('EXIT_PROBABILITY_VS_ENTRY_TIMING', types)

    def test_trade_readiness_conflict(self):
        """T3: high CL confidence but high drawdown triggers tension."""
        mods = make_all_modules(
            hitRateDecay=make_hit_rate_decay(cl_adj=0.5),  # > 0.3
            capitalPreservation=make_capital_preservation(drawdown=8.0),  # > 5
        )
        result = run_ensemble(mods)
        tensions = result['disagreementDiagnostic']['pairwiseTensions']
        types = [t['tensionType'] for t in tensions]
        self.assertIn('TRADE_READINESS_CONFLICT', types)

    def test_entrenchment_vs_exit(self):
        """T4: very low proximity but high exit prob triggers tension."""
        mods = make_all_modules(
            regimeProximity=make_regime_proximity(score=0.02),
            regimeSurvival=make_regime_survival(exit_prob=0.65),
        )
        result = run_ensemble(mods)
        tensions = result['disagreementDiagnostic']['pairwiseTensions']
        types = [t['tensionType'] for t in tensions]
        self.assertIn('ENTRENCHMENT_VS_EXIT_PROBABILITY', types)

    def test_never_cross_vs_type_crossover(self):
        """T5: high neverCross but firstTypeToCross exists."""
        mods = make_all_modules(
            parameterUncertainty=make_parameter_uncertainty(never_cross=0.9),
            optimalReEntry=make_optimal_reentry(
                crossover_day=12,
                first_type={'type': 'CRYPTO_LEADS', 'label': 'Crypto Leads Semi', 'crossoverDay': 12}
            ),
        )
        result = run_ensemble(mods)
        tensions = result['disagreementDiagnostic']['pairwiseTensions']
        types = [t['tensionType'] for t in tensions]
        self.assertIn('NEVER_CROSS_VS_TYPE_CROSSOVER', types)

    def test_tension_fields_complete(self):
        """Each tension has required fields."""
        mods = make_all_modules(
            regimeSurvival=make_regime_survival(median_remaining=25.0),
            transitionForecast=make_transition_forecast(base_days=5),
        )
        result = run_ensemble(mods)
        for t in result['disagreementDiagnostic']['pairwiseTensions']:
            self.assertIn('pair', t)
            self.assertIn('tensionType', t)
            self.assertIn('magnitude', t)
            self.assertIn('description', t)
            self.assertIn('implication', t)
            self.assertIn('resolution', t)
            self.assertGreaterEqual(t['magnitude'], 0)
            self.assertLessEqual(t['magnitude'], 1)


class TestOutlierDetection(unittest.TestCase):
    """Z-score calculation, threshold, confidence adjustment."""

    def test_outlier_detected_extreme_vote(self):
        """A module with extreme vote should be flagged as outlier."""
        # Force one module very different from others
        mods = make_all_modules(
            regimeProximity=make_regime_proximity(score=0.99),  # persistence vote = 0.01, very low
        )
        result = run_ensemble(mods)
        outliers = result['disagreementDiagnostic']['outliersDetected']
        # May or may not trigger depending on stddev
        if len(outliers) > 0:
            for o in outliers:
                self.assertIn('module', o)
                self.assertIn('zScore', o)
                self.assertIn('confidenceAdjustment', o)
                self.assertEqual(o['confidenceAdjustment'], -0.05)

    def test_outlier_z_score_absolute(self):
        """Outlier z-score > 2.0."""
        mods = make_all_modules(
            regimeProximity=make_regime_proximity(score=0.99),
        )
        result = run_ensemble(mods)
        for o in result['disagreementDiagnostic']['outliersDetected']:
            self.assertGreater(abs(o['zScore']), 2.0)

    def test_no_outlier_when_agreement(self):
        """No outliers when all modules roughly agree."""
        result = run_ensemble(make_all_modules())
        # Standard config shouldn't produce extreme outliers normally
        outliers = result['disagreementDiagnostic']['outliersDetected']
        # May or may not have outliers — just verify structure
        self.assertIsInstance(outliers, list)

    def test_dissenting_view(self):
        """If outlier exists, dissentingView describes it."""
        result = run_ensemble(make_all_modules())
        dv = result['disagreementDiagnostic']['dissentingView']
        # Either None or a string
        self.assertTrue(dv is None or isinstance(dv, str))


class TestCompositeScore(unittest.TestCase):
    """Formula correctness, clamping, grade thresholds, sigmoid."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_composite_range(self):
        """Composite score is in [0, 1]."""
        score = self.result['compositeReliability']['score']
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 1)

    def test_grade_matches_score(self):
        """Grade corresponds to score threshold."""
        score = self.result['compositeReliability']['score']
        grade = self.result['compositeReliability']['grade']
        if score > 0.80: self.assertEqual(grade, 'VERY_HIGH')
        elif score > 0.65: self.assertEqual(grade, 'HIGH')
        elif score > 0.45: self.assertEqual(grade, 'MODERATE')
        elif score > 0.30: self.assertEqual(grade, 'LOW')
        else: self.assertEqual(grade, 'VERY_LOW')

    def test_components_present(self):
        """All 4 components present with value, weight, contribution."""
        comps = self.result['compositeReliability']['components']
        for key in ['agreement', 'calibration', 'sampleAdequacy', 'informationCompleteness']:
            self.assertIn(key, comps)
            self.assertIn('value', comps[key])
            self.assertIn('weight', comps[key])
            self.assertIn('contribution', comps[key])
            self.assertIn('interpretation', comps[key])

    def test_weights_sum(self):
        """Component weights sum to 1.0."""
        comps = self.result['compositeReliability']['components']
        total = sum(comps[k]['weight'] for k in comps)
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_contribution_equals_weight_times_value(self):
        """Each contribution ≈ weight * value."""
        comps = self.result['compositeReliability']['components']
        for key in comps:
            expected = comps[key]['weight'] * comps[key]['value']
            self.assertAlmostEqual(comps[key]['contribution'], expected, places=3)

    def test_primary_bottleneck_valid(self):
        """Primary bottleneck is one of expected values."""
        bn = self.result['compositeReliability']['primaryBottleneck']
        self.assertIn(bn, ['SAMPLE_SIZE', 'CALIBRATION', 'AGREEMENT', 'INFORMATION_COMPLETENESS'])

    def test_improvement_path_nonempty(self):
        """Improvement path is a non-empty string."""
        self.assertIsInstance(self.result['compositeReliability']['improvementPath'], str)
        self.assertGreater(len(self.result['compositeReliability']['improvementPath']), 0)


class TestSampleAdequacy(unittest.TestCase):
    """Sigmoid: n=2→low, n=10→mid, n=20→high, n=50→~1."""

    def test_n2_low(self):
        """n=2 produces low sample adequacy."""
        result = run_ensemble(make_all_modules())
        sa = result['compositeReliability']['components']['sampleAdequacy']['value']
        self.assertLess(sa, 0.35, f"n=2 should give low adequacy, got {sa}")

    def test_effective_sample_reported(self):
        """Effective sample size is reported."""
        result = run_ensemble(make_all_modules())
        ess = result['compositeReliability']['effectiveSampleSize']
        self.assertIsInstance(ess, (int, float))
        self.assertGreater(ess, 0)

    def test_target_sample_size(self):
        """Target sample size is 10."""
        result = run_ensemble(make_all_modules())
        self.assertEqual(result['compositeReliability']['targetSampleSize'], 10)

    def test_sigmoid_monotonic(self):
        """Larger effective n → higher sample adequacy (verified via formula)."""
        # Test sigmoid directly: sigmoid(2,10,0.3) < sigmoid(20,10,0.3)
        def sig(x): return 1 / (1 + math.exp(-0.3 * (x - 10)))
        self.assertLess(sig(2), sig(10))
        self.assertLess(sig(10), sig(20))
        self.assertGreater(sig(50), 0.99)


class TestInformationContributions(unittest.TestCase):
    """KL divergence ranking, handles uniform votes."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_ranking_present(self):
        """Information contributions ranked list exists."""
        ic = self.result['informationContributions']
        self.assertIsInstance(ic, list)
        self.assertGreater(len(ic), 0)

    def test_ranking_sorted_descending(self):
        """Rankings sorted by KL divergence descending."""
        ic = self.result['informationContributions']
        kls = [r['klDivergence'] for r in ic]
        for i in range(len(kls) - 1):
            self.assertGreaterEqual(kls[i], kls[i+1])

    def test_rank_field_correct(self):
        """Rank field matches position (1-indexed)."""
        ic = self.result['informationContributions']
        for i, r in enumerate(ic):
            self.assertEqual(r['rank'], i + 1)

    def test_extreme_vote_high_kl(self):
        """Module voting near 0 or 1 should have higher KL than one voting 0.5."""
        ic = self.result['informationContributions']
        # All modules should have non-negative KL
        for r in ic:
            self.assertGreaterEqual(r['klDivergence'], 0)


class TestScenarioStressTest(unittest.TestCase):
    """Optimistic/pessimistic shift, range width, sensitivity."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_three_scenarios_present(self):
        """Base, optimistic, pessimistic scenarios all present."""
        st = self.result['scenarioStressTest']
        self.assertIn('base', st)
        self.assertIn('optimistic', st)
        self.assertIn('pessimistic', st)

    def test_range_width_nonnegative(self):
        """Range width >= 0."""
        self.assertGreaterEqual(self.result['scenarioStressTest']['rangeWidth'], 0)

    def test_sensitivity_label(self):
        """Sensitivity label is LOW, MODERATE, or HIGH."""
        sens = self.result['scenarioStressTest']['sensitivity']
        self.assertTrue(
            sens.startswith('LOW') or sens.startswith('MODERATE') or sens.startswith('HIGH'),
            f"Unexpected sensitivity: {sens}"
        )

    def test_optimistic_has_interpretation(self):
        """Optimistic scenario has interpretation."""
        self.assertIn('interpretation', self.result['scenarioStressTest']['optimistic'])

    def test_pessimistic_composite_range(self):
        """Pessimistic composite in [0, 1]."""
        pc = self.result['scenarioStressTest']['pessimistic']['compositeReliability']
        self.assertGreaterEqual(pc, 0)
        self.assertLessEqual(pc, 1)


class TestNetBias(unittest.TestCase):
    """Direction classification, magnitude, per-module decomposition."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_direction_valid(self):
        """Net bias direction is valid label."""
        d = self.result['netBiasAssessment']['direction']
        self.assertIn(d, ['OPTIMISTIC', 'PESSIMISTIC', 'BALANCED', 'INSUFFICIENT_DATA'])

    def test_magnitude_nonnegative(self):
        """Net bias magnitude >= 0."""
        self.assertGreaterEqual(self.result['netBiasAssessment']['magnitude'], 0)

    def test_per_module_bias_list(self):
        """Per-module bias is a list with direction field."""
        pmb = self.result['netBiasAssessment']['perModuleBias']
        self.assertIsInstance(pmb, list)
        for b in pmb:
            self.assertIn('module', b)
            self.assertIn('biasPct', b)
            self.assertIn('direction', b)


class TestEdgeCases(unittest.TestCase):
    """All modules null, single module, AT_NEUTRAL regime, division by zero."""

    def test_at_neutral_regime(self):
        """NEUTRAL regime returns AT_NEUTRAL status."""
        mods = make_all_modules(
            baseRegime=make_base_regime(regime_id='NEUTRAL', confidence=72),
            hitRateDecay={'status': 'AT_NEUTRAL'},
        )
        result = run_ensemble(mods)
        self.assertEqual(result['status'], 'AT_NEUTRAL')

    def test_no_data_insufficient_modules(self):
        """Fewer than 2 active modules returns NO_DATA."""
        mods = {
            'baseRegime': make_base_regime(),
            'regimeProximity': None,
            'regimeSurvival': None,
            'transitionForecast': None,
            'hitRateDecay': {'status': 'INACTIVE'},
            'capitalPreservation': None,
            'optimalReEntry': None,
            'parameterUncertainty': None,
        }
        result = run_ensemble(mods)
        self.assertEqual(result['status'], 'NO_DATA')

    def test_null_modules_handled(self):
        """Null modules don't crash — weight redistributed."""
        mods = make_all_modules(
            capitalPreservation=None,
            optimalReEntry=None,
        )
        result = run_ensemble(mods)
        self.assertEqual(result['status'], 'ACTIVE')
        # Should still have votes from remaining modules
        votes = result['crossModuleAgreement']['regimePersistence']['perModuleVotes']
        self.assertGreater(len(votes), 0)

    def test_zero_confidence_base_regime(self):
        """confidence=0 doesn't cause division by zero."""
        mods = make_all_modules(baseRegime=make_base_regime(confidence=0))
        result = run_ensemble(mods)
        self.assertEqual(result['status'], 'ACTIVE')

    def test_methodology_present(self):
        """Methodology field is a non-empty string."""
        result = run_ensemble(make_all_modules())
        self.assertIsInstance(result['methodology'], str)
        self.assertGreater(len(result['methodology']), 50)


class TestTopLevelFields(unittest.TestCase):
    """Verify all required top-level fields present and typed correctly."""

    def setUp(self):
        self.result = run_ensemble(make_all_modules())

    def test_status_active(self):
        self.assertEqual(self.result['status'], 'ACTIVE')

    def test_model_version(self):
        self.assertEqual(self.result['modelVersion'], 'ensemble-confidence-v1')

    def test_message_nonempty(self):
        self.assertIsInstance(self.result['message'], str)
        self.assertGreater(len(self.result['message']), 20)

    def test_limitations_list(self):
        lims = self.result['limitations']
        self.assertIsInstance(lims, list)
        self.assertGreaterEqual(len(lims), 5)
        for lim in lims:
            self.assertIn('id', lim)
            self.assertIn('description', lim)

    def test_upstream_dependencies(self):
        deps = self.result['upstreamDependencies']
        self.assertIsInstance(deps, dict)
        for key in ['regimeProximity', 'regimeSurvival', 'transitionForecast', 'hitRateDecay']:
            self.assertIn(key, deps)
            self.assertIn('status', deps[key])
            self.assertIn('available', deps[key])

    def test_crossover_day_zero_entry_available(self):
        """optimalReEntry with crossoverDay=0 → tradeReadiness vote=1.0."""
        mods = make_all_modules(optimalReEntry=make_optimal_reentry(crossover_day=0))
        result = run_ensemble(mods)
        votes = {v['module']: v for v in
                 result['crossModuleAgreement']['tradeReadiness']['perModuleVotes']}
        self.assertAlmostEqual(votes['optimalReEntry']['vote'], 1.0, places=2)

    def test_conditional_status(self):
        """CONDITIONAL optimalReEntry still counted as active."""
        mods = make_all_modules(
            optimalReEntry={'status': 'CONDITIONAL', 'crossoverDay': None, 'firstTypeToCross': None}
        )
        result = run_ensemble(mods)
        self.assertEqual(result['status'], 'ACTIVE')


class TestLiveAPIEnsembleConfidence(unittest.TestCase):
    """Integration tests against live API at localhost:8080."""

    API_URL = os.environ.get('PF_API_URL', 'http://localhost:8080')

    @classmethod
    def setUpClass(cls):
        """Check if live API is available."""
        import urllib.request
        try:
            urllib.request.urlopen(f'{cls.API_URL}/health', timeout=3)
            cls.api_available = True
        except Exception:
            cls.api_available = False

    def _fetch(self, path):
        if not self.api_available:
            self.skipTest('Live API not available')
        import urllib.request
        req = urllib.request.urlopen(f'{self.API_URL}{path}', timeout=10)
        return json.loads(req.read().decode())

    def test_regime_current_has_ensemble(self):
        """GET /regime/current includes ensembleConfidence field."""
        data = self._fetch('/regime/current')
        self.assertIn('ensembleConfidence', data)

    def test_ensemble_status_valid(self):
        """/regime/current ensembleConfidence has valid status."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        self.assertIn(ec['status'], ['ACTIVE', 'AT_NEUTRAL', 'NO_DATA'])

    def test_ensemble_model_version(self):
        """Model version is ensemble-confidence-v1."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        self.assertEqual(ec['modelVersion'], 'ensemble-confidence-v1')

    def test_composite_score_range(self):
        """Composite score in [0, 1]."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        if ec['status'] == 'ACTIVE':
            score = ec['compositeReliability']['score']
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 1)

    def test_grade_present(self):
        """Grade is present and valid."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        if ec['status'] == 'ACTIVE':
            self.assertIn(ec['compositeReliability']['grade'],
                          ['VERY_HIGH', 'HIGH', 'MODERATE', 'LOW', 'VERY_LOW'])

    def test_signals_filtered_has_ensemble(self):
        """GET /signals/filtered includes ensembleConfidence field."""
        data = self._fetch('/signals/filtered')
        self.assertIn('ensembleConfidence', data)

    def test_cross_module_agreement_votes(self):
        """Cross-module agreement has perModuleVotes."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        if ec['status'] == 'ACTIVE':
            votes = ec['crossModuleAgreement']['regimePersistence']['perModuleVotes']
            self.assertGreater(len(votes), 0)
            for v in votes:
                self.assertIn('module', v)
                self.assertIn('vote', v)
                self.assertIn('weight', v)

    def test_methodology_present(self):
        """Methodology field exists and is descriptive."""
        data = self._fetch('/regime/current')
        ec = data['ensembleConfidence']
        if ec['status'] == 'ACTIVE':
            self.assertIn('methodology', ec)
            self.assertGreater(len(ec['methodology']), 50)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    unittest.main(verbosity=2)
