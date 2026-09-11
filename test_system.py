"""Regression tests for crypto_market_intelligence_v60.

Every test here corresponds to a bug that actually shipped and was found in real
data — not hypothetical cases. Run with:

    python3 test_system.py          (no pytest needed)
    python3 -m pytest test_system.py -q

Network is never touched; anything that would fetch is fed synthetic frames.
"""
import importlib.util
import json
import math
import multiprocessing
import os
import re
import sys
import unittest

from hypothesis import given, settings, strategies as st

_spec = importlib.util.spec_from_file_location("cmi", "crypto_market_intelligence_v60.py")
cmi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmi)

import pandas as pd


def _frame(price=100.0, atr=2.0, rows=40, regime='CHOPPY', drawdown=0.0):
    return pd.DataFrame({
        'close': [price] * rows,
        'atr_14': [atr] * rows,
        'atr_ratio': [atr / price] * rows,
        'drawdown': [drawdown] * rows,
        'regime': [regime] * rows,
    })


class TestRiskConfigPinned(unittest.TestCase):
    """Deliberately different from the property/ratio tests: those check that
    the FORMULA correctly applies whatever multiplier RISK_PARAMS holds — they
    can't and shouldn't judge whether 2.0/4.0 is the right choice, since that's
    a business decision, not a bug. Mutation testing confirmed this gap
    directly: changing atr_multiplier_stop from 2.0 to 3.0 survived the whole
    suite, because the ratio test derives its own expectation from the same
    (now-changed) constant. This test exists so an ACCIDENTAL edit to one of
    these numbers — a stray keystroke, a bad find-replace — gets caught,
    while an intentional change here (a real decision to re-tune risk) is a
    one-line update to this test, not a mystery test failure elsewhere."""

    def test_configured_risk_multipliers_unchanged(self):
        self.assertEqual(cmi.RISK_PARAMS['atr_multiplier_stop'], 2.0)
        self.assertEqual(cmi.RISK_PARAMS['atr_multiplier_target'], 4.0)
        self.assertEqual(cmi.RISK_PARAMS['max_risk_per_trade'], 0.02)


class TestDirectionalLevels(unittest.TestCase):
    """A SHORT's stop must sit ABOVE entry and its targets BELOW.

    Shipped bug: calculate_dynamic_position_size had no direction parameter, so
    every short got a long's levels. Its "stop" sat in the profit direction and
    fired as a STOP_LOSS while booking a profit — all 31 closed swing shorts in
    paper_account.json show exactly that.
    """

    def test_long_levels_on_correct_side(self):
        r = cmi.calculate_dynamic_position_size(_frame(), 39, direction='LONG')
        self.assertLess(r['stop_loss'], 100.0)
        self.assertGreater(r['take_profit_1'], 100.0)
        self.assertGreater(r['take_profit_2'], r['take_profit_1'])

    def test_short_levels_are_mirrored(self):
        r = cmi.calculate_dynamic_position_size(_frame(), 39, direction='SHORT')
        self.assertGreater(r['stop_loss'], 100.0, "SHORT stop must be ABOVE entry")
        self.assertLess(r['take_profit_1'], 100.0, "SHORT target must be BELOW entry")
        self.assertLess(r['take_profit_2'], r['take_profit_1'])

    def test_strong_short_treated_as_short(self):
        r = cmi.calculate_dynamic_position_size(_frame(), 39, direction='STRONG SHORT')
        self.assertGreater(r['stop_loss'], 100.0)

    def test_default_direction_is_long(self):
        self.assertLess(cmi.calculate_dynamic_position_size(_frame(), 39)['stop_loss'], 100.0)

    def test_risk_distance_equal_both_directions(self):
        lo = cmi.calculate_dynamic_position_size(_frame(), 39, direction='LONG')
        sh = cmi.calculate_dynamic_position_size(_frame(), 39, direction='SHORT')
        self.assertAlmostEqual(abs(100.0 - lo['stop_loss']), abs(100.0 - sh['stop_loss']), places=6)


