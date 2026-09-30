from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.price_mechanism import (  # noqa: E402
    cooldown_allows,
    forward_observation,
    mechanism_flags,
    summarize_observations,
)


def fixture(prices: list[float]) -> tuple[list[str], dict[str, dict[str, str]]]:
    start = date(2020, 1, 1)
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(len(prices))]
    rows = {}
    for day, value in zip(calendar, prices):
        rows[day] = {
            "trade_date": day,
            "close_qfq": str(value),
            "open_qfq": str(value),
            "high_qfq": str(value * 1.01),
            "low_qfq": str(value * 0.99),
            "tradestatus": "1",
        }
    return calendar, rows


class PriceMechanismTests(unittest.TestCase):
    def test_cooldown_includes_the_126th_trading_day(self):
        self.assertFalse(cooldown_allows(100, 226))
        self.assertTrue(cooldown_allows(100, 227))

    def test_left_signal_uses_only_prior_252_closes(self):
        calendar, rows = fixture([100.0] * 252 + [79.0, 1000.0])
        self.assertTrue(mechanism_flags(rows, calendar, 252)["L1"])
        rows[calendar[253]]["close_qfq"] = "1"
        self.assertTrue(mechanism_flags(rows, calendar, 252)["L1"])

    def test_right_signal_requires_breakout_and_rising_60_day_mean(self):
        prices = [80.0] * 132 + [80.0 + index for index in range(121)]
        calendar, rows = fixture(prices)
        flags = mechanism_flags(rows, calendar, 252)
        self.assertTrue(flags["R1"])
        rows[calendar[200]]["close_qfq"] = ""
        self.assertFalse(mechanism_flags(rows, calendar, 252)["R1"])

    def test_calendar_gap_is_not_compressed(self):
        calendar, rows = fixture([100.0] * 252 + [79.0])
        del rows[calendar[100]]
        self.assertFalse(mechanism_flags(rows, calendar, 252)["L1"])

    def test_forward_observation_counts_entry_day_as_day_one(self):
        calendar, rows = fixture([100.0] * 260)
        rows[calendar[253]]["open_qfq"] = "50"
        rows[calendar[253]]["high_qfq"] = "55"
        rows[calendar[253]]["low_qfq"] = "45"
        rows[calendar[254]]["close_qfq"] = "60"
        rows[calendar[254]]["high_qfq"] = "62"
        rows[calendar[254]]["low_qfq"] = "48"
        observed = forward_observation(rows, calendar, 252, 2)
        self.assertEqual(observed["end_date"], calendar[254])
        self.assertAlmostEqual(observed["forward_return"], 0.2)
        self.assertAlmostEqual(observed["max_favorable_excursion"], 0.24)
        self.assertAlmostEqual(observed["max_adverse_excursion"], -0.1)

    def test_forward_observation_rejects_missing_path(self):
        calendar, rows = fixture([100.0] * 260)
        rows[calendar[254]]["low_qfq"] = ""
        self.assertEqual(forward_observation(rows, calendar, 252, 2)["status"], "PATH_INCOMPLETE")

    def test_summary_keeps_unobserved_in_coverage_denominator(self):
        rows = [
            {"mechanism": "L1", "horizon": 21, "signal_date": "2021-01-01", "status": "OBSERVED",
             "forward_return": 0.1, "benchmark_return": 0.02, "excess_return": 0.08,
             "max_adverse_excursion": -0.05, "max_favorable_excursion": 0.12},
            {"mechanism": "L1", "horizon": 21, "signal_date": "2021-02-01", "status": "PATH_INCOMPLETE"},
        ]
        overall = next(row for row in summarize_observations(rows) if row["group_type"] == "overall")
        self.assertEqual(overall["signal_count"], 2)
        self.assertEqual(overall["observed_count"], 1)
        self.assertEqual(overall["coverage_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
