import unittest
from datetime import date
from decimal import Decimal

from ashare_lab.selection.fees import FeeRuleProfile, HistoricalAshareFeeProfile


class FeeRuleTests(unittest.TestCase):
    def setUp(self):
        self.profile = FeeRuleProfile(
            profile_id="TEST_FEE_V1",
            effective_from=date(2025, 1, 1),
            commission_rate=Decimal("0.0003"),
            commission_minimum=Decimal("5"),
            stamp_tax_sell_rate=Decimal("0.0005"),
        )

    def test_buy_has_commission_minimum(self):
        self.assertEqual(self.profile.calculate(side="BUY", price=Decimal("10"), shares=100, day=date(2025, 1, 2)), Decimal("5.00"))

    def test_sell_adds_stamp_tax(self):
        self.assertEqual(self.profile.calculate(side="SELL", price=Decimal("10"), shares=10000, day=date(2025, 1, 2)), Decimal("80.00"))

    def test_pre_effective_date_rejected(self):
        with self.assertRaises(ValueError):
            self.profile.calculate(side="BUY", price=Decimal("10"), shares=100, day=date(2024, 12, 31))

    def test_historical_stamp_tax_switch(self):
        profile = HistoricalAshareFeeProfile(
            "HISTORICAL", date(2020, 1, 1), Decimal("0.0003"), Decimal("5"),
            Decimal("0.001"), Decimal("0.0005"), date(2023, 8, 28)
        )
        self.assertEqual(profile.calculate(side="SELL", price=Decimal("10"), shares=10000, day=date(2023, 8, 27)), Decimal("130.00"))
        self.assertEqual(profile.calculate(side="SELL", price=Decimal("10"), shares=10000, day=date(2023, 8, 28)), Decimal("80.00"))


if __name__ == "__main__":
    unittest.main()