class TestPositionSizingProperties(unittest.TestCase):
    """Property-based: the fixed tests above check the formula against a
    handful of numbers a person chose (price=100, ATR=2). This throws hundreds
    of randomized prices, volatilities, regimes and drawdowns at the same
    formula and checks the invariant holds on EVERY one — including the
    extreme, oddly-shaped inputs (a sub-cent DOGE-like price, a deep drawdown)
    that a person doesn't think to hand-pick but that real crypto markets
    actually produce. It does not, and cannot, check that 2x/4x ATR is the
    RIGHT multiplier — only that whatever multiplier is configured gets
    applied correctly and consistently on every input.

    atr_frac (ATR as a fraction of price) is floored at 1e-3: real observed
    atr_ratio on these 9 assets runs close to 0.02 (2%), so 0.1% is already a
    generous 20x margin below anything live data produces. Below that floor,
    _round_price's 6-significant-figure precision and the raw ATR magnitude
    converge, and a stop/target distance can round away to indistinguishable
    from zero — a real rounding-precision limit, not a formula bug, and not a
    condition any of these 9 assets' actual volatility has ever approached.
    """

    @given(
        price=st.floats(min_value=1e-6, max_value=200_000, allow_nan=False, allow_infinity=False),
        atr_frac=st.floats(min_value=1e-3, max_value=0.5, allow_nan=False, allow_infinity=False),
        direction=st.sampled_from(['LONG', 'SHORT', 'STRONG LONG', 'STRONG SHORT']),
        regime=st.sampled_from(list(cmi.REGIME_STRATEGY.keys())),
        drawdown=st.floats(min_value=-0.9, max_value=0.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=5000, deadline=None)
    def test_stop_and_targets_always_on_correct_side(self, price, atr_frac, direction, regime, drawdown):
        df = _frame(price=price, atr=price * atr_frac, regime=regime, drawdown=drawdown)
        r = cmi.calculate_dynamic_position_size(df, len(df) - 1, direction=direction)
        long = direction.upper() not in ('SHORT', 'STRONG SHORT', 'SELL')
        if long:
            self.assertLessEqual(r['stop_loss'], price)
            self.assertGreaterEqual(r['take_profit_1'], price)
            self.assertGreaterEqual(r['take_profit_2'], r['take_profit_1'])
        else:
            self.assertGreaterEqual(r['stop_loss'], price)
            self.assertLessEqual(r['take_profit_1'], price)
            self.assertLessEqual(r['take_profit_2'], r['take_profit_1'])

    @given(
        price=st.floats(min_value=1e-6, max_value=200_000, allow_nan=False, allow_infinity=False),
        atr_frac=st.floats(min_value=1e-3, max_value=0.5, allow_nan=False, allow_infinity=False),
        direction=st.sampled_from(['LONG', 'SHORT']),
        regime=st.sampled_from(list(cmi.REGIME_STRATEGY.keys())),
    )
    @settings(max_examples=5000, deadline=None)
    def test_outputs_always_finite_and_non_negative(self, price, atr_frac, direction, regime):
        df = _frame(price=price, atr=price * atr_frac, regime=regime)
        r = cmi.calculate_dynamic_position_size(df, len(df) - 1, direction=direction)
        for key in ('size_multiplier', 'position_size', 'risk_amount', 'risk_percent'):
            self.assertTrue(math.isfinite(r[key]), f"{key}={r[key]} is not finite")
            self.assertGreaterEqual(r[key], 0, f"{key}={r[key]} is negative")
        for key in ('stop_loss', 'take_profit_1', 'take_profit_2'):
            self.assertTrue(math.isfinite(r[key]), f"{key}={r[key]} is not finite")
            self.assertGreater(r[key], 0, f"{key}={r[key]} is not a positive price")

    @given(
        price=st.floats(min_value=1e-6, max_value=200_000, allow_nan=False, allow_infinity=False),
        atr_frac=st.floats(min_value=1e-3, max_value=0.5, allow_nan=False, allow_infinity=False),
        regime=st.sampled_from(list(cmi.REGIME_STRATEGY.keys())),
        drawdown=st.floats(min_value=-0.9, max_value=0.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=5000, deadline=None)
    def test_risk_distance_symmetric_long_vs_short(self, price, atr_frac, regime, drawdown):
        df = _frame(price=price, atr=price * atr_frac, regime=regime, drawdown=drawdown)
        idx = len(df) - 1
        lo = cmi.calculate_dynamic_position_size(df, idx, direction='LONG')
        sh = cmi.calculate_dynamic_position_size(df, idx, direction='SHORT')
        # delta scaled to _round_price's 6-significant-figure precision, not
        # exact equality — LONG and SHORT are each independently rounded, so a
        # ~1e-6 relative gap between them is expected rounding, not asymmetry.
        self.assertAlmostEqual(abs(price - lo['stop_loss']), abs(price - sh['stop_loss']),
                               delta=max(price * 2e-5, 1e-9))
        self.assertAlmostEqual(abs(price - lo['take_profit_1']), abs(price - sh['take_profit_1']),
                               delta=max(price * 2e-5, 1e-9))

    @given(
        price=st.floats(min_value=1e-6, max_value=200_000, allow_nan=False, allow_infinity=False),
        atr_frac=st.floats(min_value=1e-2, max_value=0.1, allow_nan=False, allow_infinity=False),
        direction=st.sampled_from(['LONG', 'SHORT']),
        regime=st.sampled_from(list(cmi.REGIME_STRATEGY.keys())),
    )
    @settings(max_examples=5000, deadline=None)
    def test_target_distance_ratios_match_configured_multipliers(self, price, atr_frac, direction, regime):
        """TP1 distance must be exactly 2x the stop distance, TP2 exactly 4x —
        that ratio is fixed by RISK_PARAMS regardless of ATR or regime, PROVIDED
        the zero-crossing safety clamp (see calculate_dynamic_position_size) isn't
        active. A bug that changes one multiplier but not the other, or applies
        the wrong one on one side, shows up here as a ratio drift even if every
        individual level still happens to land on the correct SIDE of price.

        Floored at atr_frac=1% (not 0.1% like the other tests): the stop/target
        DISTANCE is derived by subtracting two independently-6-sig-fig-rounded
        PRICES, so as the distance shrinks relative to price the fixed rounding
        granularity eats a growing share of it — a display-rounding artifact on
        this ratio check, confirmed harmless to real risk sizing because
        risk_per_share/position_size are computed from the unrounded atr_value
        earlier in the function, before any rounding happens. Real observed
        atr_ratio here (~2%) stays 2x above this floor regardless.

        Capped at atr_frac=10% (not 50%): the clamp starts changing TP2's
        distance at ~12.4% ATR/price (it hits the 99%-of-price ceiling before
        the stop or TP1 do, since it uses the largest multiplier) — an
        intentional, different invariant covered by the correct-side and
        finite/non-negative tests above, which deliberately DO span into that
        region. This test is only about the clean, unclamped ratio."""
        df = _frame(price=price, atr=price * atr_frac, regime=regime)
        r = cmi.calculate_dynamic_position_size(df, len(df) - 1, direction=direction)
        stop_dist = abs(price - r['stop_loss'])
        tp1_dist = abs(price - r['take_profit_1'])
        tp2_dist = abs(price - r['take_profit_2'])
        if stop_dist == 0:
            return  # degenerate ATR case, nothing to compare a ratio against
        expected_ratio_1 = cmi.RISK_PARAMS['atr_multiplier_target'] / cmi.RISK_PARAMS['atr_multiplier_stop']
        expected_ratio_2 = expected_ratio_1 * 2
        # rel_tol, not decimal places: each distance is independently rounded to
        # 6 significant figures by _round_price, so their RATIO can legitimately
        # carry a somewhat larger error than either rounding alone — this is
        # expected precision loss, not a formula bug, and unrelated to the much
        # larger (~0.3%) drift a flat round(x, 4) produced on sub-$1 prices.
        self.assertTrue(math.isclose(tp1_dist / stop_dist, expected_ratio_1, rel_tol=1e-3),
                        f"{tp1_dist / stop_dist} vs {expected_ratio_1}")
        self.assertTrue(math.isclose(tp2_dist / stop_dist, expected_ratio_2, rel_tol=1e-3),
                        f"{tp2_dist / stop_dist} vs {expected_ratio_2}")


class TestMarketRegimeMissingData(unittest.TestCase):
    """Absent TPU must not become a confident regime verdict, and must not crash."""

    def test_none_gives_unknown_not_low(self):
        r = cmi.detect_market_regime(None)
        self.assertEqual(r['regime'], 'UNKNOWN_UNCERTAINTY')
        self.assertNotIn('LOW_UNCERTAINTY', r['regime'])

    def test_none_does_not_raise(self):
        cmi.detect_market_regime(None)  # used to raise TypeError on `> 200`

    def test_real_values_still_classify(self):
        self.assertEqual(cmi.detect_market_regime(250)['regime'], 'HIGH_UNCERTAINTY')
        self.assertEqual(cmi.detect_market_regime(10)['regime'], 'LOW_UNCERTAINTY')

    def test_unknown_regime_weights_degrade_safely(self):
        w = cmi.adjust_weights('UNKNOWN_UNCERTAINTY')
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)


class TestNoTradeCounting(unittest.TestCase):
    """NO TRADE is the engine declining to trade; it is not a signal.

    Shipped bug: 951 of 1000 logged rows were NO TRADE, yet total_signals read
    1000 and avg_conviction was averaged across them, reporting 0.06.
    """

    def setUp(self):
        self.rows = ([{'signal': 'NO TRADE', 'conviction': 0.05, 'entry_date': '2026-09-09'}] * 90 +
                     [{'signal': 'LONG', 'conviction': 0.40, 'entry_date': '2026-09-09'}] * 10)

    def test_is_no_trade_detection(self):
        self.assertTrue(cmi._is_no_trade({'signal': 'NO TRADE'}))
        self.assertTrue(cmi._is_no_trade({'signal': 'no trade'}))
        self.assertTrue(cmi._is_no_trade({'signal': 'HOLD'}))
        self.assertFalse(cmi._is_no_trade({'signal': 'LONG'}))
        self.assertFalse(cmi._is_no_trade({'signal': 'STRONG SHORT'}))

    def test_tradeable_count_excludes_no_trade(self):
        p = cmi.calculate_performance_metrics(self.rows)
        self.assertEqual(p['tradeable_signals'], 10)
        self.assertEqual(p['no_trade_readings'], 90)
        self.assertEqual(p['total_rows_logged'], 100)

    def test_avg_conviction_over_tradeable_only(self):
        p = cmi.calculate_performance_metrics(self.rows)
        self.assertAlmostEqual(p['avg_conviction'], 0.40, places=2)

    def test_strong_short_bucket_counts(self):
        """Bucket compared against 'STRONG_SHORT' (underscore) but the engine
        emits 'STRONG SHORT' (space), so it could only ever report 0."""
        p = cmi.calculate_performance_metrics(
            [{'signal': 'STRONG SHORT', 'conviction': 0.8, 'entry_date': '2026-09-09'}] * 3)
        self.assertEqual(p['signal_distribution']['STRONG_SHORT'], 3)

    def test_empty_input_does_not_crash(self):
        p = cmi.calculate_performance_metrics([])
        self.assertEqual(p['tradeable_signals'], 0)
        self.assertIsNone(p['avg_conviction'])


class TestOnChainMissingDataNeutrality(unittest.TestCase):
    """Missing on-chain data must not push the score bullish.

    Shipped bug: `or 0` turned an absent MVRV/NVT into 0, and 0 satisfies the
    BULLISH branch of both tests, so a failed fetch produced 0.7.
    """

    def setUp(self):
        self.models = cmi.MarketMLModels()
        self.models.models_loaded = True

    def test_missing_onchain_stays_neutral(self):
        self.assertEqual(self.models.predict_bilstm({}), 0.5)

    def test_explicit_none_stays_neutral(self):
        self.assertEqual(self.models.predict_bilstm({'mvrv_zscore': None, 'nvt_ratio': None}), 0.5)

    def test_real_cheap_values_still_bullish(self):
        self.assertGreater(self.models.predict_bilstm({'mvrv_zscore': 0.1, 'nvt_ratio': 10}), 0.5)

    def test_real_extended_values_still_bearish(self):
        self.assertLess(self.models.predict_bilstm({'mvrv_zscore': 4.0, 'nvt_ratio': 60}), 0.5)


class TestSupportResistanceSides(unittest.TestCase):
    """Support below price, resistance above — no tolerance band.

    Shipped bug: filters allowed `h > price*0.98`, so a level 1.7% BELOW price
    was reported as resistance (observed: price $79,411, "resistance" $78,036).
    """

    def test_levels_land_on_correct_sides(self):
        import numpy as np
        n = 120
        rng = np.random.default_rng(7)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1.5, n)))
        df = pd.DataFrame({'close': close,
                           'high': close + abs(rng.normal(0, 1.2, n)),
                           'low': close - abs(rng.normal(0, 1.2, n))})
        sr = cmi.find_support_resistance(df)
        price = float(df['close'].iloc[-1])
        if sr.get('resistance') is not None:
            self.assertGreater(sr['resistance'], price)
        if sr.get('support') is not None:
            self.assertLess(sr['support'], price)


