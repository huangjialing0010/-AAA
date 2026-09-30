"""前复权收益口径的多标的研究组合引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Callable, Iterable


MONEY = Decimal("0.01")


class DataGateBlocked(RuntimeError):
    """研究组合因关键行情缺失而阻断。"""

    def __init__(self, code: str, trade_date: str, stale_days: int, reason: str = "MISSING_PRICE"):
        self.code = code
        self.trade_date = trade_date
        self.stale_days = stale_days
        self.reason = reason
        detail = "未结算公司行动" if reason == "UNSETTLED_CORPORATE_ACTION" else f"持仓连续超过{stale_days}个交易日没有有效前复权价格"
        super().__init__(f"{code} {detail}")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def text(value: Decimal) -> str:
    return format(money(value), "f")


def number(value: str) -> Decimal | None:
    if value == "":
        return None
    try:
        parsed = Decimal(value)
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


@dataclass(frozen=True)
class ResearchCostConfig:
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("5")
    stamp_tax_before: Decimal = Decimal("0.001")
    stamp_tax_after: Decimal = Decimal("0.0005")
    stamp_change_date: str = "2023-08-28"
    target_exposure: Decimal = Decimal("0.95")

    def scaled(self, multiplier: Decimal) -> "ResearchCostConfig":
        return ResearchCostConfig(
            commission_rate=self.commission_rate * multiplier,
            minimum_commission=self.minimum_commission * multiplier,
            slippage_bps=self.slippage_bps * multiplier,
            stamp_tax_before=self.stamp_tax_before * multiplier,
            stamp_tax_after=self.stamp_tax_after * multiplier,
            stamp_change_date=self.stamp_change_date,
            target_exposure=self.target_exposure,
        )


@dataclass
class ResearchEngineResult:
    orders: list[dict[str, str]]
    trades: list[dict[str, str]]
    ledger: list[dict[str, str]]
    positions: list[dict[str, str]]


def commission(gross: Decimal, config: ResearchCostConfig) -> Decimal:
    if gross <= 0 or config.commission_rate <= 0:
        return Decimal("0")
    return money(max(config.minimum_commission, gross * config.commission_rate))


def stamp_tax(gross: Decimal, trade_date: str, config: ResearchCostConfig) -> Decimal:
    rate = config.stamp_tax_after if trade_date >= config.stamp_change_date else config.stamp_tax_before
    return money(gross * rate)


def slippage_cost(gross: Decimal, config: ResearchCostConfig) -> Decimal:
    return money(gross * config.slippage_bps / Decimal("10000"))


def minimum_order_notional(code: str, raw_open: Decimal) -> Decimal:
    minimum_shares = 200 if code.startswith("688") else 100
    return money(raw_open * minimum_shares)


def execution_rejection(row: dict[str, str] | None, side: str) -> str:
    if row is None:
        return "MISSING_MARKET_ROW"
    raw_open = number(row.get("open", ""))
    qfq_open = number(row.get("open_qfq", ""))
    volume = number(row.get("volume", ""))
    if row.get("tradestatus") != "1" or volume is None or volume <= 0:
        return "SUSPENDED_OR_ZERO_VOLUME"
    if raw_open is None or raw_open <= 0 or qfq_open is None or qfq_open <= 0:
        return "MISSING_OR_NONPOSITIVE_OPEN"
    raw_high = number(row.get("high", ""))
    raw_low = number(row.get("low", ""))
    pct_chg = number(row.get("pct_chg", ""))
    if raw_high is not None and raw_low is not None and pct_chg is not None:
        one_price = raw_open == raw_high == raw_low
        if side == "BUY" and one_price and pct_chg > Decimal("4.5"):
            return "ONE_PRICE_LIMIT_UP"
        if side == "SELL" and one_price and pct_chg < Decimal("-4.5"):
            return "ONE_PRICE_LIMIT_DOWN"
    return ""


def affordable_buy_gross(cash: Decimal, requested: Decimal, config: ResearchCostConfig) -> Decimal:
    gross = min(cash, money(requested))
    for _ in range(3):
        total = gross + commission(gross, config) + slippage_cost(gross, config)
        if total <= cash:
            return money(gross)
        excess = total - cash
        gross = max(Decimal("0"), (gross - excess - MONEY).quantize(MONEY, rounding=ROUND_DOWN))
    return Decimal("0")


def run_research_engine(
    calendar_dates: Iterable[str],
    market_rows: Iterable[dict[str, str]],
    target_schedule: dict[str, list[str]],
    *,
    initial_equity: Decimal = Decimal("1000000"),
    costs: ResearchCostConfig = ResearchCostConfig(),
    market_row_provider: Callable[[str, str], dict[str, str] | None] | None = None,
    event_evidence: dict[str, dict] | None = None,
    execution_rejection_fn: Callable[[dict[str, str] | None, str], str] = execution_rejection,
) -> ResearchEngineResult:
    calendar = sorted(set(calendar_dates))
    market: dict[str, dict[str, dict[str, str]]] = {}
    for row in market_rows:
        market.setdefault(row["trade_date"], {})[row["code"]] = row

    def market_row(code: str, trade_date: str) -> dict[str, str] | None:
        if market_row_provider is not None:
            return market_row_provider(code, trade_date)
        return market.get(trade_date, {}).get(code)

    cash = money(initial_equity)
    holdings: dict[str, Decimal] = {}
    reference_close: dict[str, Decimal] = {}
    stale_days: dict[str, int] = {}
    pending_signal_date: str | None = None
    pending_targets: list[str] | None = None
    orders: list[dict[str, str]] = []
    trades: list[dict[str, str]] = []
    ledger: list[dict[str, str]] = []
    positions: list[dict[str, str]] = []

    def record_order(
        signal_date: str,
        trade_date: str,
        code: str,
        side: str,
        requested: Decimal,
        status: str,
        reason: str,
        gross: Decimal = Decimal("0"),
        fee: Decimal = Decimal("0"),
        tax: Decimal = Decimal("0"),
        slip: Decimal = Decimal("0"),
    ) -> None:
        order_id = f"O{len(orders) + 1:07d}"
        orders.append({
            "order_id": order_id,
            "signal_date": signal_date,
            "order_date": trade_date,
            "code": code,
            "side": side,
            "requested_notional": text(requested),
            "status": status,
            "reason": reason,
        })
        if status == "FILLED":
            cash_change = gross - slip - fee - tax if side == "SELL" else -gross - slip - fee
            trades.append({
                "trade_id": f"T{len(trades) + 1:07d}",
                "order_id": order_id,
                "signal_date": signal_date,
                "trade_date": trade_date,
                "code": code,
                "side": side,
                "gross_notional": text(gross),
                "slippage_cost": text(slip),
                "commission": text(fee),
                "stamp_tax": text(tax),
                "cash_change": text(cash_change),
            })

    for trade_date in calendar:
        needed_codes = set(holdings)
        if pending_targets is not None:
            needed_codes.update(pending_targets)
        day_market = {code: market_row(code, trade_date) for code in needed_codes}
        open_reference: dict[str, Decimal] = {}

        for code in list(holdings):
            row = day_market.get(code)
            qfq_open = number(row.get("open_qfq", "")) if row else None
            prior_close = reference_close.get(code)
            if qfq_open is not None and qfq_open > 0 and prior_close is not None and prior_close > 0:
                holdings[code] = money(holdings[code] * qfq_open / prior_close)
                open_reference[code] = qfq_open
                stale_days[code] = 0
            else:
                open_reference[code] = prior_close or Decimal("0")
                stale_days[code] = stale_days.get(code, 0) + 1
                evidence = (event_evidence or {}).get(code)
                if evidence and evidence.get("last_tradable_date") not in (None, "UNKNOWN") and trade_date > evidence["last_tradable_date"]:
                    raise DataGateBlocked(code, trade_date, stale_days[code], "UNSETTLED_CORPORATE_ACTION")
                if stale_days[code] > 60:
                    raise DataGateBlocked(code, trade_date, stale_days[code])

        if pending_targets is not None and pending_signal_date is not None:
            unique_targets = sorted(set(pending_targets))
            if len(unique_targets) != len(pending_targets):
                raise ValueError(f"{pending_signal_date} 目标股票重复")
            equity_open = money(cash + sum(holdings.values(), Decimal("0")))
            desired = money(equity_open * costs.target_exposure / Decimal(len(unique_targets)))

            for code in sorted(list(holdings)):
                target_value = desired if code in unique_targets else Decimal("0")
                requested = money(max(Decimal("0"), holdings[code] - target_value))
                if requested < MONEY:
                    continue
                row = day_market.get(code)
                reason = execution_rejection_fn(row, "SELL")
                if reason:
                    record_order(pending_signal_date, trade_date, code, "SELL", requested, "REJECTED", reason)
                    continue
                gross = min(requested, holdings[code])
                slip = slippage_cost(gross, costs)
                fee = commission(gross, costs)
                tax = stamp_tax(gross, trade_date, costs)
                holdings[code] = money(holdings[code] - gross)
                cash = money(cash + gross - slip - fee - tax)
                if holdings[code] <= 0:
                    holdings.pop(code, None)
                    reference_close.pop(code, None)
                    stale_days.pop(code, None)
                    open_reference.pop(code, None)
                record_order(
                    pending_signal_date, trade_date, code, "SELL", requested, "FILLED", "",
                    gross=gross, fee=fee, tax=tax, slip=slip,
                )

            for code in unique_targets:
                current = holdings.get(code, Decimal("0"))
                requested = money(max(Decimal("0"), desired - current))
                if requested < MONEY:
                    continue
                row = day_market.get(code)
                reason = execution_rejection_fn(row, "BUY")
                raw_open = number(row.get("open", "")) if row else None
                qfq_open = number(row.get("open_qfq", "")) if row else None
                if not reason and raw_open is not None and requested < minimum_order_notional(code, raw_open):
                    reason = "BELOW_MINIMUM_ORDER_NOTIONAL"
                if reason:
                    record_order(pending_signal_date, trade_date, code, "BUY", requested, "REJECTED", reason)
                    continue
                gross = affordable_buy_gross(cash, requested, costs)
                if raw_open is None or gross < minimum_order_notional(code, raw_open):
                    record_order(
                        pending_signal_date, trade_date, code, "BUY", requested, "REJECTED",
                        "INSUFFICIENT_CASH_FOR_MINIMUM_ORDER",
                    )
                    continue
                slip = slippage_cost(gross, costs)
                fee = commission(gross, costs)
                cash = money(cash - gross - slip - fee)
                holdings[code] = money(current + gross)
                if qfq_open is None or qfq_open <= 0:
                    raise RuntimeError(f"{code} 可成交买入却没有前复权开盘价")
                reference_close[code] = qfq_open
                open_reference[code] = qfq_open
                stale_days[code] = 0
                record_order(
                    pending_signal_date, trade_date, code, "BUY", requested, "FILLED", "",
                    gross=gross, fee=fee, slip=slip,
                )

        for code in list(holdings):
            row = day_market.get(code)
            qfq_close = number(row.get("close_qfq", "")) if row else None
            base = open_reference.get(code) or reference_close.get(code)
            if qfq_close is not None and qfq_close > 0 and base is not None and base > 0:
                holdings[code] = money(holdings[code] * qfq_close / base)
                reference_close[code] = qfq_close

        holdings_value = money(sum(holdings.values(), Decimal("0")))
        equity = money(cash + holdings_value)
        if equity <= 0:
            raise RuntimeError(f"{trade_date} 研究组合权益非正")
        for code in sorted(holdings):
            positions.append({
                "trade_date": trade_date,
                "code": code,
                "research_value": text(holdings[code]),
                "weight": format(holdings[code] / equity, ".12f"),
                "qfq_reference_close": format(reference_close[code], "f"),
                "stale_days": str(stale_days.get(code, 0)),
            })

        current_targets = target_schedule.get(trade_date)
        pending_signal_date = trade_date if current_targets is not None else None
        pending_targets = list(current_targets) if current_targets is not None else None
        ledger.append({
            "trade_date": trade_date,
            "research_cash": text(cash),
            "holdings_value": text(holdings_value),
            "equity": text(equity),
            "gross_exposure": format(holdings_value / equity, ".12f"),
            "holding_count": str(len(holdings)),
            "signal_at_close": "true" if current_targets is not None else "false",
            "accounting_scope": "RESEARCH_PORTFOLIO",
        })

    return ResearchEngineResult(orders=orders, trades=trades, ledger=ledger, positions=positions)
