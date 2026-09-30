"""从标准行情独立复算资格、因子和研究账户；不导入策略或撮合实现。"""
from __future__ import annotations
import bisect
import csv
import math
from collections import defaultdict
from decimal import Decimal as D, ROUND_HALF_UP


def read(path):
    with path.open(encoding='utf-8', newline='') as f: return list(csv.DictReader(f))


def number(value):
    try:
        d=D(value)
        return d if d.is_finite() else None
    except Exception: return None


def money(value): return value.quantize(D('.01'), rounding=ROUND_HALF_UP)


def independent_factor(rows, signal, calendar, halt_policy=None):
    halt_policy = halt_policy or {}
    dates=[r['trade_date'] for r in rows]
    i=bisect.bisect_left(dates,signal); j=bisect.bisect_left(calendar,signal)
    if i<252 or j<252 or dates[i:i+1]!=[signal]: return None
    window=rows[i-252:i+1]; last=window[-1]
    if [r['trade_date'] for r in window] != calendar[j-252:j+1]: return None
    if last['is_st']!='0': return None
    halt_days=sum(r['tradestatus']!='1' for r in window)
    longest=0; run=0
    for r in window:
        run=run+1 if r['tradestatus']!='1' else 0; longest=max(longest,run)
    if halt_days > halt_policy.get('max_window_halt_days', 0) or longest > halt_policy.get('max_consecutive_halt_days', 0): return None
    recent=halt_policy.get('recent_halt_window', 0)
    if recent and sum(r['tradestatus']!='1' for r in rows[max(0,i-recent):i]) > halt_policy.get('max_recent_halt_days', 0): return None
    cooldown=halt_policy.get('resumption_cooldown_days', 0)
    if cooldown > 0:
        resumed=0
        for r in reversed(rows[i-252:i+1]):
            if r['tradestatus']!='1': break
            resumed += 1
        if resumed < cooldown: return None
    for r in window:
        p=number(r['close_qfq'])
        if p is None or p<=0 or r['code']!=last['code']: return None
    for f in ('open','high','low','close','preclose','volume','amount'):
        p=number(last.get(f,''))
        if p is None or p<=0: return None
    amounts=[number(r.get('amount','')) for r in window[-20:]]
    if any(a is None or a<0 for a in amounts): return None
    amounts.sort(); median=(amounts[9]+amounts[10])/2
    if median<D('20000000'): return None
    returns=[float(D(window[k]['close_qfq'])/D(window[k-1]['close_qfq'])-1) for k in range(190,253)]
    avg=sum(returns)/63
    volatility=math.sqrt(sum((r-avg)**2 for r in returns)/62*252)
    return dict(momentum_12_1=format(D(window[231]['close_qfq'])/D(window[0]['close_qfq'])-1,'.12f'),
                volatility_63=format(volatility,'.12f'), median_amount_20=format(median,'f'), code=last['code'])


