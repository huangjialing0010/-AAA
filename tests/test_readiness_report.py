import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_readiness_report", ROOT / "tools" / "build_readiness_report.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ReadinessReportTests(unittest.TestCase):
    def test_empty_prices_remain_blocked_after_non_empty_events_are_reviewed(self):
        candidate = {
            "candidate_count": 3,
            "empty_price_file_count": 1,
            "reviewed_evidence_count": 2,
            "candidates": [
                {"classification": "REQUIRES_EVENT_SOURCE_REVIEW", "event_evidence": {"event_type": "DELISTING"}},
                {"classification": "REQUIRES_EVENT_SOURCE_REVIEW", "event_evidence": {"event_type": "CODE_CHANGE"}},
                {"classification": "EMPTY_PRICE_FILE", "event_evidence": None},
            ],
        }

        gates, counts = MODULE.compute_candidate_gates(candidate)

        self.assertEqual("PASS", gates["non_empty_event_evidence"])
        self.assertEqual("BLOCKED", gates["price_history_coverage"])
        self.assertEqual("INCOMPLETE", gates["event_evidence"])
        self.assertEqual(2, counts["reviewed_non_empty_count"])

    def test_all_gates_pass_only_when_no_empty_price_files_remain(self):
        candidate = {
            "candidate_count": 1,
            "empty_price_file_count": 0,
            "reviewed_evidence_count": 1,
            "candidates": [
                {"classification": "REQUIRES_EVENT_SOURCE_REVIEW", "event_evidence": {"event_type": "DELISTING"}}
            ],
        }

        gates, _ = MODULE.compute_candidate_gates(candidate)

        self.assertEqual("PASS", gates["price_history_coverage"])
        self.assertEqual("PASS", gates["non_empty_event_evidence"])
        self.assertEqual("PASS", gates["event_evidence"])

    def test_empty_price_outside_research_membership_does_not_block_price_scope(self):
        candidate = {
            "candidate_count": 2,
            "empty_price_file_count": 2,
            "reviewed_evidence_count": 2,
            "candidates": [
                {
                    "classification": "EMPTY_PRICE_FILE",
                    "research_membership_observations": 0,
                    "event_evidence": {"event_type": "MERGER_AND_TERMINATION"},
                },
                {
                    "classification": "EMPTY_PRICE_FILE",
                    "research_membership_observations": 0,
                    "event_evidence": {"event_type": "DELISTING"},
                },
            ],
        }

        gates, counts = MODULE.compute_candidate_gates(candidate)

        self.assertEqual("PASS", gates["price_history_coverage"])
        self.assertEqual(0, counts["empty_price_file_in_research_scope_count"])
        self.assertEqual(2, counts["empty_price_file_outside_research_scope_count"])

    def test_empty_price_with_research_membership_still_blocks(self):
        candidate = {
            "candidate_count": 1,
            "empty_price_file_count": 1,
            "reviewed_evidence_count": 1,
            "candidates": [
                {
                    "classification": "EMPTY_PRICE_FILE",
                    "research_membership_observations": 3,
                    "event_evidence": {"event_type": "DELISTING"},
                }
            ],
        }

        gates, counts = MODULE.compute_candidate_gates(candidate)

        self.assertEqual("BLOCKED", gates["price_history_coverage"])
        self.assertEqual(1, counts["empty_price_file_in_research_scope_count"])


if __name__ == "__main__":
    unittest.main()
