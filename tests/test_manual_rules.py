import sys
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.manual_rules import Annual, QualityEvidence, quality_reasons, entry_decision, exit_decision


DAY = date(2026, 9, 28)


def quality():
    return QualityEvidence('SYNTHETIC_TEST_ONLY', DAY, True, True, date(2020, 1, 1),
                           False, True, False,
                           tuple(Annual(y, date(y + 1, 4, 1), 100., 100., 12.) for y in (2023, 2024, 2025)),
                           100., date(2026, 8, 1), 20000000.)


def rows_for(mechanism):
    closes = [100.] * 799 + [75.] if mechanism == 'L1' else [90.] * 740 + [100.] * 59 + [110.]
    return [dict(date=(DAY-timedelta(days=799-i)).isoformat(), close=str(close),
                 peTTM='5' if i == 799 else '10', pbMRQ='1', tradestatus='1', isST='0')
            for i, close in enumerate(closes)]


class ManualRulesTests(unittest.TestCase):
    def test_quality_passes_only_complete_evidence(self):
        self.assertEqual(quality_reasons(quality(), DAY), ())
        for field in ('mainboard', 'nonfinancial', 'is_st', 'standard_audit', 'material_risk'):
            for value in (None, 1, 'false'):
                self.assertTrue(quality_reasons(replace(quality(), **{field: value}), DAY))

    def test_future_and_old_evidence(self):
        self.assertIn('MISSING_CURRENT_EVIDENCE', quality_reasons(replace(quality(), checked_on=DAY-timedelta(days=1)), DAY))
        annuals = (*quality().annuals[:2], replace(quality().annuals[2], available_on=DAY+timedelta(days=1)))
        self.assertIn('ANNUAL_AVAILABILITY_INVALID', quality_reasons(replace(quality(), annuals=annuals), DAY))
        self.assertIn('ANNUAL_WINDOW_INVALID', quality_reasons(replace(quality(), annuals=quality().annuals[:2]), DAY))

    def test_no_missing_zero_or_proxy_substitution(self):
        for value in (None, float('nan'), float('inf'), False):
            annuals = (replace(quality().annuals[0], operating_cashflow=value), *quality().annuals[1:])
            self.assertIn('MISSING_TOTAL_FINANCIAL_VALUES', quality_reasons(replace(quality(), annuals=annuals), DAY))
        annuals = tuple(replace(a, operating_cashflow=79.) for a in quality().annuals)
        self.assertIn('CASH_PROFIT_RATIO_FAILED', quality_reasons(replace(quality(), annuals=annuals), DAY))

    def test_left_and_right_signals_and_missing_quality(self):
        for mechanism in ('L1', 'R1'):
            rows = rows_for(mechanism)
            args = dict(evidence=quality(), rows=rows, calendar=[r['date'] for r in rows], signal_day=DAY)
            result = entry_decision(**args)
            self.assertEqual(result['action'], 'BUY_SIGNAL')
            self.assertEqual(result['mechanism'], mechanism)
            args['evidence'] = replace(quality(), standard_audit=None)
            self.assertEqual(entry_decision(**args)['action'], 'BLOCKED')

    def args(self):
        return dict(mechanism='L1', hard_risk=False, quality_valid=True,
                    adjusted_total_return=.05, held_trading_days=20, pe_percentile=.3,
                    entry_ttm_profit=100., current_ttm_profit=100.)

    def test_future_market_day_rejected(self):
        with self.assertRaises(ValueError):
            entry_decision(evidence=quality(), rows=[], calendar=[], signal_day=DAY,
                           market_day=DAY+timedelta(days=1))

    def test_stock_identity_mismatch_rejected(self):
        rows = [dict(r, code='sh.600674') for r in rows_for('R1')]
        with self.assertRaises(ValueError):
            entry_decision(evidence=replace(quality(), stock_code='600886'), rows=rows,
                           calendar=[r['date'] for r in rows], signal_day=DAY)

    def test_previous_session_data_keeps_actual_signal_day(self):
        rows = rows_for('R1')
        signal_day = DAY+timedelta(days=1)
        result = entry_decision(evidence=replace(quality(), checked_on=signal_day),
                                rows=rows, calendar=[r['date'] for r in rows],
                                signal_day=signal_day, market_day=DAY)
        self.assertEqual(result['action'], 'BUY_SIGNAL')
        self.assertEqual(result['signal_day'], signal_day.isoformat())
        self.assertEqual(result['metrics']['cutoff_date'], DAY.isoformat())

    def test_exit_priorities_ignore_irrelevant_missing_values(self):
        for changes, reason in [({'hard_risk': True}, 'RISK_OR_QUALITY_EXIT'),
                                ({'adjusted_total_return': -.12}, 'STOP_LOSS'),
                                ({'held_trading_days': 126}, 'TIME_EXIT')]:
            args = self.args()
            args.update(pe_percentile=None, current_ttm_profit=None)
            args.update(changes)
            self.assertEqual(exit_decision(**args), ('SELL_SIGNAL', reason))

    def test_pe_rise_not_automatically_profit(self):
        args = self.args()
        args.update(pe_percentile=.6, current_ttm_profit=80.)
        self.assertEqual(exit_decision(**args)[0], 'REVIEW')
        args['current_ttm_profit'] = 100.
        self.assertEqual(exit_decision(**args), ('SELL_SIGNAL', 'VALUATION_RECOVERY_EXIT'))
        args['adjusted_total_return'] = -.01
        self.assertEqual(exit_decision(**args)[0], 'REVIEW')

    def test_right_requires_two_complete_weeks(self):
        args = self.args()
        args.update(mechanism='R1', consecutive_weekly_below_ma60=1)
        self.assertEqual(exit_decision(**args)[0], 'HOLD')
        args['consecutive_weekly_below_ma60'] = 2
        self.assertEqual(exit_decision(**args)[0], 'SELL_SIGNAL')
        args['consecutive_weekly_below_ma60'] = None
        self.assertEqual(exit_decision(**args)[0], 'REVIEW')

    def test_unknown_risk_not_safe_hold(self):
        args = self.args()
        args['hard_risk'] = None
        self.assertEqual(exit_decision(**args)[0], 'REVIEW')


if __name__ == '__main__':
    unittest.main()
