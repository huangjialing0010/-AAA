import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ashare_lab.sources.rule_snapshot import load_rule_snapshot


class RuleSnapshotTests(unittest.TestCase):
    def test_loads_unique_rule_rows(self):
        fields = ('trade_date,code,tradestatus,upper_limit,lower_limit,buyable,sellable,source,'
                  'open,evidence_id,execution_evidence_id,minimum_buy,buy_increment,sell_policy,tradable\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.csv'
            path.write_text(fields + '2025-07-09,600036,1,11,9,1,1,test,10,RULE,EXEC,100,100,ROUND100_WITH_WHOLE_REMAINDER,1\n', encoding='utf-8')
            self.assertIn(('600036', '2025-07-09'), load_rule_snapshot(path))

    def test_missing_rule_field_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.csv'
            path.write_text('trade_date,code\n2025-07-09,600036\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_rule_snapshot(path)


if __name__ == '__main__':
    unittest.main()
