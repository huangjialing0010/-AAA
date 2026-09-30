import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.order_gate import ExecutionRules, check_order


class OrderGateTests(unittest.TestCase):
    def args(self):
        return dict(code='600036', side='BUY', shares=100, signal_day=date(2025, 7, 9),
                    execution_day=date(2025, 7, 10), price=D(10), tradable=True,
                    rules=ExecutionRules(date(2025, 7, 10), '600036', 'SYNTHETIC', 100, 100, D(9), D(11)))

    def test_buy_quantity(self):
        args = self.args()
        self.assertEqual(check_order(**args), 'PRECHECK_PASS_NOT_FILL')
        args['shares'] = 101
        self.assertEqual(check_order(**args), 'BUY_QUANTITY_RULE_VIOLATION')
        args['rules'] = replace(args['rules'], minimum_buy=200, buy_increment=1)
        args['shares'] = 201
        self.assertEqual(check_order(**args), 'PRECHECK_PASS_NOT_FILL')

    def test_limits_and_missing_rules(self):
        args = self.args()
        args['price'] = D(11)
        self.assertEqual(check_order(**args), 'BUY_AT_UPPER_LIMIT')
        args['rules'] = None
        self.assertEqual(check_order(**args), 'MISSING_VERIFIED_RULES')

    def test_signal_and_tradability(self):
        args = self.args()
        args['tradable'] = None
        self.assertEqual(check_order(**args), 'NOT_CONFIRMED_TRADABLE')
        args['signal_day'] = args['execution_day']
        self.assertEqual(check_order(**args), 'SIGNAL_NOT_PRIOR_DAY')

    def test_unknown_sell_policy_not_passed(self):
        args = self.args()
        args.update(side='SELL', available_shares=100)
        self.assertEqual(check_order(**args), 'SELL_QUANTITY_POLICY_PENDING')

    def test_mainboard_whole_remainder_examples(self):
        args = self.args()
        args.update(side='SELL', available_shares=299, total_shares=299)
        args['rules'] = replace(args['rules'], sell_policy='ROUND100_WITH_WHOLE_REMAINDER')
        for shares in (99, 100, 199, 200, 299):
            args['shares'] = shares
            self.assertEqual(check_order(**args), 'PRECHECK_PASS_NOT_FILL')
        for shares in (1, 101, 198, 298):
            args['shares'] = shares
            self.assertEqual(check_order(**args), 'ODD_LOT_SPLIT_FORBIDDEN')

    def test_small_balance_must_be_sold_whole(self):
        args = self.args()
        args.update(side='SELL', available_shares=199, total_shares=199)
        args['rules'] = replace(args['rules'], sell_policy='MIN200_OR_ENTIRE_BALANCE')
        args['shares'] = 199
        self.assertEqual(check_order(**args), 'PRECHECK_PASS_NOT_FILL')
        args['shares'] = 100
        self.assertEqual(check_order(**args), 'ODD_LOT_SPLIT_FORBIDDEN')
        args.update(shares=199, available_shares=199, total_shares=399)
        self.assertEqual(check_order(**args), 'ODD_LOT_SPLIT_FORBIDDEN')
