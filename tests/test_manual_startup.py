import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.manual_startup import startup_check


class StartupTests(unittest.TestCase):
    def args(self):
        return dict(strategy_frozen=True, eligibility_verified=True,
                    execution_connected=True, snapshot_sealed=True,
                    verified_data_cutoff='2026-09-28', required_data_cutoff='2026-09-28')

    def test_pass_is_not_trade(self):
        result = startup_check(**self.args())
        self.assertEqual(result['status'], 'PRECHECK_PASS_NOT_EXECUTED')
        self.assertEqual(result['trades_created'], 0)

    def test_missing_and_truthy_values_block(self):
        for key in ('strategy_frozen', 'eligibility_verified', 'execution_connected', 'snapshot_sealed'):
            for value in (None, False, 'true', 1):
                args = self.args()
                args[key] = value
                self.assertEqual(startup_check(**args)['status'], 'BLOCKED')

    def test_cutoff_exact_not_future_or_old(self):
        for cutoff in (None, '2026-09-24', '2026-09-29'):
            args = self.args()
            args['verified_data_cutoff'] = cutoff
            self.assertEqual(startup_check(**args)['status'], 'BLOCKED')
