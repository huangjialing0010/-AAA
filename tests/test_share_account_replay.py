import copy
import unittest
from tools.run_share_account_smoke import run
from tools.replay_share_account_smoke import normalize, replay


class ReplayTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(replay(normalize(run()))['status'], 'REPLAY_MATCH')

    def test_changed_balance_rejected(self):
        report = normalize(run())
        report['records'][0]['cash_after'] = '1000'
        with self.assertRaises(ValueError): replay(report)

    def test_changed_rejection_input_rejected(self):
        report = normalize(run())
        report['records'][1]['tradable'] = True
        with self.assertRaises(ValueError): replay(report)

    def test_changed_entitlement_rejected(self):
        report = normalize(run())
        report['records'][2]['registration']['entitlement']['shares'] = 200
        with self.assertRaises(ValueError): replay(report)
