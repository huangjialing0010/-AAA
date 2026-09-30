"""串行新开仓批次的确定性规划；不提交订单，不处理加仓。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN

from .manual_rules import VERSION, number


@dataclass(frozen=True)
class PositionValue:
    code: str
    industry: str
    market_value: Decimal


def _amount(value):
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def plan_openings(*, signals, positions, cash, as_of, monthly_buys,
                  pending_buy_count, decision_window_verified):
    """行业、估值与计数需已核验；signals每行包含code/industry/industry_as_of/decision。"""
    if not _amount(cash) or type(as_of) is not date:
        raise ValueError('现金或决策日期无效')
    if any(type(v) is not int or v < 0 for v in (monthly_buys, pending_buy_count)):
        raise ValueError('买入计数必须为非负整数')
    if len({p.code for p in positions}) != len(positions):
        raise ValueError('持仓代码重复')
    if any(not p.industry.strip() or not _amount(p.market_value) or p.market_value == 0 for p in positions):
        raise ValueError('持仓缺少行业或有效市值')
    signals = list(signals)
    codes = [s['code'] for s in signals]
    if len(set(codes)) != len(codes) or any(len(c) != 6 or not c.isdigit() for c in codes):
        raise ValueError('信号代码重复或无效')
    result = {'plans': [], 'rejections': [], 'version': VERSION, 'as_of': as_of.isoformat()}

    def reject(code, reason):
        result['rejections'].append({'code': code, 'reason': reason})

    if decision_window_verified is not True or pending_buy_count:
        reason = 'PENDING_BUYS_MUST_RESOLVE' if pending_buy_count else 'NOT_VERIFIED_DECISION_WINDOW'
        for code in sorted(codes):
            reject(code, reason)
        return result
    eligible = []
    for row in signals:
        d = row.get('decision', {})
        if (d.get('action') != 'BUY_SIGNAL' or d.get('version') != VERSION
                or d.get('signal_day') != as_of.isoformat() or not d.get('evidence_id')
                or d.get('mechanism') not in ('L1', 'R1')):
            reject(row['code'], 'SIGNAL_NOT_ADMITTED')
        elif not row.get('industry', '').strip() or row.get('industry_as_of') != as_of.isoformat():
            reject(row['code'], 'INDUSTRY_NOT_VERIFIED')
        elif number(d.get('roe_3y_median')) is None or number(d.get('cash_profit_ratio')) is None:
            reject(row['code'], 'RANKING_EVIDENCE_MISSING')
        else:
            eligible.append(row)
    eligible.sort(key=lambda r: (-r['decision']['roe_3y_median'], -r['decision']['cash_profit_ratio'], r['code']))
    held = {p.code for p in positions}
    industries = {p.industry for p in positions}
    market_value = sum((p.market_value for p in positions), Decimal(0))
    equity = cash + market_value
    reserved = Decimal(0)
    count = len(held)
    for row in eligible:
        code, industry, decision = row['code'], row['industry'], row['decision']
        if code in held:
            reject(code, 'ALREADY_HELD_ADD_NOT_IMPLEMENTED')
        elif industry in industries:
            reject(code, 'INDUSTRY_LIMIT')
        elif count >= 4:
            reject(code, 'POSITION_LIMIT')
        elif monthly_buys + len(result['plans']) >= 2:
            reject(code, 'MONTHLY_BUY_LIMIT')
        else:
            budget = min(equity * Decimal('.10'), cash - reserved,
                         equity * Decimal('.80') - market_value - reserved)
            budget = max(Decimal(0), budget).quantize(Decimal('.01'), rounding=ROUND_DOWN)
            if budget <= 0:
                reject(code, 'NO_AVAILABLE_BUDGET')
                continue
            result['plans'].append({'plan_id': f'{VERSION}:{as_of.isoformat()}:{code}:OPEN',
                                    'code': code, 'industry': industry,
                                    'mechanism': decision['mechanism'],
                                    'signal_day': as_of.isoformat(), 'budget': str(budget),
                                    'signal_evidence_id': decision['evidence_id'],
                                    'status': 'PLANNED_NOT_EXECUTED'})
            reserved += budget
            industries.add(industry)
            held.add(code)
            count += 1
    result.update(equity=str(equity), reserved_budget=str(reserved), unreserved_cash=str(cash-reserved))
    return result