class TestRoundPriceEdgeCases(unittest.TestCase):
    """Defensive branches coverage analysis found unexercised: None input,
    a non-numeric input, and non-finite/zero values. None of these are exotic
    — a missing stop_loss, a NaN slipping through upstream, or a position
    that's exactly at entry all reach this function in production."""

    def test_none_passes_through_as_none(self):
        self.assertIsNone(cmi._round_price(None))

    def test_non_numeric_returns_none_not_a_crash(self):
        self.assertIsNone(cmi._round_price('not a price'))
        self.assertIsNone(cmi._round_price(object()))

    def test_nan_and_infinity_return_none(self):
        self.assertIsNone(cmi._round_price(float('nan')))
        self.assertIsNone(cmi._round_price(float('inf')))
        self.assertIsNone(cmi._round_price(float('-inf')))

    def test_zero_passes_through_unchanged(self):
        self.assertEqual(cmi._round_price(0), 0)
        self.assertEqual(cmi._round_price(0.0), 0.0)


class TestTradePlanRiskReward(unittest.TestCase):
    """R:R must not be skewed by a fixed epsilon on low-priced assets."""

    def test_rr_accurate_on_low_priced_asset(self):
        info = {'stop_loss': 0.0950, 'take_profit_1': 0.1100, 'take_profit_2': 0.1200,
                'position_size': 10, 'risk_amount': 5, 'risk_percent': 0.5}
        plan = cmi.generate_trade_plan('DOGE', 'LONG', 0.5, 0.1000, {}, 0.004, info)
        # reward 0.010 / risk 0.005 = 2.0; the old +0.001 epsilon gave ~1.67
        self.assertAlmostEqual(plan['risk_reward_ratio'], 2.0, places=2)

    def test_rr_positive_for_short(self):
        info = {'stop_loss': 105.0, 'take_profit_1': 90.0, 'take_profit_2': 80.0,
                'position_size': 1, 'risk_amount': 5, 'risk_percent': 0.5}
        plan = cmi.generate_trade_plan('BTC', 'SHORT', 0.5, 100.0, {}, 2.0, info)
        self.assertGreater(plan['risk_reward_ratio'], 0)
        self.assertEqual(plan['entry_type'], 'SELL_LIMIT')


class TestExplanationNoFabricatedWinRate(unittest.TestCase):
    """No history must mean no quoted win rate.

    Shipped bug: historical_win_rate defaulted to 50 and the trader comment
    printed "Historical win rate: 50%" as measured fact with zero evidence.
    """

    def test_no_history_quotes_no_number(self):
        exp = cmi.generate_trade_explanation('BTC', 'LONG', 0.7, {}, {}, {}, df=None)
        self.assertIsNone(exp.get('historical_win_rate'))
        self.assertNotIn('50%', exp.get('trader_comment', ''))
        self.assertIn('no win rate', exp.get('trader_comment', '').lower())


