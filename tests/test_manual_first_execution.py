import sys
import unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ashare_lab.selection.manual_first_execution import assess_first_order
from ashare_lab.selection.manual_quote import SHANGHAI


class FirstExecutionTests(unittest.TestCase):
    def args(self):
        now = datetime(2026,9,30,16,tzinfo=SHANGHAI)
        run = dict(cash='100000',trades=[],positions=[],orders=[dict(order_id='SYNTHETIC',code='600886',
            execution_day='2026-09-30',signal_day='2026-09-29',mechanism='R1',budget='10000',signal_evidence_id='TEST')])
        quote = {'rc':0,'data':dict(f57='600886',f59=2,f292=5,f86=int(now.timestamp()),
                    f46=1530,f44=1539,f45=1509,f43=1515,f51=1686,f52=1380,f60=1533)}
        review = dict(code='600886',day='2026-09-30',reviewed_at=now.isoformat(),
            information_cutoff='2026-09-30T09:25:00+08:00', evidence_id='SYNTHETIC',source_hashes={'TEST':'TEST'})
        review.update({key:True for key in ('non_st','not_suspended','buyable','no_material_risk',
                                          'corporate_actions_verified','price_mapping_verified')})
        review['eligibility']=dict(code='600886',day='2026-09-30',frozen_at='2026-09-30T09:25:00+08:00',
            non_st=True,no_material_risk=True,corporate_actions_verified=True,source_hashes={'TEST':'TEST'})
        return dict(run=run,now=now,quote=quote,review=review,calendar=['2026-09-29','2026-09-30','2026-10-08'])

    def test_future_wait_and_expiry_never_fill(self):
        for day,status in [(29,'WAITING'),(1,'EXPIRED')]:
            args=self.args()
            args['now']=datetime(2026,9 if day==29 else 10,day,16,tzinfo=SHANGHAI)
            result=assess_first_order(**args)
            self.assertEqual(result['status'],status)
            self.assertFalse(result['fills'])

    def test_realistic_price_and_cash_reconciled(self):
        result=assess_first_order(**self.args())
        self.assertEqual(result['status'],'SIMULATED_BOOKED')
        self.assertEqual(result['fills'][0]['shares'],600)
        self.assertEqual(result['cash'],'90809.00')
        self.assertEqual(result['reconciliation']['status'],'BALANCES_MATCH')

    def test_missing_review_is_not_trade(self):
        args=self.args()
        args['review']=None
        self.assertEqual(assess_first_order(**args)['status'],'WAITING_EVIDENCE')

    def test_post_open_information_and_unknown_risk_rejected(self):
        for change in [{'information_cutoff':'2026-09-30T10:00:00+08:00'},{'no_material_risk':None},
                       {'day':'2026-09-29'},{'source_hashes':{}}]:
            args=self.args()
            args['review'].update(change)
            self.assertEqual(assess_first_order(**args)['status'],'REJECTED')

    def test_after_close_declaration_cannot_replace_prefrozen_eligibility(self):
        args=self.args()
        args['review'].pop('eligibility',None)
        self.assertEqual(assess_first_order(**args)['status'],'REJECTED')

    def test_late_or_changed_prefrozen_eligibility_rejected(self):
        for update in [{'frozen_at':'2026-09-30T16:00:00+08:00'},{'no_material_risk':None},
                       {'code':'600674'},{'day':'2026-09-29'}]:
            args=self.args()
            args['review']['eligibility'].update(update)
            self.assertEqual(assess_first_order(**args)['status'],'REJECTED')

    def test_provider_status_cannot_be_overridden_by_review(self):
        for value in (None,True,'5',10,2,19):
            args=self.args()
            args['quote']['data']['f292']=value
            result=assess_first_order(**args)
            self.assertEqual(result['status'],'WAITING_EVIDENCE')
            self.assertFalse(result['fills'])
        for value in (6,7,8,9,14,15,16):
            args=self.args()
            args['quote']['data']['f292']=value
            self.assertEqual(assess_first_order(**args)['reason'],'PROVIDER_STATUS_NOT_TRADABLE')
