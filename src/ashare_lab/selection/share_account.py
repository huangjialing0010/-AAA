"""已验证成交的现金/整数持仓记账；不是撮合器或实盘接口。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from .share_lots import ShareLot, consume_lots


@dataclass(frozen=True)
class Fill:
    fill_id: str
    code: str
    side: str
    day: date
    sequence: int
    shares: int
    price: Decimal
    fees: Decimal
    sellable_on: date | None = None

    def __post_init__(self):
        if not self.fill_id or len(self.code) != 6 or not self.code.isdigit():
            raise ValueError('成交身份无效')
        if self.side not in ('BUY', 'SELL'):
            raise ValueError('成交方向无效')
        if type(self.shares) is not int or self.shares <= 0:
            raise ValueError('成交股数必须为正整数')
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError('成交顺序无效')
        if not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0:
            raise ValueError('成交价格无效')
        if not isinstance(self.fees, Decimal) or not self.fees.is_finite() or self.fees < 0:
            raise ValueError('成交费用无效')
        if self.side == 'BUY' and (self.sellable_on is None or self.sellable_on <= self.day):
            raise ValueError('买入必须指定未来可卖日')
        if self.side == 'SELL' and self.sellable_on is not None:
            raise ValueError('卖出不能新增可卖日')


@dataclass(frozen=True)
class ShareAccount:
    cash: Decimal
    lots: tuple[ShareLot, ...] = ()
    fills: tuple[Fill, ...] = ()

    def __post_init__(self):
        if not isinstance(self.cash, Decimal) or not self.cash.is_finite() or self.cash < 0:
            raise ValueError('现金必须是非负明确金额')


def book_fill(account: ShareAccount, fill: Fill):
    """返回新账户及卖出批次；失败不改变原账户，完全相同的成交可幂等重放。"""
    for prior in account.fills:
        if prior.fill_id == fill.fill_id:
            if prior != fill:
                raise ValueError('相同成交ID内容冲突')
            return account, ()
    if account.fills and (fill.day, fill.sequence) <= (account.fills[-1].day, account.fills[-1].sequence):
        raise ValueError('成交必须按日期及日内顺序入账')
    gross = fill.price * fill.shares
    if fill.side == 'BUY':
        cash = account.cash - gross - fill.fees
        if cash < 0:
            raise ValueError('现金不足')
        lot = ShareLot(fill.fill_id, fill.code, fill.day, fill.sellable_on, fill.shares, fill.sequence)
        lots, sales = account.lots + (lot,), ()
    else:
        lots, sales = consume_lots(account.lots, fill.code, fill.day, fill.shares)
        cash = account.cash + gross - fill.fees
        if cash < 0:
            raise ValueError('卖出费用导致现金不足')
    return ShareAccount(cash, lots, account.fills + (fill,)), sales


def mark_equity(account, prices):
    """只计算现金与股票市值；应收和税款由账户汇总层另行计入。"""
    market_value = Decimal('0')
    for lot in account.lots:
        price = prices.get(lot.code)
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise ValueError('持仓缺少有效估值')
        market_value += price * lot.shares
    return {'cash': account.cash, 'stock_value': market_value,
            'cash_plus_stocks': account.cash + market_value}
