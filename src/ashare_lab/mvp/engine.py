"""单标的T+1撮合、费用、分红应收与每日账本。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


MONEY = Decimal("0.01")
PRICE = Decimal("0.000001")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def text(value: Decimal, quantum: Decimal = MONEY) -> str:
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


@dataclass(frozen=True)
class CostConfig:
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("5")
    sell_stamp_tax_rate: Decimal = Decimal("0")
    lot_size: int = 100

    @classmethod
    def zero(cls) -> "CostConfig":
        return cls(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            slippage_bps=Decimal("0"),
            sell_stamp_tax_rate=Decimal("0"),
            lot_size=100,
        )


@dataclass(frozen=True)
class DividendEvent:
    record_date: str
    ex_date: str
    payment_date: str
    cash_per_share: Decimal


@dataclass
class EngineResult:
    orders: list[dict[str, str]]
    trades: list[dict[str, str]]
    ledger: list[dict[str, str]]
    dividend_ledger: list[dict[str, str]]


def commission(gross: Decimal, config: CostConfig) -> Decimal:
    if gross <= 0 or config.commission_rate == 0:
        return Decimal("0")
    return money(max(config.minimum_commission, gross * config.commission_rate))


def buy_quantity(cash: Decimal, execution_price: Decimal, config: CostConfig) -> int:
    if execution_price <= 0 or cash <= 0:
        return 0
    quantity = int(cash / execution_price / config.lot_size) * config.lot_size
    while quantity > 0:
        gross = money(execution_price * quantity)
        if gross + commission(gross, config) <= cash:
            return quantity
        quantity -= config.lot_size
    return 0


def run_engine(
    market_rows: Iterable[dict[str, str]],
    signal_rows: Iterable[dict[str, str]],
    dividend_events: Iterable[DividendEvent],
    *,
    initial_cash: Decimal = Decimal("1000000"),
    costs: CostConfig = CostConfig(),
) -> EngineResult:
    markets = sorted((dict(row) for row in market_rows), key=lambda row: row["trade_date"])
    signals = {row["signal_date"]: dict(row) for row in signal_rows}
    events = list(dividend_events)
    event_by_record = {event.record_date: event for event in events}
    event_by_ex = {event.ex_date: event for event in events}
    events_by_payment: dict[str, list[DividendEvent]] = {}
    for event in events:
        events_by_payment.setdefault(event.payment_date, []).append(event)

    cash = money(initial_cash)
    shares = 0
    receivable = Decimal("0")
    last_buy_date: str | None = None
    entitlement_shares: dict[str, int] = {}
    receivable_by_event: dict[str, Decimal] = {}
    pending_signal: dict[str, str] | None = None
    orders: list[dict[str, str]] = []
    trades: list[dict[str, str]] = []
    ledger: list[dict[str, str]] = []
    dividend_ledger: list[dict[str, str]] = []

    for bar in markets:
        trade_date = bar["trade_date"]
        open_price = Decimal(bar["open"])
        close_price = Decimal(bar["close"])
        volume = Decimal(bar["volume"])

        for event in events_by_payment.get(trade_date, []):
            amount = receivable_by_event.pop(event.ex_date, Decimal("0"))
            receivable = money(receivable - amount)
            cash = money(cash + amount)
            dividend_ledger.append({
                "event_date": trade_date,
                "event_type": "PAYMENT",
                "record_date": event.record_date,
                "ex_date": event.ex_date,
                "payment_date": event.payment_date,
                "entitled_shares": str(entitlement_shares.get(event.ex_date, 0)),
                "cash_per_share": text(event.cash_per_share, PRICE),
                "amount": text(amount),
            })

        event = event_by_ex.get(trade_date)
        if event is not None:
            entitled = entitlement_shares.get(event.ex_date, 0)
            amount = money(event.cash_per_share * entitled)
            receivable_by_event[event.ex_date] = amount
            receivable = money(receivable + amount)
            dividend_ledger.append({
                "event_date": trade_date,
                "event_type": "ENTITLEMENT",
                "record_date": event.record_date,
                "ex_date": event.ex_date,
                "payment_date": event.payment_date,
                "entitled_shares": str(entitled),
                "cash_per_share": text(event.cash_per_share, PRICE),
                "amount": text(amount),
            })

        if pending_signal is not None:
            target = int(pending_signal["target_position"])
            needs_buy = target == 1 and shares == 0
            needs_sell = target == 0 and shares > 0
            if needs_buy or needs_sell:
                side = "BUY" if needs_buy else "SELL"
                order_id = f"O{len(orders) + 1:05d}"
                reason = ""
                status = "FILLED"
                requested_quantity = 0
                if open_price <= 0:
                    status, reason = "REJECTED", "MISSING_OR_NONPOSITIVE_OPEN"
                elif volume <= 0:
                    status, reason = "REJECTED", "ZERO_VOLUME_OR_SUSPENDED"
                elif needs_sell and last_buy_date is not None and trade_date <= last_buy_date:
                    status, reason = "REJECTED", "T_PLUS_ONE_NOT_SELLABLE"

                if status == "FILLED" and needs_buy:
                    execution_price = (open_price * (Decimal("1") + costs.slippage_bps / Decimal("10000"))).quantize(
                        PRICE, rounding=ROUND_HALF_UP
                    )
                    requested_quantity = buy_quantity(cash, execution_price, costs)
                    if requested_quantity == 0:
                        status, reason = "REJECTED", "INSUFFICIENT_CASH_FOR_ONE_LOT"
                    else:
                        gross = money(execution_price * requested_quantity)
                        fee = commission(gross, costs)
                        tax = Decimal("0")
                        cash = money(cash - gross - fee - tax)
                        shares += requested_quantity
                        last_buy_date = trade_date
                elif status == "FILLED" and needs_sell:
                    execution_price = (open_price * (Decimal("1") - costs.slippage_bps / Decimal("10000"))).quantize(
                        PRICE, rounding=ROUND_HALF_UP
                    )
                    requested_quantity = shares
                    gross = money(execution_price * requested_quantity)
                    fee = commission(gross, costs)
                    tax = money(gross * costs.sell_stamp_tax_rate)
                    cash = money(cash + gross - fee - tax)
                    shares = 0
                    last_buy_date = None

                order = {
                    "order_id": order_id,
                    "signal_date": pending_signal["signal_date"],
                    "order_date": trade_date,
                    "side": side,
                    "target_position": str(target),
                    "requested_quantity": str(requested_quantity),
                    "status": status,
                    "reason": reason,
                }
                orders.append(order)
                if status == "FILLED":
                    trade_id = f"T{len(trades) + 1:05d}"
                    cash_change = -gross - fee - tax if side == "BUY" else gross - fee - tax
                    trades.append({
                        "trade_id": trade_id,
                        "order_id": order_id,
                        "signal_date": pending_signal["signal_date"],
                        "trade_date": trade_date,
                        "side": side,
                        "quantity": str(requested_quantity),
                        "execution_price": text(execution_price, PRICE),
                        "gross_amount": text(gross),
                        "commission": text(fee),
                        "stamp_tax": text(tax),
                        "cash_change": text(cash_change),
                    })

        record_event = event_by_record.get(trade_date)
        if record_event is not None:
            entitlement_shares[record_event.ex_date] = shares

        market_value = money(close_price * shares)
        equity = money(cash + receivable + market_value)
        current_signal = signals.get(trade_date)
        pending_signal = current_signal
        ledger.append({
            "trade_date": trade_date,
            "cash": text(cash),
            "dividend_receivable": text(receivable),
            "shares": str(shares),
            "close": text(close_price, PRICE),
            "market_value": text(market_value),
            "equity": text(equity),
            "signal_target_at_close": current_signal["target_position"] if current_signal else "",
            "pending_execution_after_close": "true" if current_signal else "false",
        })
    return EngineResult(orders=orders, trades=trades, ledger=ledger, dividend_ledger=dividend_ledger)
