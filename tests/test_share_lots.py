import sys
import unittest
from datetime import date as Day
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_lots import ShareLot, consume_lots


class ShareLotTests(unittest.TestCase):
    def lot(self, name, acquired, available, shares=100, seq=0):
        return ShareLot(name, '600036', Day(2025, 7, acquired), Day(2025, 7, available), shares, seq)

    def test_old_shares_sellable_new_shares_locked(self):
        lots = (self.lot('old', 9, 10), self.lot('new', 10, 11))
        updated, sales = consume_lots(lots, '600036', Day(2025, 7, 10), 60)
        self.assertEqual([lot.shares for lot in updated], [40, 100])
        self.assertEqual(sales[0]['lot_id'], 'old')
        self.assertEqual(lots[0].shares, 100)

    def test_insufficient_is_atomic(self):
        lots = (self.lot('old', 9, 10), self.lot('new', 10, 11))
        with self.assertRaises(ValueError):
            consume_lots(lots, '600036', Day(2025, 7, 10), 101)
        self.assertEqual(sum(l.shares for l in lots), 200)

    def test_fifo_by_explicit_same_day_sequence(self):
        lots = (self.lot('b', 9, 10, seq=1), self.lot('a', 9, 10, seq=0))
        updated, sales = consume_lots(lots, '600036', Day(2025, 7, 10), 150)
        self.assertEqual([s['lot_id'] for s in sales], ['a', 'b'])
        self.assertEqual(updated[0].shares, 50)

    def test_duplicate_and_future_rejected(self):
        lot = self.lot('a', 10, 11)
        for lots, day in (((lot, lot), 11), ((lot,), 9)):
            with self.assertRaises(ValueError):
                consume_lots(lots, '600036', Day(2025, 7, day), 1)

    def test_same_day_availability_rejected(self):
        with self.assertRaises(ValueError):
            self.lot('a', 10, 10)
