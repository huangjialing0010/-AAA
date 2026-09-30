import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill
from ashare_lab.selection.cash_dividend_account import CashDividendAccount
from ashare_lab.selection.order_gate import ExecutionRules
from ashare_lab.selection.checked_execution import apply_checked_execution
from ashare_lab.selection.account_reconciliation import reconcile
from ashare_lab.selection.fees import FeeRuleProfile


class CheckedExecutionTests(unittest.TestCase):
    def setup_inputs(self, cash='2000'):
        account = CashDividendAccount(ShareAccount(D(cash)))
        fill = Fill('buy', '600036', 'BUY', date(2025, 7, 10), 0, 100, D(10), D(5), date(2025, 7, 11))
        args = dict(signal_day=date(2025, 7, 9), tradable=True, execution_evidence_id='SYNTHETIC',
                    rules=ExecutionRules(fill.day, fill.code, 'SYNTHETIC', 100, 100, D(9), D(11),
                                         'ROUND100_WITH_WHOLE_REMAINDER'))
        return account, fill, args

    def test_buy_sell_replay_reconciles(self):
        account, fill, args = self.setup_inputs()
        account, decision, _ = apply_checked_execution(account, fill, **args)
        self.assertEqual(decision.status, 'BOOKED')
        sell = Fill('sell', '600036', 'SELL', date(2025, 7, 11), 0, 100, D(10), D(5))
        args.update(signal_day=date(2025, 7, 10), rules=replace(args['rules'], day=sell.day))
        account, decision, sales = apply_checked_execution(account, sell, **args)
        self.assertEqual(decision.status, 'BOOKED')
        self.assertEqual(len(sales), 1)
        repeated, decision, _ = apply_checked_execution(account, sell, **args)
        self.assertEqual(decision.status, 'ALREADY_BOOKED')
        self.assertEqual(repeated, account)
        self.assertEqual(account.shares.cash, D(1990))
        self.assertEqual(reconcile(account, D(2000))['status'], 'BALANCES_MATCH')

    def test_rejected_does_not_change_account(self):
        for condition in ('suspended', 'limit', 'quantity', 'cash', 'missing_evidence'):
            account, fill, args = self.setup_inputs('1000' if condition == 'cash' else '2000')
            if condition == 'suspended': args['tradable'] = False
            if condition == 'limit': fill = replace(fill, price=D(11))
            if condition == 'quantity': fill = replace(fill, shares=101)
            if condition == 'missing_evidence': args['execution_evidence_id'] = ''
            updated, decision, sales = apply_checked_execution(account, fill, **args)
            self.assertEqual(decision.status, 'REJECTED', condition)
            self.assertEqual(updated, account)
            self.assertEqual(sales, ())

    def test_fee_profile_mismatch_rejected(self):
        account, fill, args = self.setup_inputs()
        profile = FeeRuleProfile('TEST', date(2025, 1, 1), D('0.0003'), D('5'), D('0.0005'))
        fill = replace(fill, fees=D('4.99'))
        updated, decision, sales = apply_checked_execution(account, fill, fee_profile=profile, **args)
        self.assertEqual(decision.reason, 'FEE_MISMATCH')
        self.assertEqual(updated, account)
        self.assertEqual(sales, ())

    def test_fee_profile_matching_can_book(self):
        account, fill, args = self.setup_inputs()
        profile = FeeRuleProfile('TEST', date(2025, 1, 1), D('0.0003'), D('5'), D('0.0005'))
        fill = replace(fill, fees=D('5.00'))
        updated, decision, _ = apply_checked_execution(account, fill, fee_profile=profile, **args)
        self.assertEqual(decision.status, 'BOOKED')
