from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.engine import DataGateBlocked, ResearchCostConfig, run_research_engine  # noqa: E402


def bar(day: str, code: str = "000001", *, qopen: str = "10", qclose: str = "10", pct: str = "0"):
    return {
        "trade_date": day,
        "code": code,
        "open": "10",
        "high": "10.5",
        "low": "9.5",
        "close": "10",
        "volume": "100000",
        "tradestatus": "1",
        "pct_chg": pct,
        "open_qfq": qopen,
        "close_qfq": qclose,
    }


class SelectionEngineTests(unittest.TestCase):
    def test_signal_executes_next_day_and_uses_intraday_qfq_return(self):
        rows = [bar("2024-01-02"), bar("2024-01-03", qopen="10", qclose="11")]
        result = run_research_engine(
            ["2024-01-02", "2024-01-03"], rows, {"2024-01-02": ["000001"]},
            initial_equity=Decimal("100000"),
        )
        self.assertEqual(result.trades[0]["trade_date"], "2024-01-03")
        self.assertEqual(result.trades[0]["side"], "BUY")
        self.assertGreater(Decimal(result.ledger[-1]["equity"]), Decimal("109000"))
        self.assertEqual(result.ledger[-1]["accounting_scope"], "RESEARCH_PORTFOLIO")

    def test_one_price_limit_up_rejects_buy(self):
        locked = bar("2024-01-03", pct="10")
        locked.update({"open": "11", "high": "11", "low": "11"})
        result = run_research_engine(
            ["2024-01-02", "2024-01-03"], [bar("2024-01-02"), locked],
            {"2024-01-02": ["000001"]},
        )
        self.assertEqual(result.orders[0]["status"], "REJECTED")
        self.assertEqual(result.orders[0]["reason"], "ONE_PRICE_LIMIT_UP")

    def test_sell_uses_date_specific_stamp_tax(self):
        rows = [bar("2023-08-24"), bar("2023-08-25"), bar("2023-08-28"), bar("2023-08-29")]
        result = run_research_engine(
            [row["trade_date"] for row in rows], rows,
            {"2023-08-24": ["000001"], "2023-08-28": ["000002"]},
        )
        sells = [row for row in result.trades if row["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(Decimal(sells[0]["stamp_tax"]), Decimal(sells[0]["gross_notional"]) * Decimal("0.0005"))

    def test_daily_identity_is_exact(self):
        rows = [bar("2024-01-02"), bar("2024-01-03"), bar("2024-01-04", qclose="12")]
        result = run_research_engine(
            [row["trade_date"] for row in rows], rows, {"2024-01-02": ["000001"]},
        )
        for row in result.ledger:
            self.assertEqual(
                Decimal(row["research_cash"]) + Decimal(row["holdings_value"]),
                Decimal(row["equity"]),
            )

    def test_market_provider_can_replace_preloaded_panel(self):
        rows = {("000001", "2024-01-02"): bar("2024-01-02"), ("000001", "2024-01-03"): bar("2024-01-03")}
        result = run_research_engine(
            ["2024-01-02", "2024-01-03"], (), {"2024-01-02": ["000001"]},
            market_row_provider=lambda code, day: rows.get((code, day)),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0]["code"], "000001")

    def test_stale_holding_raises_structured_data_gate(self):
        dates = [f"2024-01-{day:02d}" for day in range(1, 32)] + [f"2024-02-{day:02d}" for day in range(1, 32)] + [f"2024-03-{day:02d}" for day in range(1, 5)]
        rows = [bar(d, qopen="10", qclose="10") for d in dates]
        with self.assertRaises(DataGateBlocked) as raised:
            run_research_engine(dates, rows, {dates[0]: ["000001"]}, market_row_provider=lambda code, day: bar(day) if day in dates[:2] else None)
        self.assertEqual(raised.exception.code, "000001")
        self.assertGreater(raised.exception.stale_days, 60)

    def test_confirmed_event_blocks_before_stale_threshold(self):
        dates = [f"2024-01-{day:02d}" for day in range(1, 10)]
        rows = [bar(d, qopen="10", qclose="10") for d in dates]
        with self.assertRaises(DataGateBlocked) as raised:
            run_research_engine(
                dates, rows, {dates[0]: ["000001"]},
                market_row_provider=lambda code, day: bar(day) if day in dates[:2] else None,
                event_evidence={"000001": {"last_tradable_date": dates[1]}},
            )
        self.assertEqual(raised.exception.reason, "UNSETTLED_CORPORATE_ACTION")


if __name__ == "__main__":
    unittest.main()