def audit_factors(canonical, price_entries, candidates, calendar, signal_dates, membership, halt_policy=None):
    observed=defaultdict(dict)
    for r in candidates:
        if r['code'] in observed[r['signal_date']]: raise ValueError('候选代码重复')
        observed[r['signal_date']][r['code']]=r
    if set(observed)!=set(signal_dates): raise ValueError('候选信号日期非冻结月末')
    expected=defaultdict(list)
    for n,e in enumerate(price_entries,1):
        relevant=[d for d in signal_dates if e['code'] in membership[d]]
        if relevant:
            rows=read(canonical/e['path'])
            for d in relevant:
                r=independent_factor(rows,d,calendar,halt_policy)
                if r is not None: expected[d].append(r)
        if n%100==0: print('independent_factor_progress',n,len(price_entries),flush=True)
    for d in signal_dates:
        rs=expected[d]
        if {r['code'] for r in rs} != set(observed[d]):
            expected_codes={r['code'] for r in rs}; observed_codes=set(observed[d])
            raise ValueError(f'资格或成分独立复算不一致: {d} expected={len(expected_codes)} observed={len(observed_codes)} expected_only={sorted(expected_codes-observed_codes)[:10]} observed_only={sorted(observed_codes-expected_codes)[:10]}')
        denom=D(max(1,len(rs)-1)); ranks={}
        for field,ascending in [('momentum_12_1',True),('volatility_63',False)]:
            ordered=sorted(rs,key=lambda r:(D(r[field]),r['code']))
            ranks[field]={r['code']:(D(i)/denom if ascending else 1-D(i)/denom) for i,r in enumerate(ordered)}
        for r in rs:
            code=r['code']; got=observed[d][code]
            for f in ('momentum_12_1','volatility_63','median_amount_20'):
                if abs(D(got[f])-D(r[f]))>D('.000000000001'): raise ValueError('因子独立复算不同: '+d+' '+code+' '+f)
            m=ranks['momentum_12_1'][code]; v=ranks['volatility_63'][code]
            r['composite_score']=format((m+v)/2,'.12f')
            for field,value in [('momentum_percentile',m),('low_volatility_percentile',v),('composite_score',(m+v)/2)]:
                if D(got[field])!=D(format(value,'.12f')): raise ValueError('分位或综合评分不同')
        ordered=sorted(rs,key=lambda r:(-D(r['composite_score']),r['code']))
        for rank,r in enumerate(ordered,1):
            if int(observed[d][r['code']]['rank'])!=rank: raise ValueError('排序独立复算不同')
    return expected


def rejection(row, side):
    if row is None: return 'MISSING_MARKET_ROW'
    vol=number(row.get('volume','')); op=number(row.get('open','')); qo=number(row.get('open_qfq',''))
    if row.get('tradestatus')!='1' or vol is None or vol<=0: return 'SUSPENDED_OR_ZERO_VOLUME'
    if op is None or op<=0 or qo is None or qo<=0: return 'MISSING_OR_NONPOSITIVE_OPEN'
    hi,lo,pct=[number(row.get(f,'')) for f in ('high','low','pct_chg')]
    if hi is not None and lo is not None and pct is not None and op==hi==lo:
        if side=='BUY' and pct>D('4.5'): return 'ONE_PRICE_LIMIT_UP'
        if side=='SELL' and pct<D('-4.5'): return 'ONE_PRICE_LIMIT_DOWN'
    return ''