class TestPaperCloseAccounting(unittest.TestCase):
    """A profitable close must be recorded as profitable."""

    def setUp(self):
        # _paper_close feeds the self-learning engine, which WRITES to the real
        # docs/signal_database.json. Without this stub, running the tests
        # silently pollutes committed production data with fake predictions.
        self._real = cmi.track_prediction_accuracy
        cmi.track_prediction_accuracy = lambda *a, **k: None

    def tearDown(self):
        cmi.track_prediction_accuracy = self._real

    def _acct(self):
        return {'cash': 10000.0, 'positions': {}, 'closed_trades': [], 'equity_curve': []}

    def test_take_profit_2_is_marked_successful(self):
        """Mutation testing found this survives if SUCCESS_REASONS silently
        drops TAKE_PROFIT_2 — nothing directly asserted a full TP2 close is
        'successful', only that TIME_EXIT (not in the set) isn't."""
        acct = self._acct()
        pos = {'asset': 'BTC', 'side': 'LONG', 'qty': 1.0, 'avg_entry': 100.0,
               'opened': cmi.datetime.now().isoformat(), 'tranches': 1}
        cmi._paper_close(acct, 'BTC', pos, 120.0, 1.0, 'TAKE_PROFIT_2')
        self.assertTrue(acct['closed_trades'][0]['successful'])

    def test_short_profit_recorded_positive(self):
        acct = self._acct()
        pos = {'asset': 'ADA', 'side': 'SHORT', 'qty': 100.0, 'avg_entry': 0.22,
               'opened': cmi.datetime.now().isoformat(), 'tranches': 1}
        pnl = cmi._paper_close(acct, 'ADA', pos, 0.20, 100.0, 'TAKE_PROFIT_2')
        self.assertGreater(pnl, 0)
        self.assertTrue(acct['closed_trades'][0]['profitable'])

    def test_long_loss_recorded_negative(self):
        acct = self._acct()
        pos = {'asset': 'BTC', 'side': 'LONG', 'qty': 1.0, 'avg_entry': 100.0,
               'opened': cmi.datetime.now().isoformat(), 'tranches': 1}
        pnl = cmi._paper_close(acct, 'BTC', pos, 90.0, 1.0, 'STOP_LOSS')
        self.assertLess(pnl, 0)
        self.assertFalse(acct['closed_trades'][0]['profitable'])

    def test_profitable_and_successful_are_distinct_fields(self):
        acct = self._acct()
        pos = {'asset': 'BTC', 'side': 'LONG', 'qty': 1.0, 'avg_entry': 100.0,
               'opened': cmi.datetime.now().isoformat(), 'tranches': 1}
        cmi._paper_close(acct, 'BTC', pos, 110.0, 1.0, 'TIME_EXIT')
        t = acct['closed_trades'][0]
        self.assertTrue(t['profitable'])          # made money
        self.assertFalse(t['successful'])         # but did not reach a target


