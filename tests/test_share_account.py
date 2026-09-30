import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_account import Fill, ShareAccount, book_fill, mark_equity


class AccountTests(unittest.TestCase):
    def buy(self):
        return Fill('buy1', '600036', 'BUY', date(2025, 7, 9), 0, 100, D(10), D(5), date(2025, 7, 10))

    def test_round_trip_cash_and_lots(self):
        state, _ = book_fill(ShareAccount(D(2000)), self.buy())
        self.assertEqual(state.cash, D(995))
        sell = Fill('sell1', '600036', 'SELL', date(2025, 7, 10), 0, 40, D(11), D(5))
        state, sales = book_fill(state, sell)
        self.assertEqual(state.cash, D(1430))
        self.assertEqual(state.lots[0].shares, 60)
        self.assertEqual(sales[0]['acquired_on'], date(2025, 7, 9))
        self.assertEqual(mark_equity(state, {'600036': D(11)})['cash_plus_stocks'], D(2090))

    def test_duplicate_and_conflict(self):
        state, _ = book_fill(ShareAccount(D(2000)), self.buy())
        self.assertEqual(book_fill(state, self.buy())[0], state)
        with self.assertRaises(ValueError):
            book_fill(state, replace(self.buy(), price=D(12)))

    def test_insufficient_cash_no_mutation(self):
        state = ShareAccount(D(1000))
        with self.assertRaises(ValueError):
            book_fill(state, self.buy())
        self.assertEqual(state.cash, D(1000))
        self.assertEqual(state.lots, ())

    def test_same_day_sell_and_missing_price_block(self):
        state, _ = book_fill(ShareAccount(D(2000)), self.buy())
        with self.assertRaises(ValueError):
            book_fill(state, Fill('sell', '600036', 'SELL', date(2025, 7, 9), 1, 100, D(10), D(5)))
        with self.assertRaises(ValueError):
            mark_equity(state, {})

    def test_earlier_sequence_block(self):
        state, _ = book_fill(ShareAccount(D(4000)), self.buy())
        with self.assertRaises(ValueError):
            book_fill(state, replace(self.buy(), fill_id='another'))
