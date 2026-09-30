import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill
from ashare_lab.selection.dividend_registration import register_dividend
from ashare_lab.selection.cash_dividend_account import CashDividendAccount, apply_fill, apply_dividends, valuation


class CashDividendIntegrationTests(unittest.TestCase):
    def setup_account(self):
        account = CashDividendAccount(ShareAccount(D(1000)))
        account, _ = apply_fill(account, Fill('buy', '600036', 'BUY', date(2025, 7, 9),
            0, 100, D(10), D(0), date(2025, 7, 10)))
        registered = register_dividend(event_id='synthetic', code='600036',
            register_date=date(2025, 7, 10), ex_date=date(2025, 7, 11), pay_date=date(2025, 7, 11),
            gross_per_share=D(2), payment_per_share=D(2), evidence_id='SYNTHETIC',
            snapshot_date=date(2025, 7, 10), close_snapshot_id='close', lots=account.shares.lots)
        return account, registered.entitlement

    def test_payment_funds_purchase_without_double_counting(self):
        account, event = self.setup_account()
        account, _ = apply_dividends(account, date(2025, 7, 11), [event])
        self.assertEqual(account.shares.cash, D(200))
        account, _ = apply_fill(account, Fill('reinvest', '600036', 'BUY', date(2025, 7, 11),
            0, 20, D(10), D(0), date(2025, 7, 14)))
        replay, audit = apply_dividends(account, date(2025, 7, 11), [event])
        self.assertEqual(replay, account)
        self.assertEqual(audit, [])
        self.assertEqual(account.shares.cash, D(0))
        self.assertEqual(valuation(account, {'600036': D(10)})['equity_before_unresolved_tax'], D(1200))

    def test_future_cash_cannot_fund_earlier_fill(self):
        account, event = self.setup_account()
        account, _ = apply_dividends(account, date(2025, 7, 11), [event])
        with self.assertRaises(ValueError):
            apply_fill(account, Fill('late', '600036', 'BUY', date(2025, 7, 10),
                0, 10, D(10), D(0), date(2025, 7, 11)))

    def test_payment_not_backfilled_after_same_day_sale(self):
        account, event = self.setup_account()
        account, _ = apply_fill(account, Fill('sell', '600036', 'SELL', date(2025, 7, 11), 0, 100, D(10), D(0)))
        with self.assertRaises(ValueError):
            apply_dividends(account, date(2025, 7, 11), [event])
