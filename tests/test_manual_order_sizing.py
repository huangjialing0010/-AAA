import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.fees import FeeRuleProfile
from ashare_lab.selection.manual_order_sizing import size_opening_buy


class ManualSizingTests(unittest.TestCase):
    def args(self):
        return dict(cash=D('100000'), single_budget=D('10000'),
                    portfolio_budget=D('40000'), execution_price=D('10'),
                    minimum_buy=100, buy_increment=100,
                    fees=FeeRuleProfile('TEST_ONLY', date(2020, 1, 1), D('.0003'), D(5), D('.0005')),
                    day=date(2026, 9, 29))

    def test_fee_prevents_budget_overrun(self):
        result = size_opening_buy(**self.args())
        self.assertEqual(result.shares, 900)
        self.assertEqual(result.cash_required, D(9005))
        self.assertEqual(result.status, 'SIZED_NOT_EXECUTED')

    def test_all_three_budgets_bind(self):
        for field in ('cash', 'single_budget', 'portfolio_budget'):
            args = self.args()
            args[field] = D(1005)
            self.assertEqual(size_opening_buy(**args).shares, 100)
            args[field] = D(1004)
            self.assertEqual(size_opening_buy(**args).status, 'BUDGET_BELOW_MINIMUM_LOT')

    def test_invalid_inputs(self):
        for field in ('cash', 'single_budget', 'portfolio_budget', 'execution_price'):
            for value in (None, False, D('NaN'), D('Infinity'), D('-1')):
                args = self.args()
                args[field] = value
                with self.assertRaises(ValueError):
                    size_opening_buy(**args)
        for field, value in [('execution_price', D(0)), ('minimum_buy', True),
                             ('buy_increment', 0), ('day', date(2019, 1, 1))]:
            args = self.args()
            args[field] = value
            with self.assertRaises(ValueError):
                size_opening_buy(**args)

    def test_matches_exhaustive_search(self):
        for budget in (0, 99, 1005, 20000, 50000):
            for price in ('.37', '10', '199.99'):
                args = self.args()
                args.update(single_budget=D(budget), execution_price=D(price))
                args.update(minimum_buy=200, buy_increment=50)
                bound = min(args['cash'], args['single_budget'], args['portfolio_budget'])
                best = 0
                for shares in range(200, int(bound / D(price)) + 1, 50):
                    cost = D(price)*shares + args['fees'].calculate(side='BUY', price=D(price), shares=shares, day=args['day'])
                    if cost <= bound:
                        best = shares
                self.assertEqual(size_opening_buy(**args).shares, best)


if __name__ == '__main__':
    unittest.main()
