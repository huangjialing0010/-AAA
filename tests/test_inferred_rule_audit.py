import unittest

from ashare_lab.sources.inferred_rule_audit import board_of, classify_row, price_limit_ratio


class InferredRuleAuditTests(unittest.TestCase):
    def test_board_and_st_date_rules(self):
        self.assertEqual(board_of("688001"), "star")
        self.assertEqual(board_of("302132"), "chinext")
        self.assertEqual(price_limit_ratio("000001", is_st=True, trade_date="2025-01-02"), 0.05)
        self.assertEqual(price_limit_ratio("000001", is_st=True, trade_date="2026-07-06"), 0.10)

    def test_classifies_limit_and_suspension(self):
        row = {"trade_date": "2025-01-02", "code": "000001", "preclose": "10.00", "close": "10.50", "volume": "0", "tradestatus": "1", "is_st": "1"}
        item = classify_row(row)
        self.assertTrue(item["inferred_limit_up"])
        self.assertTrue(item["inferred_suspended"])


if __name__ == "__main__":
    unittest.main()
