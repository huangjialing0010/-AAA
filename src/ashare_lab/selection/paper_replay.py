"""将研究订单送入受规则约束的纸面账户；不连接券商。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_DOWN

from .cash_dividend_account import CashDividendAccount
from .checked_execution import ExecutionDecision, apply_checked_execution
from .fees import FeeRuleProfile, HistoricalAshareFeeProfile
from .order_gate import ExecutionRules
from .share_account import Fill


FeeProfile = FeeRuleProfile | HistoricalAshareFeeProfile


def _shares(requested: Decimal, price: Decimal, code: str) -> int:
    lot = 200 if code.startswith("688") else 100
    value = int((requested / price / Decimal(lot)).to_integral_value(rounding=ROUND_DOWN))
    return value * lot


def replay_orders(*, orders: list[dict[str, str]], rules_by_key: dict[tuple[str, str], dict[str, str]],
                  account: CashDividendAccount, fee_profile: FeeProfile,
                  next_trade_day: dict[str, str]) -> tuple[CashDividendAccount, list[ExecutionDecision]]:
    """严格按订单日期重放；规则缺失或费用不一致时保留拒绝决策。"""
    decisions: list[ExecutionDecision] = []
    for sequence, order in enumerate(sorted(orders, key=lambda row: (row["order_date"], row["order_id"])), start=1):
        day = order["order_date"]
        key = (order["code"], day)
        row = rules_by_key.get(key)
        if row is None:
            decisions.append(ExecutionDecision(order["order_id"], "REJECTED", "MISSING_VERIFIED_RULES", "", ""))
            continue
        try:
            price = Decimal(row["open"])
            shares = _shares(Decimal(order["requested_notional"]), price, order["code"])
            if shares <= 0:
                raise ValueError("BELOW_MINIMUM_ORDER")
            sellable_on = next_trade_day.get(day)
            fill = Fill(
                order["order_id"], order["code"], order["side"], date.fromisoformat(day), sequence,
                shares, price,
                fee_profile.calculate(side=order["side"], price=price, shares=shares, day=date.fromisoformat(day)),
                date.fromisoformat(sellable_on) if order["side"] == "BUY" and sellable_on else None,
            )
            execution_rules = ExecutionRules(
                date.fromisoformat(day), order["code"], row["evidence_id"], int(row["minimum_buy"]),
                int(row["buy_increment"]), Decimal(row["lower_limit"]), Decimal(row["upper_limit"]),
                row.get("sell_policy", "UNVERIFIED"),
            )
            tradable = row.get("tradable") == "1"
            account, decision, _ = apply_checked_execution(
                account, fill, signal_day=date.fromisoformat(order["signal_date"]),
                rules=execution_rules, tradable=tradable,
                execution_evidence_id=row.get("execution_evidence_id", ""), fee_profile=fee_profile,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            decision = ExecutionDecision(order["order_id"], "REJECTED", "INPUT_REJECTED:" + str(error), "", "")
        decisions.append(decision)
    return account, decisions
