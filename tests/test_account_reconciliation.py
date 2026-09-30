import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill
from ashare_lab.selection.cash_dividend_account import CashDividendAccount, apply_fill, apply_dividends
from ashare_lab.selection.dividend_ledger import DividendEntitlement
from ashare_lab.selection.account_reconciliation import reconcile


class ReconciliationTests(unittest.TestCase):
    def account(self):
        account, _ = apply_fill(CashDividendAccount(ShareAccount(D(2000))),
            Fill('b', '600036', 'BUY', date(2025, 7, 9), 0, 100, D(10), D(5), date(2025, 7, 10)))
        event = DividendEntitlement('e', '600036', date(2025, 7, 10), date(2025, 7, 11),
                                    date(2025, 7, 11), 100, D(2), D(2), 'SYNTHETIC')
        return apply_dividends(account, date(2025, 7, 11), [event])[0]

    def test_balances_match(self):
        result = reconcile(self.account(), D(2000))
        self.assertEqual(result['status'], 'BALANCES_MATCH')
        self.assertEqual(result['expected_cash'], D(1195))

    def test_cash_tampering_detected(self):
        account = self.account()
        bad = replace(account, shares=replace(account.shares, cash=D(1200)))
        self.assertIn('CASH_MISMATCH', reconcile(bad, D(2000))['errors'])

    def test_share_tampering_detected(self):
        account = self.account()
        bad = replace(account, shares=replace(account.shares, lots=()))
        self.assertIn('SHARES_MISMATCH', reconcile(bad, D(2000))['errors'])

    def test_dividend_tampering_detected(self):
        account = self.account()
        bad = replace(account, dividends=replace(account.dividends, cash_received=D(400)))
        self.assertIn('DIVIDEND_CASH_MISMATCH', reconcile(bad, D(2000))['errors'])
