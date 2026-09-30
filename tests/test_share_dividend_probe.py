import unittest
from tools.validate_share_dividend_probe import check_event, numeric


class DividendProbeTests(unittest.TestCase):
    def event(self):
        return dict(code='sh.600036', dividRegistDate='2025-07-10',
                    dividOperateDate='2025-07-11', dividPayDate='2025-07-11',
                    dividCashPsBeforeTax='2', dividCashPsAfterTax='1.8或2',
                    dividStocksPs='0', dividReserveToStockPs='')

    def test_same_day_payment_allowed_but_tax_unknown(self):
        errors, reviews = check_event(self.event(), '600036', 2025)
        self.assertEqual(errors, [])
        self.assertIn('NET_CASH_NOT_SINGLE_NUMBER', reviews)
        self.assertIn('UNRESOLVED_dividReserveToStockPs', reviews)

    def test_payment_before_ex_rejected(self):
        event = self.event()
        event['dividPayDate'] = '2025-07-09'
        self.assertIn('INVALID_DATE_ORDER', check_event(event, '600036', 2025)[0])

    def test_wrong_year_and_security_rejected(self):
        errors, _ = check_event(self.event(), '601857', 2024)
        self.assertIn('SECURITY_MISMATCH', errors)
        self.assertIn('QUERY_YEAR_MISMATCH', errors)

    def test_missing_date_rejected(self):
        event = self.event()
        event['dividPayDate'] = ''
        self.assertIn('MISSING_OR_INVALID_DATE', check_event(event, '600036', 2025)[0])

    def test_invalid_numbers_not_zero(self):
        for value in ('', None, 'NaN', 'Infinity', '-1', '1或2'):
            self.assertFalse(numeric(value))
        self.assertTrue(numeric('0'))
