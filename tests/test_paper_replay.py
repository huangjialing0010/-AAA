import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ashare_lab.selection.cash_dividend_account import CashDividendAccount
from ashare_lab.selection.fees import FeeRuleProfile
from ashare_lab.selection.paper_replay import replay_orders
from ashare_lab.selection.share_account import ShareAccount


class PaperReplayTests(unittest.TestCase):
    def setUp(self):
        self.profile = FeeRuleProfile('TEST', date(2025, 1, 1), D('0.0003'), D('5'), D('0.0005'))
        self.orders = [{
            'order_id': 'O1', 'signal_date': '2025-07-08', 'order_date': '2025-07-09',
            'code': '600036', 'side': 'BUY', 'requested_notional': '1000',
        }]
        self.rules = {('600036', '2025-07-09'): {
            'open': '10', 'minimum_buy': '100', 'buy_increment': '100',
            'lower_limit': '9', 'upper_limit': '11', 'evidence_id': 'RULE1',
            'execution_evidence_id': 'EXEC1', 'tradable': '1',
        }}

    def test_replays_verified_buy(self):
        account, decisions = replay_orders(
            orders=self.orders, rules_by_key=self.rules,
            account=CashDividendAccount(ShareAccount(D('2000'))), fee_profile=self.profile,
            next_trade_day={'2025-07-09': '2025-07-10'},
        )
        self.assertEqual(decisions[0].status, 'BOOKED')
        self.assertEqual(account.shares.cash, D('995.00'))

    def test_missing_rules_rejected(self):
        _, decisions = replay_orders(
            orders=self.orders, rules_by_key={},
            account=CashDividendAccount(ShareAccount(D('2000'))), fee_profile=self.profile,
            next_trade_day={'2025-07-09': '2025-07-10'},
        )
        self.assertEqual(decisions[0].reason, 'MISSING_VERIFIED_RULES')


if __name__ == '__main__':
    unittest.main()
