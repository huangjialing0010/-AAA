"""单笔研究信号接入既有交易检查和账本，不连接券商。"""
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .checked_execution import ExecutionDecision, apply_checked_execution
from .manual_order_sizing import size_opening_buy
from .share_account import Fill


def execute_signal(*, account, action, mechanism, code, order_id, signal_day,
                   execution_day, next_trade_day, rules, tradable,
                   open_price, low_price, high_price, slippage_rate, fees,
                   single_budget, portfolio_budget, portfolio_check_passed,
                   corporate_actions_verified, signal_evidence_id, execution_evidence_id):
    """预算与证据由上层提供；拒绝时返回原账户，不建立虚假成交。"""
    def reject(reason):
        return account, ExecutionDecision(order_id, 'REJECTED', reason,
                                          rules.evidence_id if rules else '', execution_evidence_id), ()

    if any(f.fill_id == order_id for f in account.shares.fills):
        return reject('ORDER_ID_ALREADY_USED')
    if not order_id or not signal_evidence_id or mechanism not in ('L1', 'R1'):
        return reject('MISSING_SIGNAL_IDENTITY')
    if action not in ('BUY_SIGNAL', 'SELL_SIGNAL'):
        return reject('NO_EXECUTABLE_SIGNAL')
    if corporate_actions_verified is not True:
        return reject('CORPORATE_ACTIONS_NOT_VERIFIED')
    if action == 'BUY_SIGNAL' and portfolio_check_passed is not True:
        return reject('PORTFOLIO_NOT_ADMITTED')
    if type(signal_day) is not date or type(execution_day) is not date:
        return reject('INVALID_DATE')
    if signal_day >= execution_day or next_trade_day.get(signal_day) != execution_day:
        return reject('ORDER_NOT_NEXT_TRADING_DAY')
    if rules is None:
        return reject('MISSING_VERIFIED_RULES')
    if any(not isinstance(v, Decimal) or not v.is_finite() or v <= 0
           for v in (open_price, low_price, high_price)) or not low_price <= open_price <= high_price:
        return reject('INVALID_EXECUTION_BAR')
    if not isinstance(slippage_rate, Decimal) or not slippage_rate.is_finite() or not 0 <= slippage_rate < 1:
        return reject('INVALID_SLIPPAGE')
    side = 'BUY' if action == 'BUY_SIGNAL' else 'SELL'
    direction = Decimal(1) if side == 'BUY' else Decimal(-1)
    price = (open_price * (1 + direction * slippage_rate)).quantize(
        Decimal('.01'), rounding=ROUND_CEILING if side == 'BUY' else ROUND_FLOOR)
    if not low_price <= price <= high_price:
        return reject('SLIPPED_PRICE_OUTSIDE_BAR')
    lots = [lot for lot in account.shares.lots if lot.code == code]
    try:
        if side == 'BUY':
            if lots:
                return reject('ADD_POSITION_NOT_IMPLEMENTED')
            size = size_opening_buy(cash=account.shares.cash, single_budget=single_budget,
                                    portfolio_budget=portfolio_budget, execution_price=price,
                                    minimum_buy=rules.minimum_buy, buy_increment=rules.buy_increment,
                                    fees=fees, day=execution_day)
            if size.shares == 0:
                return reject(size.status)
            shares = size.shares
            sellable = next_trade_day.get(execution_day)
            if type(sellable) is not date or sellable <= execution_day:
                return reject('SELLABLE_DATE_NOT_VERIFIED')
        else:
            shares = sum(lot.shares for lot in lots)
            if shares == 0:
                return reject('NO_POSITION')
            sellable = None
        charge = fees.calculate(side=side, price=price, shares=shares, day=execution_day)
        sequence = 1 + max((f.sequence for f in account.shares.fills if f.day == execution_day), default=-1)
        fill = Fill(order_id, code, side, execution_day, sequence, shares, price, charge, sellable)
        return apply_checked_execution(account, fill, signal_day=signal_day,
                                       rules=rules, tradable=tradable,
                                       execution_evidence_id=execution_evidence_id, fee_profile=fees)
    except (ValueError, TypeError, ArithmeticError) as error:
        return reject('INVALID_INPUT: ' + str(error))
