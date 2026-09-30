import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.dividend_ledger import DividendEntitlement, DividendState, advance_dividends


class DividendLedgerTests(unittest.TestCase):
    def event(self, pay=11, payment='2', shares=100):
        return DividendEntitlement('test-event', '600036', date(2025, 7, 10),
            date(2025, 7, 11), date(2025, 7, pay), shares, D('2'), D(payment), 'SYNTHETIC')

    def test_same_day_and_duplicate(self):
        event = self.event()
        state, audit = advance_dividends(DividendState(), event.ex_date, [event])
        self.assertEqual([row[1] for row in audit], ['RECEIVABLE', 'PAYMENT'])
        self.assertEqual((state.receivable, state.cash_received), (D(0), D(200)))
        again, audit = advance_dividends(state, event.ex_date, [event, event])
        self.assertEqual(again, state)
        self.assertEqual(audit, [])

    def test_later_payment_and_withheld_reconcile(self):
        event = self.event(pay=14, payment='1.8')
        state, _ = advance_dividends(DividendState(), event.ex_date, [event])
        self.assertEqual(state.receivable, D(200))
        state, _ = advance_dividends(state, event.pay_date, [])
        self.assertEqual((state.cash_received, state.tax_withheld, state.receivable), (D(180), D(20), D(0)))

    def test_missing_day_rejected_and_input_unchanged(self):
        original = DividendState()
        with self.assertRaises(ValueError):
            advance_dividends(original, date(2025, 7, 12), [self.event()])
        self.assertEqual(original, DividendState())

    def test_changed_entitlement_rejected(self):
        state, _ = advance_dividends(DividendState(), date(2025, 7, 11), [self.event()])
        with self.assertRaises(ValueError):
            advance_dividends(state, date(2025, 7, 11), [self.event(shares=200)])

    def test_nonfinite_and_fractional_shares_rejected(self):
        with self.assertRaises(ValueError):
            self.event(payment='NaN')
        with self.assertRaises(ValueError):
            self.event(shares=1.5)

    def test_zero_entitlement(self):
        state, _ = advance_dividends(DividendState(), date(2025, 7, 11), [self.event(shares=0)])
        self.assertEqual(state.cash_received, D(0))
