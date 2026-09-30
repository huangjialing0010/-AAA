import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "run_qlib_alpha_portfolio_smoke",
    ROOT / "tools" / "run_qlib_alpha_portfolio_smoke.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class QlibPortfolioSmokeTests(unittest.TestCase):
    def test_only_point_in_time_members_reach_ranking(self):
        candidates = [
            ("000001", {"trade_date": "2023-01-31"}),
            ("000540", {"trade_date": "2023-01-31"}),
        ]

        filtered = MODULE.filter_point_in_time_members(candidates, frozenset({"000001"}))

        self.assertEqual([("000001", {"trade_date": "2023-01-31"})], filtered)

    def test_benchmark_dominance_rejects_only_when_all_dimensions_are_worse(self):
        portfolio = {"annualized_return": 0.03, "max_drawdown": -0.24, "sharpe_zero_rate": 0.2}
        benchmark = {"annualized_return": 0.07, "max_drawdown": -0.22, "sharpe_zero_rate": 0.5}

        self.assertEqual(
            "REJECTED_BENCHMARK_DOMINATED",
            MODULE.benchmark_dominance_decision(portfolio, benchmark),
        )

        lower_drawdown = dict(portfolio, max_drawdown=-0.10)
        self.assertEqual("REQUIRES_FURTHER_VALIDATION", MODULE.benchmark_dominance_decision(lower_drawdown, benchmark))


if __name__ == "__main__":
    unittest.main()