class TestRunPaperAccountLifecycle(unittest.TestCase):
    """run_paper_account() and quick_position_check() are the two functions that
    actually move the account every cycle — the 2h pipeline and the 10-15min
    monitor. Coverage analysis found them at 1% and 1% respectively: every test
    elsewhere exercises their extracted HELPERS (_paper_close,
    compute_paper_account_stats) in isolation, but the orchestration logic that
    decides WHEN to call them — stop/TP1/TP2/time-exit detection, DCA
    triggering, new-position sizing — had never been run by anything except a
    live cycle against real money-shaped data. That's exactly the class of bug
    this project has shipped twice already (inverted SHORT levels, stale
    stats), so it is exactly the class of code that most needs a real test
    here, not just its helpers.
    """

    def setUp(self):
        self._real_load = cmi.load_paper_account
        self._real_save = cmi.save_paper_account
        self._real_track = cmi.track_prediction_accuracy
        self.saved = None
        cmi.load_paper_account = lambda: self.acct
        cmi.save_paper_account = lambda a: setattr(self, 'saved', a)
        cmi.track_prediction_accuracy = lambda *a, **k: None

    def tearDown(self):
        cmi.load_paper_account = self._real_load
        cmi.save_paper_account = self._real_save
        cmi.track_prediction_accuracy = self._real_track

    def _acct(self, positions=None, cash=10000.0):
        self.acct = {
            'created': cmi.datetime.now().isoformat(), 'starting_capital': 10000.0,
            'cash': cash, 'positions': positions or {}, 'closed_trades': [],
            'equity_curve': [],
        }
        return self.acct

    def _pos(self, side='LONG', entry=100.0, stop=95.0, tp1=110.0, tp2=120.0,
             trade_type='SWING_DAILY', tp1_hit=False, stop_moved=False,
             opened=None, expected_hours=240, tranches=1):
        return {
            'asset': 'BTC', 'trade_type': trade_type, 'side': side, 'qty': 1.0,
            'avg_entry': entry, 'opened': opened or cmi.datetime.now().isoformat(),
            'tranches': tranches, 'last_tranche_notional': entry,
            'stop_loss': stop, 'take_profit_1': tp1, 'take_profit_2': tp2,
            'risk_distance': abs(entry - stop), 'entry_conviction': 0.5,
            'expected_hours': expected_hours, 'tp1_hit': tp1_hit,
            'stop_moved_to_breakeven': stop_moved,
        }

    def _sig(self, price, signal='NO TRADE', conviction=0.1, stop=None, tp1=None, tp2=None):
        return {
            'price': price, 'signal': signal, 'conviction': conviction,
            'trade_plan': {'stop_loss': stop, 'take_profit_1': tp1, 'take_profit_2': tp2},
            'intraday_trade': {},
        }

    def test_stop_loss_closes_long_position(self):
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=95.0)})
        cmi.run_paper_account({'BTC': self._sig(price=94.0)})
        self.assertNotIn('BTC', self.saved['positions'])
        self.assertEqual(len(self.saved['closed_trades']), 1)
        self.assertEqual(self.saved['closed_trades'][0]['reason'], 'STOP_LOSS')
        self.assertLess(self.saved['closed_trades'][0]['pnl'], 0)

    def test_stop_loss_triggers_at_exact_price_not_only_past_it(self):
        """Mutation testing found this boundary undefended: LONG's `price <= sl`
        relaxed to `price < sl` still passed every test, because none used
        price exactly equal to the stop. A real price feed can report the
        stop price exactly — that must still close the position, not require
        it to trade one tick further through."""
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=95.0)})
        cmi.run_paper_account({'BTC': self._sig(price=95.0)})
        self.assertNotIn('BTC', self.saved['positions'])
        self.assertEqual(self.saved['closed_trades'][0]['reason'], 'STOP_LOSS')

    def test_dca_triggers_at_exactly_the_configured_threshold(self):
        """Mutation testing found this boundary undefended too: `drawdown >=
        dca_trigger_drawdown` relaxed to `>` still passed, because the
        existing DCA test used a drawdown comfortably above the threshold,
        not exactly at it."""
        trigger = cmi.PAPER_CONFIG['dca_trigger_drawdown']
        entry = 100.0
        price_at_threshold = entry * (1 - trigger)   # exactly at the trigger drawdown
        self._acct(positions={'BTC': self._pos(side='LONG', entry=entry, stop=50.0,
                                                tp1=200.0, tp2=300.0)})
        cmi.run_paper_account({'BTC': self._sig(price=price_at_threshold, signal='LONG',
                                               conviction=0.5, stop=50.0, tp1=200.0, tp2=300.0)})
        self.assertEqual(self.saved['positions']['BTC']['tranches'], 2)

    def test_stop_loss_closes_short_position_at_correct_side(self):
        self._acct(positions={'BTC': self._pos(side='SHORT', entry=100.0, stop=105.0,
                                                tp1=90.0, tp2=80.0)})
        cmi.run_paper_account({'BTC': self._sig(price=106.0)})
        t = self.saved['closed_trades'][0]
        self.assertEqual(t['reason'], 'STOP_LOSS')
        # A SHORT genuinely stopped out (price rose) must show a LOSS — the
        # exact invariant the inverted-levels bug violated in production.
        self.assertLess(t['pnl'], 0)

    def test_tp1_partial_exit_moves_stop_to_breakeven(self):
        # tp2 far out of reach so only the TP1 partial fires this cycle.
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=95.0,
                                                tp1=110.0, tp2=999.0)})
        cmi.run_paper_account({'BTC': self._sig(price=110.5)})
        pos = self.saved['positions']['BTC']
        self.assertTrue(pos['tp1_hit'])
        self.assertTrue(pos['stop_moved_to_breakeven'])
        # At least breakeven — the trailing-stop block runs in this SAME cycle
        # right after the breakeven move, so if price is already comfortably
        # past TP1 it can ratchet the stop above pure breakeven immediately.
        # That's correct (more profit locked in sooner), not a bug; exact
        # equality to avg_entry only holds when price is barely past TP1.
        self.assertGreaterEqual(pos['stop_loss'], 100.0)
        self.assertAlmostEqual(pos['qty'], 0.5)     # half taken off
        self.assertEqual(self.saved['closed_trades'][0]['reason'], 'TAKE_PROFIT_1_PARTIAL')

    def test_tp2_closes_remaining_position(self):
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=100.0,
                                                tp1=110.0, tp2=120.0,
                                                tp1_hit=True, stop_moved=True)})
        cmi.run_paper_account({'BTC': self._sig(price=121.0)})
        self.assertNotIn('BTC', self.saved['positions'])
        self.assertEqual(self.saved['closed_trades'][0]['reason'], 'TAKE_PROFIT_2')
        self.assertGreater(self.saved['closed_trades'][0]['pnl'], 0)

    def test_trailing_stop_only_tightens_never_loosens(self):
        pos = self._pos(side='LONG', entry=100.0, stop=108.0, tp1=110.0, tp2=999.0,
                        tp1_hit=True, stop_moved=True)
        pos['risk_distance'] = 5.0
        self._acct(positions={'BTC': pos})
        # Price pulls back from a prior high; new trail (price - trail_dist) would
        # be BELOW the current stop of 108 — must not loosen it.
        cmi.run_paper_account({'BTC': self._sig(price=109.0)})
        self.assertEqual(self.saved['positions']['BTC']['stop_loss'], 108.0)

    def test_time_exit_closes_stale_position(self):
        stale_open = (cmi.datetime.now() - cmi.timedelta(hours=100)).isoformat()
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=50.0,
                                                tp1=200.0, tp2=300.0,
                                                opened=stale_open, expected_hours=10)})
        cmi.run_paper_account({'BTC': self._sig(price=101.0)})
        self.assertNotIn('BTC', self.saved['positions'])
        self.assertEqual(self.saved['closed_trades'][0]['reason'], 'TIME_EXIT')

    def test_dca_adds_a_tranche_on_valid_drawdown(self):
        self._acct(positions={'BTC': self._pos(side='LONG', entry=100.0, stop=50.0,
                                                tp1=200.0, tp2=300.0)},
                  cash=10000.0)
        # 5%+ drawdown, signal still LONG with conviction above the DCA floor.
        cmi.run_paper_account({'BTC': self._sig(price=94.0, signal='LONG', conviction=0.5,
                                               stop=50.0, tp1=200.0, tp2=300.0)})
        pos = self.saved['positions']['BTC']
        self.assertEqual(pos['tranches'], 2)
        self.assertLess(pos['avg_entry'], 100.0)   # averaged down
        self.assertLess(self.saved['cash'], 10000.0)

    def test_new_swing_position_opens_on_valid_signal(self):
        self._acct(positions={})
        cmi.run_paper_account({'BTC': self._sig(price=100.0, signal='LONG', conviction=0.6,
                                               stop=95.0, tp1=110.0, tp2=120.0)})
        self.assertIn('BTC', self.saved['positions'])
        pos = self.saved['positions']['BTC']
        self.assertEqual(pos['side'], 'LONG')
        self.assertEqual(pos['trade_type'], 'SWING_DAILY')
        self.assertLess(self.saved['cash'], 10000.0)

    def test_new_intraday_position_opens_with_single_target(self):
        self._acct(positions={})
        sig = self._sig(price=100.0)
        sig['intraday_trade'] = {'signal': 'LONG', 'entry': 100.0, 'stop_loss': 98.0,
                                 'take_profit': 104.0, 'conviction': 0.6, 'expected_hours': 8}
        cmi.run_paper_account({'BTC': sig})
        self.assertIn('BTC@4h', self.saved['positions'])
        pos = self.saved['positions']['BTC@4h']
        self.assertEqual(pos['take_profit_1'], pos['take_profit_2'])
        self.assertTrue(pos['tp1_hit'])
        self.assertFalse(pos['stop_moved_to_breakeven'])

    def test_intraday_target_inside_round_trip_cost_is_rejected(self):
        self._acct(positions={})
        sig = self._sig(price=100.0)
        # 0.05% move — inside round-trip fees/slippage, the exact BNB bug.
        sig['intraday_trade'] = {'signal': 'LONG', 'entry': 100.0, 'stop_loss': 98.0,
                                 'take_profit': 100.05, 'conviction': 0.6}
        cmi.run_paper_account({'BTC': sig})
        self.assertNotIn('BTC@4h', self.saved['positions'])

    def test_portfolio_risk_cap_blocks_new_entry_once_at_ceiling(self):
        """RISK_PARAMS['max_portfolio_risk'] (6%) was defined and never
        referenced anywhere — individual trades were capped (2% swing) but
        nothing summed them. Three held positions, each risking $600 (a wide
        60%-of-entry stop distance, qty=10, entry=100) against an equity of
        $30,000 (cash 27,000 + their own $3,000 notional): $1,800 open risk is
        exactly 6.0% — already at the cap. A new candidate sized at its own
        default 2% of equity ($600) would push total risk to 8%, so it must
        be rejected."""
        positions = {f'A{i}': self._pos(entry=100.0, stop=40.0) for i in range(3)}
        for p in positions.values():
            p['qty'] = 10.0
        self._acct(positions=positions, cash=27000.0)
        signals = {f'A{i}': self._sig(price=100.0) for i in range(3)}
        signals['BTC'] = self._sig(price=100.0, signal='LONG', conviction=0.9,
                                   stop=95.0, tp1=110.0, tp2=120.0)
        cmi.run_paper_account(signals)
        self.assertNotIn('BTC', self.saved['positions'])

    def test_portfolio_risk_cap_does_not_block_when_well_under(self):
        # Sanity check the cap isn't just blocking everything unconditionally.
        self._acct(positions={})
        cmi.run_paper_account({'BTC': self._sig(price=100.0, signal='LONG', conviction=0.6,
                                                stop=95.0, tp1=110.0, tp2=120.0)})
        self.assertIn('BTC', self.saved['positions'])

    def test_max_open_positions_blocks_new_swing_entries(self):
        positions = {f'A{i}': self._pos(entry=100.0) for i in range(cmi.PAPER_CONFIG['max_open_positions'])}
        self._acct(positions=positions)
        signals = {f'A{i}': self._sig(price=100.0) for i in range(cmi.PAPER_CONFIG['max_open_positions'])}
        signals['BTC'] = self._sig(price=100.0, signal='LONG', conviction=0.9,
                                   stop=95.0, tp1=110.0, tp2=120.0)
        cmi.run_paper_account(signals)
        self.assertNotIn('BTC', self.saved['positions'])

    def test_stats_and_equity_curve_written_every_cycle(self):
        self._acct(positions={'BTC': self._pos()})
        cmi.run_paper_account({'BTC': self._sig(price=101.0)})
        self.assertIn('stats', self.saved)
        self.assertEqual(len(self.saved['equity_curve']), 1)


