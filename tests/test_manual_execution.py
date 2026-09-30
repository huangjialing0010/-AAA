import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.manual_execution import execute_signal
from ashare_lab.selection.manual_rules import exit_decision
from ashare_lab.selection.share_account import ShareAccount
from ashare_lab.selection.cash_dividend_account import CashDividendAccount
from ashare_lab.selection.fees import FeeRuleProfile
from ashare_lab.selection.order_gate import ExecutionRules
from ashare_lab.selection.account_reconciliation import reconcile


class ManualExecutionTests(unittest.TestCase):
    def args(self):
        return dict(account=CashDividendAccount(ShareAccount(D(100000))),
                    action='BUY_SIGNAL', mechanism='L1', code='600001', order_id='SYNTHETIC_BUY',
                    signal_day=date(2026, 9, 25), execution_day=date(2026, 9, 28),
                    next_trade_day={date(2026, 9, 25): date(2026, 9, 28), date(2026, 9, 28): date(2026, 9, 29),
                                    date(2026, 9, 29): date(2026, 9, 30)},
                    rules=ExecutionRules(date(2026, 9, 28), '600001', 'SYNTHETIC', 100, 100, D(9), D(11),
                                         'ROUND100_WITH_WHOLE_REMAINDER'),
                    tradable=True, open_price=D(10), low_price=D('9.5'), high_price=D('10.5'),
                    slippage_rate=D('.0005'),
                    fees=FeeRuleProfile('SYNTHETIC', date(2026, 1, 1), D('.0003'), D(5), D('.0005')),
                    single_budget=D(10000), portfolio_budget=D(40000), portfolio_check_passed=True,
                    corporate_actions_verified=True, signal_evidence_id='SYNTHETIC', execution_evidence_id='SYNTHETIC')

    def test_buy_risk_exit_and_independent_balance(self):
        args = self.args()
        account, decision, _ = execute_signal(**args)
        self.assertEqual(decision.status, 'BOOKED')
        self.assertEqual(account.shares.fills[0].price, D('10.01'))
        self.assertEqual(account.shares.lots[0].shares, 900)
        self.assertEqual(account.shares.cash, D(90986))
        action, reason = exit_decision(mechanism='L1', hard_risk=True, quality_valid=True,
                                      adjusted_total_return=None, held_trading_days=1)
        self.assertEqual(reason, 'RISK_OR_QUALITY_EXIT')
        args.update(account=account, action=action, order_id='SYNTHETIC_SELL',
                    signal_day=date(2026, 9, 28), execution_day=date(2026, 9, 29),
                    rules=replace(args['rules'], day=date(2026, 9, 29)), portfolio_check_passed=False)
        closed, decision, _ = execute_signal(**args)
        self.assertEqual(decision.status, 'BOOKED')
        self.assertFalse(closed.shares.lots)
        self.assertEqual(closed.shares.cash, D('99967.50'))
        self.assertEqual(reconcile(closed, D(100000))['status'], 'BALANCES_MATCH')
        args['account'] = closed
        repeated, decision, _ = execute_signal(**args)
        self.assertEqual(decision.reason, 'ORDER_ID_ALREADY_USED')
        self.assertEqual(repeated, closed)

    def test_rejections_preserve_account(self):
        for changes, reason in [({'corporate_actions_verified': None}, 'CORPORATE_ACTIONS_NOT_VERIFIED'),
                                ({'portfolio_check_passed': False}, 'PORTFOLIO_NOT_ADMITTED'),
                                ({'tradable': False}, 'NOT_CONFIRMED_TRADABLE'),
                                ({'signal_day': date(2026, 9, 28)}, 'ORDER_NOT_NEXT_TRADING_DAY'),
                                ({'single_budget': D(100)}, 'BUDGET_BELOW_MINIMUM_LOT'),
                                ({'high_price': D(10)}, 'SLIPPED_PRICE_OUTSIDE_BAR')]:
            args = self.args()
            args.update(changes)
            account, decision, _ = execute_signal(**args)
            self.assertEqual(account, args['account'])
            self.assertEqual(decision.reason, reason)

    def test_upper_limit_and_missing_execution_evidence(self):
        args = self.args()
        args.update(rules=replace(args['rules'], upper_price=D('10.01')))
        self.assertEqual(execute_signal(**args)[1].reason, 'BUY_AT_UPPER_LIMIT')
        args = self.args()
        args['execution_evidence_id'] = ''
        self.assertEqual(execute_signal(**args)[1].reason, 'MISSING_EXECUTION_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
