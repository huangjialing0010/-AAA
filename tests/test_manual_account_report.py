import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ashare_lab.selection.manual_account_report import summarize, render
from ashare_lab.selection.manual_first_execution import assess_first_order
from ashare_lab.selection.manual_quote import SHANGHAI
import test_manual_first_execution as execution_fixture


class AccountReportTests(unittest.TestCase):
    def args(self):
        args=execution_fixture.FirstExecutionTests().args()
        args['run'].update(account_id='SYNTHETIC',generated_at='2026-09-29T16:00:00+08:00',
                           market_cutoff='2026-09-28',signals=[{}],rejections=[{},{}])
        return args

    def test_booked_cash_fees_and_loss_recomputed(self):
        args=self.args()
        event=assess_first_order(**args)
        report=summarize(args['run'],[event],now=args['now'])
        self.assertEqual(report['net_pnl'],'-101.00')
        self.assertEqual(report['counts']['fills'],1)
        self.assertEqual(report['fees'],'5.00')
        self.assertIn('模拟成交',render(report))
        self.assertIn('今日机会与行动建议',render(report))
        self.assertIn('600886',render(report))
        self.assertEqual(report['opportunity']['recommendation_id'],
                         report['opportunity']['order_id'])
        self.assertEqual(report['opportunity']['binding_status'],
                         'BOUND_TO_SIMULATION_ORDER')

    def test_stale_price_never_becomes_current_equity(self):
        args=self.args()
        event=assess_first_order(**args)
        report=summarize(args['run'],[event],now=args['now']+timedelta(days=1))
        self.assertIsNone(report['equity'])
        self.assertIsNone(report['account_return'])

    def test_report_discloses_all_recorded_limitations(self):
        args=self.args()
        event=assess_first_order(**args)
        report=summarize(args['run'],[event],now=args['now']+timedelta(days=1))
        for period in ('daily','weekly','monthly'):
            text=render(report,period)
            for limitation in report['limitations']:
                self.assertIn(limitation,text)
            self.assertIn('不可计算',text)

    def test_waits_do_not_multiply_orders_or_fills(self):
        args=self.args()
        args['now']=datetime(2026,9,29,17,tzinfo=SHANGHAI)
        event=assess_first_order(**args)
        other=deepcopy(event)
        other['checked_at']='2026-09-29T17:01:00+08:00'
        report=summarize(args['run'],[event,other],now=args['now']+timedelta(minutes=2))
        self.assertEqual(report['counts']['orders'],1)
        self.assertEqual(report['net_pnl'],'0')
        self.assertEqual(report['counts']['admission_exclusions'],2)
        self.assertEqual(report['counts']['order_rejections'],0)

    def test_duplicate_terminal_and_cash_mismatch_stop(self):
        args=self.args()
        event=assess_first_order(**args)
        with self.assertRaises(ValueError): summarize(args['run'],[event,event],now=args['now'])
        event['cash']='100000'
        with self.assertRaises(ValueError): summarize(args['run'],[event],now=args['now'])

    def test_incomplete_period_and_missing_check(self):
        args=self.args()
        report=summarize(args['run'],[],now=args['now'])
        self.assertIsNone(report['equity'])
        self.assertIn('周内报告',render(report,'weekly'))
        self.assertIn('月内报告',render(report,'monthly'))

    def test_old_fill_is_not_current_day_or_month_activity(self):
        args=self.args()
        event=assess_first_order(**args)
        report=summarize(args['run'],[event],now=datetime(2026,10,1,16,tzinfo=SHANGHAI))
        self.assertEqual(report['period_counts']['daily']['fills'],0)
        self.assertEqual(report['period_counts']['monthly']['fills'],0)
        self.assertEqual(report['period_counts']['weekly']['fills'],1)
        self.assertEqual(report['counts']['fills'],1)
        self.assertNotIn('本期模拟成交：',render(report,'monthly'))
