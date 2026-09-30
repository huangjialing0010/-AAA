"""现金账户与分红子账集成；到账可交易时点必须由上层确认。"""
from dataclasses import dataclass, replace
from datetime import date
from .dividend_ledger import DividendState, advance_dividends
from .share_account import ShareAccount, book_fill, mark_equity


@dataclass(frozen=True)
class CashDividendAccount:
    shares: ShareAccount
    dividends: DividendState = DividendState()
    as_of: date | None = None


def apply_dividends(account, day, entitlements):
    if account.as_of is not None and day < account.as_of:
        raise ValueError('不能向过去的账户入账分红')
    dividend_state, audit = advance_dividends(account.dividends, day, entitlements)
    delta = dividend_state.cash_received - account.dividends.cash_received
    # 已成交后不得补记同日新增分红，否则无法证明成交当时现金可用。
    if audit and account.shares.fills and account.shares.fills[-1].day >= day:
        raise ValueError('当日成交后不能补录分红，请按时序重放')
    shares = replace(account.shares, cash=account.shares.cash + delta)
    return CashDividendAccount(shares, dividend_state, day), audit


def apply_fill(account, fill):
    if account.as_of is not None and fill.day < account.as_of:
        raise ValueError('不能用后续现金记录早期成交')
    shares, sales = book_fill(account.shares, fill)
    return CashDividendAccount(shares, account.dividends, fill.day), sales


def valuation(account, prices):
    values = mark_equity(account.shares, prices)
    return {**values, 'dividend_receivable': account.dividends.receivable,
            'equity_before_unresolved_tax': values['cash_plus_stocks'] + account.dividends.receivable,
            'tax_status': 'NOT_ASSESSED', 'live_admission': 'BLOCKED'}