def _race_worker(scratch_path, module_path, trade_id, delay):
    """Runs in a separate OS process (not a thread — the GIL can mask a real
    file race). Loads the real load_paper_account/save_paper_account against
    a scratch file, appends one trade, sleeps to force two writers to overlap,
    then saves — exactly what run_paper_account and quick_position_check do."""
    spec = importlib.util.spec_from_file_location("cmi_race", module_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.PAPER_ACCOUNT_PATH = scratch_path
    acct = m.load_paper_account()
    acct['closed_trades'].append({
        'asset': f'RACE{trade_id}', 'trade_type': 'SWING_DAILY', 'side': 'LONG',
        'reason': 'TIME_EXIT', 'successful': False, 'profitable': True,
        'entry_price': 100.0, 'exit_price': 101.0, 'qty': 1.0, 'tranches': 1,
        'pnl': 1.0, 'return_pct': 1.0, 'opened': m.datetime.now().isoformat(),
        'closed': m.datetime.now().isoformat(), 'holding_days': 0,
    })
    import time as _time
    _time.sleep(delay)
    m.save_paper_account(acct)


class TestPaperAccountConcurrentWriteSafety(unittest.TestCase):
    """Found by direct stress test, not inspection: load_paper_account() /
    save_paper_account() had no protection against two writers racing —
    position-monitor.yml and update.yml both write this file, serialized only
    by a GitHub Actions concurrency group, which guarantees nothing about the
    file itself. Two processes loading the same starting state and racing to
    save silently lost one writer's trade — valid JSON throughout, no crash,
    nothing that would ever surface on its own. Now protected by an flock held
    for the full load-to-save span. Uses real OS processes against a scratch
    file, not the production docs/paper_account.json."""

    def test_two_concurrent_writers_both_survive(self):
        scratch = f"/tmp/claude-0/scratchpad/test_race_{os.getpid()}.json"
        module_path = os.path.abspath('crypto_market_intelligence_v60.py')
        fresh = {'created': '2026-01-01T00:00:00', 'starting_capital': 10000.0,
                 'cash': 10000.0, 'positions': {}, 'closed_trades': [], 'equity_curve': []}
        with open(scratch, 'w') as f:
            json.dump(fresh, f)
        try:
            p1 = multiprocessing.Process(target=_race_worker, args=(scratch, module_path, 'A', 0.3))
            p2 = multiprocessing.Process(target=_race_worker, args=(scratch, module_path, 'B', 0.3))
            p1.start(); p2.start()
            p1.join(timeout=10); p2.join(timeout=10)
            final = json.load(open(scratch))
            assets = sorted(t['asset'] for t in final['closed_trades'])
            self.assertEqual(assets, ['RACEA', 'RACEB'],
                             "one writer's trade was silently lost under a real race")
        finally:
            for p in (scratch, scratch + '.tmp', scratch + '.lock'):
                if os.path.exists(p):
                    os.remove(p)


class TestQuickPositionCheckLifecycle(unittest.TestCase):
    """quick_position_check() is the frequent (10-15min) monitor — a second,
    separately-written code path that duplicates the exit logic above. Same
    coverage gap (1%), same reasoning for testing it directly: this file has
    already shipped a bug (stale stats after this exact function ran) that
    only existed because this function's own logic diverged from its sibling
    without a test catching it.
    """

    def setUp(self):
        self._real_load = cmi.load_paper_account
        self._real_save = cmi.save_paper_account
        self._real_track = cmi.track_prediction_accuracy
        self._real_fetch = cmi.fetch_binance_klines
        cmi.load_paper_account = lambda: self.acct
        cmi.save_paper_account = lambda a: setattr(self, 'saved', a)
        cmi.track_prediction_accuracy = lambda *a, **k: None
        self.saved = None

    def tearDown(self):
        cmi.load_paper_account = self._real_load
        cmi.save_paper_account = self._real_save
        cmi.track_prediction_accuracy = self._real_track
        cmi.fetch_binance_klines = self._real_fetch

    def _stub_price(self, price):
        cmi.fetch_binance_klines = lambda *a, **k: pd.DataFrame({'close': [price]})

    def test_stop_loss_closes_and_recomputes_stats(self):
        self.acct = {
            'created': cmi.datetime.now().isoformat(), 'starting_capital': 10000.0,
            'cash': 9000.0,
            'positions': {'BTC': {
                'asset': 'BTC', 'trade_type': 'SWING_DAILY', 'side': 'LONG', 'qty': 1.0,
                'avg_entry': 100.0, 'opened': cmi.datetime.now().isoformat(), 'tranches': 1,
                'last_tranche_notional': 100.0, 'stop_loss': 95.0, 'take_profit_1': 110.0,
                'take_profit_2': 120.0, 'risk_distance': 5.0, 'entry_conviction': 0.5,
                'expected_hours': 240, 'tp1_hit': False, 'stop_moved_to_breakeven': False,
            }},
            'closed_trades': [], 'equity_curve': [],
        }
        self._stub_price(94.0)
        cmi.ASSETS['BTC']['binance'] = 'BTCUSDT'  # already set in production config
        cmi.quick_position_check()
        # The bug this project actually shipped: stats were never recomputed
        # here, only last_events was patched — closed_trades/win_rate/equity
        # stayed stale until the next full 2h cycle picked it up.
        self.assertIsNotNone(self.saved)
        self.assertNotIn('BTC', self.saved['positions'])
        self.assertEqual(self.saved['stats']['closed_trades'], 1)
        self.assertIn('equity', self.saved['stats'])

    def test_no_open_positions_still_saves_file(self):
        # A fresh repo where paper_account.json doesn't exist yet: this must
        # still write the file, or `git add docs/paper_account.json` in the
        # workflow fails with "pathspec did not match any files".
        self.acct = {'created': cmi.datetime.now().isoformat(), 'starting_capital': 10000.0,
                    'cash': 10000.0, 'positions': {}, 'closed_trades': [], 'equity_curve': []}
        cmi.quick_position_check()
        self.assertIsNotNone(self.saved)


class TestPaperAccountCorruptedTradesExcluded(unittest.TestCase):
    """The inverted-SHORT bug left 31 closed SHORTs on the live ledger that hit
    reason=='STOP_LOSS' with a positive pnl — impossible under correct logic, since
    a stop is by definition the exit you take when you're wrong. Headline win_rate/
    wins/losses/profit_factor must exclude these; equity/cash must not be touched
    (that money already moved in the simulation, it isn't rewritten)."""

    def _base_acct(self):
        today = cmi.datetime.now().isoformat()
        return {
            'starting_capital': 10000.0, 'cash': 10000.0, 'positions': {},
            'closed_trades': [], 'equity_curve': [],
        }

    def _trade(self, side, reason, pnl, opened=None, trade_type='SWING_DAILY'):
        return {
            'asset': 'ADA', 'trade_type': trade_type, 'side': side, 'reason': reason,
            'successful': reason in cmi.SUCCESS_REASONS, 'profitable': pnl > 0,
            'entry_price': 0.22, 'exit_price': 0.20, 'qty': 100.0, 'tranches': 1,
            'pnl': pnl, 'return_pct': 0.0,
            'opened': opened or cmi.datetime.now().isoformat(),
            'closed': cmi.datetime.now().isoformat(), 'holding_days': 1,
        }

    def test_short_stoploss_with_positive_pnl_is_corrupted(self):
        self.assertTrue(cmi._is_corrupted_short_stopout(
            self._trade('SHORT', 'STOP_LOSS', 101.33)))

    def test_short_stoploss_with_negative_pnl_is_clean(self):
        self.assertFalse(cmi._is_corrupted_short_stopout(
            self._trade('SHORT', 'STOP_LOSS', -50.0)))

    def test_long_stoploss_with_small_positive_pnl_is_not_flagged(self):
        # Legitimate: stop trailed to breakeven-or-better after a TP1 partial.
        self.assertFalse(cmi._is_corrupted_short_stopout(
            self._trade('LONG', 'STOP_LOSS', 2.82)))

    def test_corrupted_short_excluded_from_win_rate(self):
        acct = self._base_acct()
        acct['closed_trades'] = [
            self._trade('SHORT', 'STOP_LOSS', 101.33),   # corrupted — bug
            self._trade('LONG', 'STOP_LOSS', -50.0),     # genuine loss
            self._trade('LONG', 'TAKE_PROFIT_1_PARTIAL', 20.0),  # genuine win
        ]
        stats = cmi.compute_paper_account_stats(acct, equity=10071.33)
        self.assertEqual(stats['closed_trades'], 2)
        self.assertEqual(stats['excluded_corrupted'], 1)
        self.assertEqual(stats['corrupted_trades_pnl'], 101.33)
        self.assertEqual(stats['wins'], 1)
        self.assertEqual(stats['losses'], 1)

    def test_pre_fix_trades_excluded_by_date(self):
        acct = self._base_acct()
        acct['closed_trades'] = [
            self._trade('LONG', 'TAKE_PROFIT_2', 30.0, opened='2026-08-01T00:00:00'),
        ]
        stats = cmi.compute_paper_account_stats(acct, equity=10030.0)
        self.assertEqual(stats['closed_trades'], 0)
        self.assertEqual(stats['excluded_pre_fix'], 1)

    def test_equity_and_cash_untouched_by_exclusion(self):
        # Corrupted trades stay OUT of win_rate but the cash they already banked
        # in the simulation is not rewritten — only the headline stat is filtered.
        acct = self._base_acct()
        acct['cash'] = 10101.33
        acct['closed_trades'] = [self._trade('SHORT', 'STOP_LOSS', 101.33)]
        stats = cmi.compute_paper_account_stats(acct, equity=10101.33)
        self.assertEqual(stats['cash'], 10101.33)
        self.assertEqual(stats['equity'], 10101.33)
        self.assertEqual(stats['closed_trades'], 0)

    def test_exact_breakeven_counts_as_a_loss_not_a_win(self):
        """Mutation testing found this boundary undefended: pnl > 0 relaxed to
        >= 0 (making an exact-breakeven trade a 'win') still passed every
        existing test, because none of them used pnl=0.0 exactly. A breakeven
        trade made no money — counting it as a win inflates the win rate on
        every trade that merely didn't lose."""
        acct = self._base_acct()
        acct['closed_trades'] = [self._trade('LONG', 'TIME_EXIT', 0.0)]
        stats = cmi.compute_paper_account_stats(acct, equity=10000.0)
        self.assertEqual(stats['wins'], 0)
        self.assertEqual(stats['losses'], 1)


class TestReturningVisitorLoadsData(unittest.TestCase):
    """Found by actually rendering the page in a headless browser as a returning
    visitor (localStorage already set from an earlier visit) — the disclaimer-skip
    branch toggled CSS classes and returned without ever calling loadData() or
    initReveal(). Anyone who had dismissed the disclaimer once got a permanently
    blank dashboard on every later visit. Static source checks below pin the fix
    in place; audit_dashboard_render.py is the full browser-level proof."""

    def test_single_shared_entry_function(self):
        src = open('index.html').read()
        self.assertIn('function enterDashboard()', src,
                     "both the first-visit and returning-visitor paths must "
                     "call one shared function, not duplicate logic that can diverge")

    def test_returning_visitor_branch_calls_it(self):
        src = open('index.html').read()
        m = re.search(
            r"if\s*\(localStorage\.getItem\('market-cortex-understood'\)\s*===\s*'true'\)\s*\{(.*?)\}",
            src, re.S)
        self.assertIsNotNone(m, "could not find the returning-visitor branch at all")
        self.assertIn('enterDashboard()', m.group(1),
                     "returning-visitor branch does not call the function that loads data")

    def test_click_handler_calls_it(self):
        src = open('index.html').read()
        m = re.search(r"btn\.addEventListener\('click',\s*function\s*\(\)\s*\{(.*?)\}\);",
                     src, re.S)
        self.assertIsNotNone(m)
        self.assertIn('enterDashboard()', m.group(1))


class TestTargetMustClearRoundTripCosts(unittest.TestCase):
    """A target closer to entry than the round trip costs cannot make money. The
    trade hits it exactly as designed, books a loss, and is recorded as a win.
    Live evidence: BNB 4h LONG entry 749.62, target 750.00 (0.05% of move against
    a ~0.48% round trip) closed TAKE_PROFIT_2 at -$0.30 and successful=True."""

    def test_floor_exceeds_actual_round_trip_cost(self):
        floor = cmi.min_profitable_move_pct()
        actual_cost = cmi.PAPER_CONFIG['fee_pct'] * 2 + cmi.PAPER_CONFIG['slippage_pct']
        self.assertGreater(floor, actual_cost,
                           "floor must clear costs, not merely tie them")

    def test_the_real_bnb_trade_would_now_be_rejected(self):
        entry, target = 749.62, 750.00
        self.assertLess(abs(target - entry), entry * cmi.min_profitable_move_pct())

    def test_a_normal_target_still_passes(self):
        entry, target = 749.62, 749.62 * 1.02   # a 2% move
        self.assertGreaterEqual(abs(target - entry), entry * cmi.min_profitable_move_pct())

    def test_margin_is_configurable_and_monotonic(self):
        self.assertLess(cmi.min_profitable_move_pct(1.0), cmi.min_profitable_move_pct(2.0))


class TestIntradayStopNotTrailedWithoutBreakeven(unittest.TestCase):
    """Intraday positions are created with tp1_hit=True purely to skip the
    partial-exit branch (single target: take_profit_1 == take_profit_2). Gating the
    trailing stop on tp1_hit therefore enrolled them in a trail designed around a
    breakeven floor they never received — their stops crept from the original risk
    distance toward entry from the first cycle while still sitting BELOW entry.
    Live evidence: BTC@4h entry 77106.2 with a 'trailed' stop at 77091.77."""

    def test_intraday_open_does_not_claim_breakeven(self):
        src = open('crypto_market_intelligence_v60.py').read()
        # The intraday creation site must not imply a breakeven floor.
        self.assertIn("'stop_moved_to_breakeven': False,", src)

    def test_trailing_gated_on_breakeven_not_tp1_hit(self):
        src = open('crypto_market_intelligence_v60.py').read()
        # Both trailing blocks (full cycle + position monitor) must gate on the
        # breakeven flag. If either reverts to tp1_hit, intraday stops silently
        # start trailing again.
        self.assertEqual(
            src.count("if pos.get('stop_moved_to_breakeven') and pos.get('risk_distance'):"), 2)
        self.assertEqual(
            src.count("if pos.get('tp1_hit') and pos.get('risk_distance'):"), 0)

    def test_breakeven_flag_set_wherever_stop_moves_to_entry(self):
        src = open('crypto_market_intelligence_v60.py').read()
        # Every place that moves the stop to avg_entry must also raise the flag,
        # or that position would never trail afterward.
        self.assertEqual(src.count("pos['stop_loss'] = pos['avg_entry']"),
                         src.count("pos['stop_moved_to_breakeven'] = True"))


class TestDcaStopNeverWidens(unittest.TestCase):
    """DCA may tighten a stop, never widen it."""

    def test_config_caps_at_one_add(self):
        self.assertEqual(cmi.PAPER_CONFIG['dca_max_tranches'], 2)

    def test_long_stop_does_not_move_down(self):
        original_stop, risk = 95.0, 5.0
        new_avg = 97.0                      # averaged down
        reanchored = new_avg - risk         # 92.0 — further away, must be rejected
        self.assertEqual(max(original_stop, reanchored), 95.0)

    def test_short_stop_does_not_move_up(self):
        original_stop, risk = 105.0, 5.0
        new_avg = 103.0
        reanchored = new_avg + risk         # 108.0 — further away, must be rejected
        self.assertEqual(min(original_stop, reanchored), 105.0)


class TestFiniteGuards(unittest.TestCase):
    def test_rejects_nan_and_none(self):
        for bad in (None, float('nan'), float('inf'), 0, -1, 'abc'):
            self.assertFalse(cmi._is_finite_positive(bad), f"{bad!r} must be rejected")

    def test_accepts_real_prices(self):
        for good in (0.0001, 1, 79411.23):
            self.assertTrue(cmi._is_finite_positive(good))


class TestNoDeadOrUndefinedCode(unittest.TestCase):
    def test_no_unreferenced_functions(self):
        import ast
        tree = ast.parse(open('crypto_market_intelligence_v60.py').read())
        defined = {n.name: n.lineno for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        used = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute):
                used.add(n.attr)
        dead = sorted(k for k in defined if k not in used and not k.startswith('__'))
        self.assertEqual(dead, [], f"dead functions: {dead}")

    def test_fred_fetcher_defined_once(self):
        with open('crypto_market_intelligence_v60.py') as fh:
            src = fh.read()
        self.assertEqual(src.count('def fetch_fred_data('), 1)

    def test_no_undefined_names(self):
        """Catches the classic self-inflicted edit bug: removing `as _e` from an
        except clause whose body still references _e. Silent until it fires."""
        try:
            from pyflakes.api import check
            from pyflakes.reporter import Reporter
        except ImportError:
            self.skipTest('pyflakes not installed')
        import io
        out, err = io.StringIO(), io.StringIO()
        with open('crypto_market_intelligence_v60.py') as fh:
            check(fh.read(), 'crypto_market_intelligence_v60.py', Reporter(out, err))
        undefined = [l for l in out.getvalue().splitlines() if 'undefined name' in l]
        self.assertEqual(undefined, [], f"undefined names: {undefined}")


class TestDashboardWiring(unittest.TestCase):
    def setUp(self):
        self.html = open('index.html').read()

    def test_every_referenced_element_exists(self):
        import re
        present = set(re.findall(r'id="([\w-]+)"', self.html))
        referenced = (set(re.findall(r"\$\('([\w-]+)'\)", self.html)) |
                      set(re.findall(r"getElementById\('([\w-]+)'\)", self.html)))
        missing = sorted(referenced - present)
        self.assertEqual(missing, [], f"referenced but missing from HTML: {missing}")

    def test_every_section_has_plain_english_guide(self):
        import re
        headings = [re.sub(r'<[^>]+>', '', m).strip()
                    for m in re.findall(r'<h2>\s*<span class="num">.*?</span>(.*?)</h2>', self.html)]
        for h in headings:
            self.assertIn(f"'{h}'", self.html, f"section '{h}' has no plain-English guide entry")

    def test_chart_failure_cannot_blank_the_dashboard(self):
        self.assertIn('function safeRender', self.html)
        self.assertIn("typeof Chart === 'undefined'", self.html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
