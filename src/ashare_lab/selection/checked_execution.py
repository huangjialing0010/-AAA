"""将明确的候选成交接入前置检查和账户；不产生市场成交价格。"""
from dataclasses import dataclass
from .order_gate import check_order
from .cash_dividend_account import apply_fill


@dataclass(frozen=True)
class ExecutionDecision:
    fill_id: str
    status: str
    reason: str
    rule_evidence_id: str
    execution_evidence_id: str


def apply_checked_execution(account, fill, *, signal_day, rules, tradable,
                            execution_evidence_id, fee_profile=None):
    """原子地检查并记账，返回决策；调用者须持久保存决策与候选输入。"""
    rule_id = rules.evidence_id if rules is not None else ''
    def reject(reason):
        return account, ExecutionDecision(fill.fill_id, 'REJECTED', reason,
                                          rule_id, execution_evidence_id), ()
    if not execution_evidence_id:
        return reject('MISSING_EXECUTION_EVIDENCE')
    if fee_profile is not None:
        try:
            expected_fees = fee_profile.calculate(
                side=fill.side, price=fill.price, shares=fill.shares, day=fill.day
            )
        except (TypeError, ValueError):
            return reject('INVALID_FEE_PROFILE')
        if fill.fees != expected_fees:
            return reject('FEE_MISMATCH')
    total = sum(l.shares for l in account.shares.lots if l.code == fill.code)
    available = sum(l.shares for l in account.shares.lots
                    if l.code == fill.code and l.sellable_on <= fill.day)
    # 已记入成交的重复请求不按当前持仓重新检查，以免重复卖出误报超卖。
    prior = next((f for f in account.shares.fills if f.fill_id == fill.fill_id), None)
    if prior is not None:
        if prior != fill:
            return reject('FILL_ID_CONFLICT')
        return account, ExecutionDecision(fill.fill_id, 'ALREADY_BOOKED', 'NO_STATE_CHANGE',
                                          rule_id, execution_evidence_id), ()
    reason = check_order(code=fill.code, side=fill.side, shares=fill.shares,
                         signal_day=signal_day, execution_day=fill.day, price=fill.price,
                         tradable=tradable, rules=rules, available_shares=available,
                         total_shares=total)
    if reason != 'PRECHECK_PASS_NOT_FILL':
        return reject(reason)
    try:
        updated, sales = apply_fill(account, fill)
    except ValueError as error:
        return reject('ACCOUNT_REJECTED: ' + str(error))
    return updated, ExecutionDecision(fill.fill_id, 'BOOKED', 'CANDIDATE_EXECUTION_BOOKED',
                                      rule_id, execution_evidence_id), sales
