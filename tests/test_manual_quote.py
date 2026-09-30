import sys
import unittest
from pathlib import Path
from datetime import datetime, date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ashare_lab.selection.manual_quote import normalize_quote, SHANGHAI


class QuoteTests(unittest.TestCase):
    def args(self):
        now = datetime(2026,9,29,16,tzinfo=SHANGHAI)
        return dict(payload={'rc':0,'data':dict(f57='600886',f59=2,f86=int(now.timestamp()),
                    f46=1530,f44=1539,f45=1509,f43=1515,f51=1686,f52=1380,f60=1533)},
                    code='600886',day=date(2026,9,29),observed_at=now)

    def test_normalizes_without_inventing_tradability(self):
        result = normalize_quote(**self.args())
        self.assertEqual(str(result['open']), '15.3')
        self.assertIsNone(result['tradable'])

    def test_missing_false_and_scale_fail_closed(self):
        for field, value in [('f46',None),('f46',False),('f59',3),('f57','600674'),('f51',100)]:
            args = self.args()
            args['payload']['data'][field] = value
            with self.assertRaises(ValueError): normalize_quote(**args)

    def test_previous_day_cannot_fill_next_day(self):
        args = self.args()
        args.update(day=date(2026,9,30),observed_at=datetime(2026,9,30,16,tzinfo=SHANGHAI))
        with self.assertRaises(ValueError): normalize_quote(**args)

    def test_future_timestamp_rejected(self):
        args = self.args()
        args['payload']['data']['f86'] += 60
        with self.assertRaises(ValueError): normalize_quote(**args)
