"""从登记日收盘批次快照生成分红资格；上层负责保证快照已完成当日成交。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from .share_lots import ShareLot, validate_lots
from .dividend_ledger import DividendEntitlement


@dataclass(frozen=True)
class RegisteredDividend:
    entitlement: DividendEntitlement
    eligible_lots: tuple[ShareLot, ...]
    close_snapshot_id: str


def register_dividend(*, event_id: str, code: str, register_date: date,
                      ex_date: date, pay_date: date, gross_per_share: Decimal,
                      payment_per_share: Decimal, evidence_id: str,
                      snapshot_date: date, close_snapshot_id: str, lots):
    if snapshot_date != register_date or not close_snapshot_id:
        raise ValueError('必须提供登记日收盘快照身份及日期')
    lots = tuple(lots)
    validate_lots(lots)
    if any(lot.acquired_on > snapshot_date for lot in lots):
        raise ValueError('登记日快照不能包含未来持仓')
    eligible = tuple(sorted((lot for lot in lots if lot.code == code),
                            key=lambda lot: (lot.acquired_on, lot.sequence)))
    entitlement = DividendEntitlement(event_id, code, register_date, ex_date, pay_date,
        sum(lot.shares for lot in eligible), gross_per_share, payment_per_share, evidence_id)
    return RegisteredDividend(entitlement, eligible, close_snapshot_id)
