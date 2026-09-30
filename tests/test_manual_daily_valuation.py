import unittest
from datetime import timedelta
from decimal import Decimal
import test_manual_account_report as fixture
from ashare_lab.selection.manual_first_execution import assess_first_order
from ashare_lab.selection.manual_account_replay import replay_first_journal
from ashare_lab.selection.manual_daily_valuation import value_day


class DailyValuationTests(unittest.TestCase):
    def args(self):
        args = fixture.AccountReportTests().args()
        event = assess_first_order(**args)
        account, _ = replay_first_journal(args['run'], [event], now=args['now'])
        return dict(account=account, initial_cash=Decimal(args['run']['cash']),
                    now=args['now'], ledger_day=args['now'].date(), actions_verified=True,
                    quotes={'600886': {'payload':args['quote'], 'observed_at':args['now'],
                                       'evidence_id':'SYNTHETIC'}})

    def test_valid_close_matches_first_event(self):
        args=self.args()
        result=value_day(**args)
        self.assertEqual(result['status'],'VALUED')
        self.assertEqual(Decimal(result['net_pnl_before_unresolved_tax']),Decimal('-101'))

    def test_missing_or_stale_quote_does_not_invent_equity(self):
        args=self.args()
        args['quotes']={}
        self.assertIsNone(value_day(**args)['equity_before_unresolved_tax'])
        args=self.args()
        args['now']+=timedelta(days=1)
        args['ledger_day']=args['now'].date()
        self.assertEqual(value_day(**args)['status'],'INCOMPLETE')

    def test_review_and_ledger_gates(self):
        for field,value in [('actions_verified',None),('ledger_day',None)]:
            args=self.args();args[field]=value
            self.assertIsNone(value_day(**args)['account_return'])

    def test_intraday_status_and_future_observation_rejected(self):
        args=self.args();args['quotes']['600886']['payload']['data']['f292']=2
        self.assertEqual(value_day(**args)['status'],'INCOMPLETE')
        args=self.args();args['quotes']['600886']['observed_at']+=timedelta(seconds=1)
        self.assertEqual(value_day(**args)['status'],'INCOMPLETE')
