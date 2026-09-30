"""First pending buy lifecycle; verified adapters supply sources, never real orders."""
from dataclasses import asdict
from datetime import date, datetime, time
from decimal import Decimal as D
from .manual_quote import normalize_quote, SHANGHAI
from .manual_execution import execute_signal
from .cash_dividend_account import CashDividendAccount
from .share_account import ShareAccount
from .fees import FeeRuleProfile
from .order_gate import ExecutionRules
from .account_reconciliation import reconcile


def assess_first_order(run, *, now, quote=None, review=None, calendar=()):
    if now.tzinfo is None:
        raise ValueError('Timezone required')
    now = now.astimezone(SHANGHAI)
    if len(run['orders']) != 1 or run['trades'] or run['positions']:
        raise ValueError('Only the first empty-account order is supported')
    order = run['orders'][0]
    execution_day = date.fromisoformat(order['execution_day'])
    result = dict(order_id=order['order_id'], code=order['code'], checked_at=now.isoformat(),
                  execution_day=order['execution_day'], fills=[], cash=run['cash'], positions=[])
    def outcome(status, reason):
        return {**result, 'status':status, 'reason':reason}
    if now.date() < execution_day:
        return outcome('WAITING', 'EXECUTION_DAY_NOT_REACHED')
    if now.date() > execution_day:
        return outcome('EXPIRED', 'NO_RETROSPECTIVE_FILL')
    if now.time() < time(15, 5):
        return outcome('WAITING', 'END_OF_DAY_MODEL_NOT_READY')
    if quote is None or review is None:
        return outcome('WAITING_EVIDENCE', 'MISSING_EXECUTION_DAY_EVIDENCE')
    try:
        bar = normalize_quote(quote, code=order['code'], day=execution_day, observed_at=now)
        if bar['quote_time'].time() < time(15):
            return outcome('WAITING_EVIDENCE','INCOMPLETE_DAILY_QUOTE')
        market_status = quote['data'].get('f292')
        if type(market_status) is not int or market_status != 5:
            if type(market_status) is int and market_status in (6,7,8,9,14,15,16):
                return outcome('REJECTED','PROVIDER_STATUS_NOT_TRADABLE')
            return outcome('WAITING_EVIDENCE','PROVIDER_CLOSE_STATUS_NOT_CONFIRMED')
        if review.get('code') != order['code'] or review.get('day') != execution_day.isoformat():
            return outcome('REJECTED','REVIEW_SCOPE_MISMATCH')
        eligibility = review.get('eligibility')
        if not isinstance(eligibility,dict) or not eligibility.get('source_hashes'):
            return outcome('REJECTED','MISSING_PREFROZEN_ELIGIBILITY')
        frozen = datetime.fromisoformat(eligibility['frozen_at'])
        if (eligibility.get('code') != order['code'] or eligibility.get('day') != execution_day.isoformat()
                or frozen.tzinfo is None or frozen > now or frozen.astimezone(SHANGHAI).date() != execution_day
                or frozen.astimezone(SHANGHAI).time() > time(9,25)):
            return outcome('REJECTED','ELIGIBILITY_NOT_FROZEN_BEFORE_OPEN')
        for key in ('non_st','no_material_risk','corporate_actions_verified'):
            if eligibility.get(key) is not True or review.get(key) is not eligibility[key]:
                return outcome('REJECTED','PREFROZEN_QUALIFICATION_FAILED:'+key)
        reviewed_at = datetime.fromisoformat(review['reviewed_at'])
        cutoff = datetime.fromisoformat(review['information_cutoff'])
        if (reviewed_at.tzinfo is None or reviewed_at > now or reviewed_at.astimezone(SHANGHAI).date() != execution_day
                or cutoff.tzinfo is None or cutoff.astimezone(SHANGHAI).date() != execution_day
                or cutoff.astimezone(SHANGHAI).time() > time(9,25) or cutoff > frozen):
            return outcome('REJECTED','REVIEW_TIME_INVALID')
        for key in ('non_st','not_suspended','buyable','no_material_risk','corporate_actions_verified','price_mapping_verified'):
            if review.get(key) is not True:
                return outcome('REJECTED','NOT_VERIFIED:'+key)
        if not review.get('evidence_id') or not review.get('source_hashes'):
            return outcome('REJECTED','MISSING_REVIEW_SOURCES')
        days = sorted(set(date.fromisoformat(d) for d in calendar))
        next_day = dict(zip(days, days[1:]))
        account = CashDividendAccount(ShareAccount(D(run['cash'])))
        rules = ExecutionRules(execution_day, order['code'], review['evidence_id'], 100,100,
                               bar['lower'],bar['upper'],'ROUND100_WITH_WHOLE_REMAINDER')
        updated, decision, _ = execute_signal(account=account,action='BUY_SIGNAL',mechanism=order['mechanism'],
            code=order['code'],order_id=order['order_id'],signal_day=date.fromisoformat(order['signal_day']),
            execution_day=execution_day,next_trade_day=next_day,rules=rules,tradable=True,
            open_price=bar['open'],low_price=bar['low'],high_price=bar['high'],slippage_rate=D('.0005'),
            fees=FeeRuleProfile('MANUAL_RESEARCH_V1',date(2026,9,29),D('.0003'),D(5),D('.0005')),
            single_budget=D(order['budget']),portfolio_budget=D(run['cash'])*D('.8'),
            portfolio_check_passed=D(order['budget']) <= D(run['cash'])*D('.1'),
            corporate_actions_verified=True,signal_evidence_id=order['signal_evidence_id'],
            execution_evidence_id=review['evidence_id'])
        if decision.status != 'BOOKED':
            return outcome(decision.status,decision.reason)
        balance = reconcile(updated,D(run['cash']))
        if balance['status'] != 'BALANCES_MATCH':
            raise ValueError('Independent reconciliation failed')
        return {**result, 'status':'SIMULATED_BOOKED','reason':decision.reason,
                'cash':str(updated.shares.cash),'fills':[asdict(f) for f in updated.shares.fills],
                'positions':[asdict(l) for l in updated.shares.lots], 'reconciliation':balance,
                'valuation_price':str(bar['close']),
                'equity':str(updated.shares.cash+sum(l.shares for l in updated.shares.lots)*bar['close']),
                'model':'PRECOMMITTED_NEXT_OPEN_ADVERSE_5BPS_EOD_SIMULATION'}
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        return outcome('REJECTED','INVALID_EVIDENCE:'+str(error))
