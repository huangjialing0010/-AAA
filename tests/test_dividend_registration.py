import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_lots import ShareLot, consume_lots
from ashare_lab.selection.dividend_registration import register_dividend
from ashare_lab.selection.dividend_ledger import DividendState, advance_dividends


class RegistrationTests(unittest.TestCase):
    def register(self, lots, day=date(2025, 7, 10)):
        return register_dividend(event_id='synthetic-dividend', code='600036',
            register_date=date(2025, 7, 10), ex_date=date(2025, 7, 11),
            pay_date=date(2025, 7, 11), gross_per_share=D('2'), payment_per_share=D('2'),
            evidence_id='SYNTHETIC', snapshot_date=day, close_snapshot_id='synthetic-close', lots=lots)

    def lot(self, acquired=10, shares=100):
        return ShareLot('buy', '600036', date(2025, 7, acquired), date(2025, 7, acquired+1), shares, 0)

    def test_register_day_buy_receives_even_if_not_sellable(self):
        result = self.register([self.lot()])
        self.assertEqual(result.entitlement.shares, 100)
        self.assertEqual(result.eligible_lots[0].acquired_on, date(2025, 7, 10))

    def test_sell_after_registration_preserves_entitlement(self):
        lots = (self.lot(),)
        registered = self.register(lots)
        updated, _ = consume_lots(lots, '600036', date(2025, 7, 11), 100)
        self.assertEqual(updated, ())
        state, _ = advance_dividends(DividendState(), date(2025, 7, 11), [registered.entitlement])
        self.assertEqual(state.cash_received, D(200))
        self.assertEqual(registered.eligible_lots[0].shares, 100)

    def test_sale_on_registration_day_excluded(self):
        lots, _ = consume_lots((self.lot(acquired=9),), '600036', date(2025, 7, 10), 40)
        self.assertEqual(self.register(lots).entitlement.shares, 60)

    def test_future_or_wrong_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            self.register([self.lot(acquired=11)])
        with self.assertRaises(ValueError):
            self.register([], date(2025, 7, 11))

    def test_empty_qualifies_for_zero(self):
        self.assertEqual(self.register([]).entitlement.shares, 0)
