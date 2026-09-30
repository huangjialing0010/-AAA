import sys
import unittest
import csv
from tempfile import TemporaryDirectory
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools')]
from ashare_lab.selection.canonical import merge_dual_price_rows
from fetch_baostock_historical_daily import validate_result
from ashare_lab.sources.baostock_source import QueryResult
from validate_baostock_historical_daily import read_segment


def sample():
    return dict(date='2022-01-26', code='sh.688223', open='8.5000', high='11.9500',
                low='8.5000', close='10.5500', preclose='5.0000', volume='881979200',
                amount='8997736842.5200', adjustflag='3', turn='66.734600',
                tradestatus='1', pctChg='111.000000', isST='0')


class QfqExceptionTests(unittest.TestCase):
    def test_independent_reader_requires_exact_missing_declaration(self):
        row = sample()
        with TemporaryDirectory() as directory:
            path = Path(directory)/'source.csv'
            with path.open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            entry = dict(stock_code='688223', adjustflag='2', start_date='2022-01-01',
                         end_date='2026-09-02', relative_path='source.csv', rows=1,
                         min_date=row['date'], max_date=row['date'], missing_qfq_dates=[row['date']])
            errors = []; read_segment(path, entry, errors); self.assertEqual(errors, [])
            entry['missing_qfq_dates'] = []
            errors = []; read_segment(path, entry, errors); self.assertTrue(errors)

    def test_missing_not_relabelled(self):
        raw = sample(); adjusted = sample()
        result = merge_dual_price_rows([raw], [adjusted], expected_code='688223')[0]
        for field in ('open','high','low','close','preclose'):
            self.assertEqual(result[field+'_qfq'], '')
            self.assertEqual(result[field], raw[field])
        self.assertEqual(result['qfq_scale'], '')
        self.assertEqual(result['qfq_status'], 'SOURCE_UNADJUSTED_RESPONSE')
        self.assertEqual(adjusted, sample())

    def test_fetch_records_exception(self):
        row = sample()
        task = dict(stock_code='688223', adjustflag='2', start_date='2022-01-01',
                    end_date='2026-09-02', relative_path='daily_qfq/688223/test.csv')
        result = validate_result(QueryResult(list(row), [row]), task)
        self.assertEqual(result['missing_qfq_dates'], ['2022-01-26'])

    def test_other_date_or_code_rejected(self):
        for change in ({'date':'2022-01-27'}, {'code':'sh.688222'}, {'adjustflag':'1'}):
            raw = sample(); adjusted = sample(); adjusted.update(change)
            with self.assertRaises(ValueError):
                merge_dual_price_rows([raw], [adjusted], expected_code='688223')

    def test_exception_prices_must_match_raw(self):
        raw = sample(); adjusted = sample(); adjusted['close'] = '9.9'
        with self.assertRaises(ValueError):
            merge_dual_price_rows([raw], [adjusted], expected_code='688223')