def audit_portfolio(ledger, positions, orders, trades, schedule, provider, multiplier=D(1)):
    pos=defaultdict(dict); od=defaultdict(list); fills={}
    for r in positions:
        if r['code'] in pos[r['trade_date']]: raise ValueError('持仓日期代码重复')
        pos[r['trade_date']][r['code']]=r
    for r in orders: od[r['order_date']].append(r)
    for r in trades:
        if r['order_id'] in fills: raise ValueError('同一订单重复成交')
        fills[r['order_id']]=r
    cash=D('1000000'); holdings={}; refs={}; stale={}; prior_signal=None
    consumed=set()
    for day in ledger:
        date=day['trade_date']; targets=schedule.get(prior_signal); opening={}
        for code in list(holdings):
            r=provider(code,date); op=number(r.get('open_qfq','')) if r else None
            if op is not None and op>0:
                holdings[code]=money(holdings[code]*op/refs[code]); opening[code]=op; stale[code]=0
            else:
                opening[code]=refs[code]; stale[code]=stale.get(code,0)+1
                if stale[code]>60: raise ValueError('缺报价超过60日仍输出净值')
        today=iter(od[date]); expected_orders=0
        if targets is not None:
            desired=money((cash+sum(holdings.values(),D(0)))*D('.95')/len(targets))
            for side,codes in [('SELL',sorted(holdings)),('BUY',sorted(targets))]:
                for code in codes:
                    current=holdings.get(code,D(0))
                    requested=money(max(D(0),current-(desired if code in targets else D(0)))) if side=='SELL' else money(max(D(0),desired-current))
                    if requested<D('.01'): continue
                    order=next(today,None); expected_orders+=1
                    if order is None or (order['code'],order['side'],order['signal_date'])!=(code,side,prior_signal) or D(order['requested_notional'])!=requested:
                        raise ValueError('应下订单独立复算不一致: '+date+' '+code)
                    consumed.add(order['order_id'])
                    row=provider(code,date); reason=rejection(row,side)
                    minimum=money(D(row['open'])*(200 if code.startswith('688') else 100)) if not reason else D(0)
                    if side=='BUY' and not reason and requested<minimum: reason='BELOW_MINIMUM_ORDER_NOTIONAL'
                    fee=lambda gross: money(max(D(5)*multiplier,gross*D('.0003')*multiplier)) if gross>0 else D(0)
                    slip=lambda gross: money(gross*D('.0005')*multiplier)
                    available=requested
                    if side=='BUY' and not reason:
                        # 在分的整数网格上独立求最大可负担金额。
                        lo,hi=0,int(min(requested,cash)*100)
                        while lo<hi:
                            mid=(lo+hi+1)//2; gross=D(mid)/100
                            if gross+fee(gross)+slip(gross)<=cash: lo=mid
                            else: hi=mid-1
                        available=D(lo)/100
                        if available<minimum: reason='INSUFFICIENT_CASH_FOR_MINIMUM_ORDER'
                    trade=fills.get(order['order_id'])
                    if reason:
                        if order['status']!='REJECTED' or order['reason']!=reason or trade is not None: raise ValueError('拒单独立复算不同')
                        continue
                    if trade is None or order['status']!='FILLED': raise ValueError('缺少应有成交')
                    if (trade['code'],trade['side'],trade['trade_date'],trade['signal_date'])!=(code,side,date,prior_signal): raise ValueError('成交身份或日期错误')
                    gross=D(trade['gross_notional'])
                    if side=='SELL' and gross!=requested: raise ValueError('卖出金额错误')
                    rounding_allowance = max(D('.02'), number(row.get('open_qfq','')) or D('.02')) if side=='BUY' else D('.02')
                    if side=='BUY' and (gross>available or gross<minimum or available-gross>rounding_allowance):
                        raise ValueError(f'买入金额错误 date={date} code={code} gross={gross} available={available} requested={requested} cash={cash}')
                    tax=money(gross*(D('.001') if date<'2023-08-28' else D('.0005'))*multiplier) if side=='SELL' else D(0)
                    cash=money(cash+(gross if side=='SELL' else -gross)-fee(gross)-slip(gross)-tax)
                    value=money(current+(-gross if side=='SELL' else gross))
                    if value:
                        holdings[code]=value
                        if side=='BUY': opening[code]=D(row['open_qfq']); refs[code]=opening[code]; stale[code]=0
                    else:
                        holdings.pop(code,None); refs.pop(code,None); opening.pop(code,None); stale.pop(code,None)
        if next(today,None) is not None: raise ValueError('存在多余或非调仓日订单')
        for code in holdings:
            r=provider(code,date); close=number(r.get('close_qfq','')) if r else None
            if close is not None and close>0:
                holdings[code]=money(holdings[code]*close/opening[code]); refs[code]=close
        if set(pos[date])!=set(holdings): raise ValueError('持仓代码复算不同: '+date)
        equity=money(cash+sum(holdings.values(),D(0)))
        for code,value in holdings.items():
            r=pos[date][code]
            if D(r['research_value'])!=value or D(r['qfq_reference_close'])!=refs[code] or int(r['stale_days'])!=stale[code]:
                raise ValueError('逐股行情收益复算不同: '+date+' '+code)
            if D(r['weight'])!=D(format(value/equity,'.12f')): raise ValueError('持仓权重错误')
        if D(day['research_cash'])!=cash or D(day['equity'])!=equity or D(day['holdings_value'])!=sum(holdings.values(),D(0)):
            raise ValueError('逐日现金或净值独立复算不同: '+date)
        prior_signal=date
    if consumed!={r['order_id'] for r in orders}: raise ValueError('账本之外存在订单')
    if set(pos)-{r['trade_date'] for r in ledger}: raise ValueError('账本之外存在持仓')
