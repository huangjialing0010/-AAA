"""低频研究规则：输入为已核验证据，不包含行情抓取或真实订单。"""
from dataclasses import dataclass
from datetime import date
import math
from statistics import median

from .current_watchlist import evaluate_candidate

VERSION = 'MANUAL_RULE_ENGINE_V1'


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class Annual:
    year: int
    available_on: date
    parent_profit: float | None
    operating_cashflow: float | None
    roe_percent: float | None


@dataclass(frozen=True)
class QualityEvidence:
    evidence_id: str
    checked_on: date
    mainboard: bool | None
    nonfinancial: bool | None
    listed_on: date
    is_st: bool | None
    standard_audit: bool | None
    material_risk: bool | None
    annuals: tuple[Annual, ...]
    ttm_parent_profit: float | None
    ttm_available_on: date
    median_amount_20d: float | None
    stock_code: str = ''


def quality_reasons(evidence: QualityEvidence, signal_day: date):
    reasons = []
    if not evidence.evidence_id.strip() or evidence.checked_on != signal_day:
        reasons.append('MISSING_CURRENT_EVIDENCE')
    for value, expected, reason in (
        (evidence.mainboard, True, 'MAINBOARD_NOT_CONFIRMED'),
        (evidence.nonfinancial, True, 'NONFINANCIAL_NOT_CONFIRMED'),
        (evidence.is_st, False, 'NON_ST_NOT_CONFIRMED'),
        (evidence.standard_audit, True, 'STANDARD_AUDIT_NOT_CONFIRMED'),
        (evidence.material_risk, False, 'NO_MATERIAL_RISK_NOT_CONFIRMED'),
    ):
        if value is not expected:
            reasons.append(reason)
    try:
        third_anniversary = evidence.listed_on.replace(year=evidence.listed_on.year + 3)
    except ValueError:
        third_anniversary = evidence.listed_on.replace(year=evidence.listed_on.year + 3, day=28)
    if signal_day < third_anniversary:
        reasons.append('LISTING_LESS_THAN_THREE_YEARS')
    annuals = evidence.annuals
    if len(annuals) != 3 or {a.year for a in annuals} != set(range(signal_day.year - 3, signal_day.year)):
        reasons.append('ANNUAL_WINDOW_INVALID')
    if any(a.available_on > signal_day or a.available_on <= date(a.year, 12, 31) for a in annuals):
        reasons.append('ANNUAL_AVAILABILITY_INVALID')
    profits = [number(a.parent_profit) for a in annuals]
    cash = [number(a.operating_cashflow) for a in annuals]
    roe = [number(a.roe_percent) for a in annuals]
    if len(annuals) != 3 or any(v is None for v in profits + cash + roe):
        reasons.append('MISSING_TOTAL_FINANCIAL_VALUES')
    else:
        if any(v <= 0 for v in profits):
            reasons.append('ANNUAL_PROFIT_FAILED')
        if sum(v > 0 for v in cash) < 2:
            reasons.append('OPERATING_CASHFLOW_FAILED')
        if sum(profits) <= 0 or sum(cash) / sum(profits) < .8:
            reasons.append('CASH_PROFIT_RATIO_FAILED')
        if median(roe) < 10:
            reasons.append('ROE_FAILED')
    ttm = number(evidence.ttm_parent_profit)
    if evidence.ttm_available_on > signal_day or ttm is None or ttm <= 0:
        reasons.append('TTM_PROFIT_UNAVAILABLE_OR_FAILED')
    amount = number(evidence.median_amount_20d)
    if amount is None or amount < 20000000:
        reasons.append('LIQUIDITY_UNAVAILABLE_OR_FAILED')
    return tuple(reasons)


def entry_decision(*, evidence, rows, calendar, signal_day, market_day=None):
    reasons = quality_reasons(evidence, signal_day)
    if reasons:
        return {'version': VERSION, 'action': 'BLOCKED', 'reasons': reasons, 'mechanism': ''}
    market_day = signal_day if market_day is None else market_day
    if market_day > signal_day:
        raise ValueError('市场数据不能晚于信号生成日')
    rows = list(rows)
    if evidence.stock_code and any(row.get('code', '').split('.')[-1] != evidence.stock_code for row in rows):
        raise ValueError('行情与财务证据证券代码不一致')
    result = evaluate_candidate(rows, calendar, market_day.isoformat())
    return {'version': VERSION,
            'action': 'BUY_SIGNAL' if result['status'] == 'BASELINE_TRIGGER' else result['status'],
            'reasons': tuple(filter(None, result.get('review_reasons', '').split(';'))),
            'mechanism': result['mechanism'], 'evidence_id': evidence.evidence_id,
            'roe_3y_median': median(a.roe_percent for a in evidence.annuals),
            'cash_profit_ratio': sum(a.operating_cashflow for a in evidence.annuals) / sum(a.parent_profit for a in evidence.annuals),
            'signal_day': signal_day.isoformat(), 'metrics': result}


def exit_decision(*, mechanism, hard_risk, quality_valid, adjusted_total_return,
                  held_trading_days, pe_percentile=None, entry_ttm_profit=None,
                  current_ttm_profit=None, consecutive_weekly_below_ma60=None):
    """调用者保证收益含公司行动、周末序列连续、交易日计数已验证。"""
    if mechanism not in ('L1', 'R1'):
        raise ValueError('入场标签必须为L1或R1')
    if type(held_trading_days) is not int or held_trading_days < 0:
        raise ValueError('持有交易日数必须为非负整数')
    if hard_risk is True or quality_valid is False:
        return 'SELL_SIGNAL', 'RISK_OR_QUALITY_EXIT'
    total_return = number(adjusted_total_return)
    if total_return is not None and total_return <= -.12:
        return 'SELL_SIGNAL', 'STOP_LOSS'
    if held_trading_days >= 126:
        return 'SELL_SIGNAL', 'TIME_EXIT'
    if mechanism == 'R1':
        if type(consecutive_weekly_below_ma60) is int and consecutive_weekly_below_ma60 >= 2:
            return 'SELL_SIGNAL', 'TWO_WEEK_MA60_EXIT'
    else:
        pe = number(pe_percentile)
        entry_profit, current_profit = number(entry_ttm_profit), number(current_ttm_profit)
        if pe is None or not 0 <= pe <= 1:
            return 'REVIEW', 'VALUATION_MISSING_OR_INVALID'
        if pe >= .6:
            if entry_profit is None or entry_profit <= 0 or current_profit is None or current_profit < entry_profit:
                return 'REVIEW', 'PE_RISE_EARNINGS_NOT_CONFIRMED'
            if total_return is not None and total_return > 0:
                return 'SELL_SIGNAL', 'VALUATION_RECOVERY_EXIT'
            return 'REVIEW', 'PE_RISE_WITHOUT_POSITIVE_TOTAL_RETURN'
    if hard_risk is not False or quality_valid is not True or total_return is None:
        return 'REVIEW', 'RISK_OR_RETURN_UNKNOWN'
    if mechanism == 'R1' and (type(consecutive_weekly_below_ma60) is not int or consecutive_weekly_below_ma60 < 0):
        return 'REVIEW', 'WEEKLY_SERIES_UNKNOWN'
    return 'HOLD', 'NO_EXIT_TRIGGER'
