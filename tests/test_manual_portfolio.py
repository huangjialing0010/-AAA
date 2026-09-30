import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from dataclasses import replace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.manual_portfolio import PositionValue, plan_openings
from ashare_lab.selection.manual_rules import VERSION
from ashare_lab.selection.manual_execution import execute_signal
from ashare_lab.selection.account_reconciliation import reconcile
import test_manual_execution as execution_fixture


class PortfolioTests(unittest.TestCase):
    def args(self):
        return dict(signals=[dict(code=f'60000{i}', industry=str(i), industry_as_of='2026-09-29',
                                 decision=dict(action='BUY_SIGNAL', version=VERSION, signal_day='2026-09-29',
                                               evidence_id='SYNTHETIC', mechanism='L1' if i % 2 else 'R1',
                                               roe_3y_median=20-i, cash_profit_ratio=1.)) for i in (3, 2, 1)],
                    positions=[], cash=D(100000), as_of=date(2026, 9, 29), monthly_buys=0,
                    pending_buy_count=0, decision_window_verified=True)

    def test_rank_monthly_limit_and_reservation(self):
        result = plan_openings(**self.args())
        self.assertEqual([r['code'] for r in result['plans']], ['600001', '600002'])
        self.assertEqual(result['reserved_budget'], '20000.00')
        self.assertEqual(result['unreserved_cash'], '80000.00')
        self.assertEqual(result['rejections'][0]['reason'], 'MONTHLY_BUY_LIMIT')

    def test_industry_and_existing_positions(self):
        args = self.args()
        args['positions'] = [PositionValue('600001', '1', D(10000))]
        args['signals'][0]['industry'] = '1'
        result = plan_openings(**args)
        self.assertEqual([p['code'] for p in result['plans']], ['600002'])
        self.assertEqual({r['reason'] for r in result['rejections']}, {'ALREADY_HELD_ADD_NOT_IMPLEMENTED', 'INDUSTRY_LIMIT'})

    def test_position_and_total_exposure_limits(self):
        args = self.args()
        args['positions'] = [PositionValue('000001', 'A', D(70000)), PositionValue('000002', 'B', D(10000)),
                             PositionValue('000003', 'C', D(10000))]
        args['cash'] = D(10000)
        result = plan_openings(**args)
        self.assertFalse(result['plans'])
        self.assertEqual({r['reason'] for r in result['rejections']}, {'NO_AVAILABLE_BUDGET'})
        args['positions'] = [PositionValue(f'00000{i}', str(i+10), D(1000)) for i in range(4)]
        self.assertEqual({r['reason'] for r in plan_openings(**args)['rejections']}, {'POSITION_LIMIT'})

    def test_pending_or_off_cycle_blocks_all(self):
        for field, value in [('pending_buy_count', 1), ('decision_window_verified', False)]:
            args = self.args()
            args[field] = value
            self.assertFalse(plan_openings(**args)['plans'])

    def test_stale_and_missing_rank_evidence(self):
        args = self.args()
        for s in args['signals']:
            s['decision']['signal_day'] = '2026-09-28'
        self.assertFalse(plan_openings(**args)['plans'])
        args = self.args()
        for s in args['signals']:
            s['decision']['cash_profit_ratio'] = None
        self.assertFalse(plan_openings(**args)['plans'])

    def test_duplicate_rejected_and_deterministic_rerun(self):
        args = self.args()
        self.assertEqual(plan_openings(**args), plan_openings(**args))
        args['signals'].append(args['signals'][0])
        with self.assertRaises(ValueError):
            plan_openings(**args)

    def test_plan_to_two_fills_reconciles(self):
        args = self.args()
        args['as_of'] = date(2026, 9, 25)
        for row in args['signals']:
            row['industry_as_of'] = args['as_of'].isoformat()
            row['decision']['signal_day'] = args['as_of'].isoformat()
        plan = plan_openings(**args)
        execution = execution_fixture.ManualExecutionTests().args()
        account = execution['account']
        for item in plan['plans']:
            execution.update(account=account, order_id=item['plan_id'], code=item['code'],
                             mechanism=item['mechanism'], single_budget=D(item['budget']),
                             portfolio_budget=D(item['budget']),
                             rules=replace(execution['rules'], code=item['code']))
            account, decision, _ = execute_signal(**execution)
            self.assertEqual(decision.status, 'BOOKED')
        self.assertEqual(len(account.shares.fills), 2)
        self.assertEqual(len(account.shares.lots), 2)
        self.assertEqual(account.shares.cash, D(81972))
        self.assertLessEqual(D(100000)-account.shares.cash, D(plan['reserved_budget']))
        self.assertEqual(reconcile(account, D(100000))['status'], 'BALANCES_MATCH')
