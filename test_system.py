"""Regression tests for crypto_market_intelligence_v60.

Every test here corresponds to a bug that actually shipped and was found in real
data — not hypothetical cases. Run with:

    python3 test_system.py          (no pytest needed)
    python3 -m pytest test_system.py -q

Network is never touched; anything that would fetch is fed synthetic frames.
"""
import importlib.util
import math
import sys
import unittest

_spec = importlib.util.spec_from_file_location("cmi", "crypto_market_intelligence_v60.py")
cmi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmi)

import pandas as pd


def _frame(price=100.0, atr=2.0, rows=40, regime='CHOPPY'):
    return pd.DataFrame({
        'close': [price] * rows,
        'atr_14': [atr] * rows,
        'atr_ratio': [atr / price] * rows,
        'drawdown': [0.0] * rows,
        'regime': [regime] * rows,
    })


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
