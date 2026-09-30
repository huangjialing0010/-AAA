import unittest
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import test_manual_account_report as report_fixture
from ashare_lab.selection.manual_first_execution import assess_first_order
from ashare_lab.selection.manual_account_replay import replay_first_journal
from ashare_lab.selection.manual_quote import SHANGHAI


class ManualReplayTests(unittest.TestCase):
    def fixture(self):
        args = report_fixture.AccountReportTests().args()
        return args, assess_first_order(**args)

    def test_booked_bridge_preserves_original_and_is_repeatable(self):
        args, event = self.fixture()
        before = deepcopy(event)
        first, proof = replay_first_journal(args['run'], [event], now=args['now'])
        second, _ = replay_first_journal(args['run'], [event], now=args['now'])
        self.assertEqual(first, second)
        self.assertEqual(first.shares.cash, Decimal(event['cash']))
        self.assertEqual(len(first.shares.fills), 1)
        self.assertEqual(proof['status'], 'REPLAY_MATCH')
        self.assertEqual(event, before)

    def test_waiting_has_no_fill(self):
        args, _ = self.fixture()
        args['now'] = datetime(2026,9,30,9,tzinfo=SHANGHAI)
        event = assess_first_order(**args)
        account, _ = replay_first_journal(args['run'], [event], now=args['now'])
        self.assertFalse(account.shares.fills)
        self.assertEqual(account.shares.cash, Decimal(args['run']['cash']))

    def test_lot_date_and_sequence_tampering_rejected(self):
        args, event = self.fixture()
        for field, value in [('sellable_on', '2026-10-09'), ('buy_day','2026-09-29'),
                             ('sequence',999)]:
            changed = deepcopy(event)
            changed['positions'][0][field] = value
            with self.assertRaises(ValueError):
                replay_first_journal(args['run'], [changed], now=args['now'])

    def test_duplicate_terminal_rejected(self):
        args, event = self.fixture()
        with self.assertRaises(ValueError):
            replay_first_journal(args['run'], [event,event], now=args['now'])
