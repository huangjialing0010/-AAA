"""真实整数股数的批次记录；成交规则和交易日由上层已验证输入提供。"""
from dataclasses import dataclass, replace
from datetime import date


@dataclass(frozen=True)
class ShareLot:
    lot_id: str
    code: str
    acquired_on: date
    sellable_on: date
    shares: int
    sequence: int

    def __post_init__(self):
        if not self.lot_id or len(self.code) != 6 or not self.code.isdigit():
            raise ValueError('批次或证券身份无效')
        if type(self.shares) is not int or self.shares <= 0:
            raise ValueError('批次必须为正整数股数')
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError('同日批次顺序无效')
        if self.sellable_on <= self.acquired_on:
            raise ValueError('普通买入批次不得当天可卖')


def validate_lots(lots):
    if len({lot.lot_id for lot in lots}) != len(lots):
        raise ValueError('批次身份重复')
    keys = [(lot.code, lot.acquired_on, lot.sequence) for lot in lots]
    if len(set(keys)) != len(keys):
        raise ValueError('同股同日批次顺序重复')


def consume_lots(lots, code, day, shares):
    """按买入日及同日成交顺序扣减可卖批次；不足则原子拒绝。"""
    lots = tuple(lots)
    validate_lots(lots)
    if type(shares) is not int or shares <= 0:
        raise ValueError('卖出数量必须为正整数')
    if any(lot.acquired_on > day for lot in lots):
        raise ValueError('持仓含未来买入批次')
    eligible = sorted((lot for lot in lots if lot.code == code and lot.sellable_on <= day),
                      key=lambda lot: (lot.acquired_on, lot.sequence))
    if sum(lot.shares for lot in eligible) < shares:
        raise ValueError('可卖股数不足')
    remaining = shares
    deductions = {}
    sales = []
    for lot in eligible:
        count = min(remaining, lot.shares)
        if not count:
            break
        deductions[lot.lot_id] = count
        sales.append({'lot_id': lot.lot_id, 'code': code, 'acquired_on': lot.acquired_on,
                      'sold_on': day, 'shares': count})
        remaining -= count
    updated = tuple(replace(lot, shares=lot.shares - deductions.get(lot.lot_id, 0))
                    for lot in lots if lot.shares > deductions.get(lot.lot_id, 0))
    return updated, tuple(sales)
