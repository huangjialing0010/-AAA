import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.dividend_tax import NaturalPersonAShareTaxProfile, deferred_dividend_tax, holding_band


class DividendTaxTests(unittest.TestCase):
    def test_bands_use_day_before_sale(self):
        self.assertEqual(holding_band(date(2025, 1, 1), date(2025, 2, 1)), 'ONE_MONTH_OR_LESS')
        self.assertEqual(holding_band(date(2025, 1, 1), date(2025, 2, 2)), 'OVER_ONE_MONTH_TO_ONE_YEAR')
        self.assertEqual(holding_band(date(2024, 1, 1), date(2025, 1, 2)), 'OVER_ONE_YEAR')

    def test_deferred_tax_not_payment_day_tax(self):
        result = deferred_dividend_tax(gross_cash=D('2'), shares=100,
            acquired_on=date(2025, 6, 20), register_date=date(2025, 7, 10), sale_day=date(2025, 7, 20))
        self.assertEqual(result['status'], 'CALCULATED_DEFERRED_LIABILITY')
        self.assertEqual(result['tax'], D('40'))

    def test_long_hold_exempt(self):
        result = deferred_dividend_tax(gross_cash=D('2'), shares=100,
            acquired_on=date(2024, 1, 1), register_date=date(2025, 7, 10), sale_day=date(2026, 1, 2))
        self.assertEqual(result['tax'], D('0.00'))

    def test_pre_policy_blocked(self):
        result = deferred_dividend_tax(gross_cash=D('2'), shares=100,
            acquired_on=date(2015, 8, 1), register_date=date(2015, 9, 7), sale_day=date(2015, 9, 20))
        self.assertEqual(result['status'], 'BLOCKED_POLICY_NOT_COVERED')

    def test_profile_and_bad_inputs(self):
        with self.assertRaises(ValueError):
            NaturalPersonAShareTaxProfile(rate=D('0.1'))
        with self.assertRaises(ValueError):
            holding_band(date(2025, 1, 2), date(2025, 1, 2))
