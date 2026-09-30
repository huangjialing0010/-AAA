"""将已封存真实证据接入手工低频首批订单；无未来成交。"""
import csv
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
from statistics import median

from .manual_rules import Annual, QualityEvidence, entry_decision
from .manual_portfolio import plan_openings


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sealed(root):
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'SEALED':
        raise ValueError('未封存证据')
    for entry in manifest['entries']:
        path = (root/entry['path']).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != entry['sha256']:
            raise ValueError('来源文件越界或哈希改变')
    return manifest


def read_rows(path):
    with path.open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def verify_financial_facts(root, facts):
    from pypdf import PdfReader
    sources = {}
    results = {}
    for code, fact in facts['candidates'].items():
        for kind in ('annual', 'h1'):
            if kind+'_pdf' not in fact:
                continue
            path = root/fact[kind+'_pdf']
            # 旧半年报在reports子目录，manifest位于其父目录。
            source_root = path.parent if (path.parent/'manifest.json').exists() else path.parent.parent
            manifest = sealed(source_root)
            if not any((source_root/e['path']).resolve() == path.resolve() for e in manifest['entries']):
                raise ValueError('PDF不在封存清单')
            text = PdfReader(path).pages[fact[kind+'_page']-1].extract_text()
            compact = re.sub(r'\s+', '', text)
            if '归属于上市公司股东的净利润' not in compact or '经营活动产生的现金流量净额' not in compact:
                raise ValueError('财务指标标签不匹配')
            values = [a[k] for a in fact['annuals'] for k in ('parent_profit','operating_cashflow','roe_percent')] if kind=='annual' else [fact[k] for k in ('h1_parent_profit','prior_h1_parent_profit','h1_operating_cashflow','prior_h1_operating_cashflow')]
            for value in values:
                if format(D(value), ',.2f') not in compact:
                    raise ValueError(f'{code} 原文未找到已转录数字 {value}')
            sources[fact[kind+'_pdf']] = sha(path)
        annuals = fact['annuals']
        ratio = sum(D(a['operating_cashflow']) for a in annuals)/sum(D(a['parent_profit']) for a in annuals)
        ttm = D(annuals[-1]['parent_profit']) + D(fact['h1_parent_profit']) - D(fact['prior_h1_parent_profit']) if 'h1_parent_profit' in fact else None
        results[code] = {'cash_profit_ratio':str(ratio), 'ttm_parent_profit':str(ttm) if ttm is not None else None}
    return results, sources


def prepare_launch(root, *, snapshot, reference, announcements, now):
    if now.tzinfo is None:
        raise ValueError('需要带时区生成时间')
    day = now.date()
    if day != date(2026, 9, 29):
        raise ValueError('人工风险审阅仅截至2026-09-29；后续运行须刷新，禁止自动延期')
    market_manifest = sealed(snapshot)
    sealed(reference)
    announcement_manifest = sealed(announcements)
    if announcement_manifest['code'] != '600886' or announcement_manifest['through'] != day.isoformat():
        raise ValueError('公告范围不匹配')
    refs = json.loads((reference/'reference.json').read_text(encoding='utf-8'))
    trading = [date.fromisoformat(r['calendar_date']) for r in refs['calendar'] if r['is_trading_day']=='1']
    previous = max(d for d in trading if d < day)
    next_day = min(d for d in trading if d > day)
    facts_path = root/'docs/MANUAL_FINANCIAL_FACTS_20260929.json'
    facts = json.loads(facts_path.read_text(encoding='utf-8'))
    metrics, evidence_sources = verify_financial_facts(root, facts)
    all_rows = {e['stock_code']:read_rows(snapshot/e['path']) for e in market_manifest['entries'] if e.get('category')=='daily_qfq_valuation'}
    calendar = sorted(set().union(*(set(r['date'] for r in rows) for rows in all_rows.values())))
    cutoff = min(max(r['date'] for r in rows) for rows in all_rows.values())
    if date.fromisoformat(cutoff) < previous or date.fromisoformat(cutoff) > day:
        raise ValueError('行情超过允许的一个交易日滞后或来自未来')
    if cutoff == day.isoformat() and now.hour < 15:
        raise ValueError('不允许使用尚未收盘的当日日线')
    signals, rejections, qualified = [], [], []
    # 只有已完成风险审阅的真实候选可准入；其他候选保留阻断。
    for code in ('600886', '600674', '605499'):
        fact = facts['candidates'][code]
        if code != '600886':
            reasons = ['CASH_PROFIT_RATIO_FAILED'] if D(metrics[code]['cash_profit_ratio']) < D('.8') else ['RISK_REVIEW_INCOMPLETE']
            raw = read_rows(snapshot/'daily_unadjusted_valuation'/f'{code}.csv')
            price = D(next(r['close'] for r in raw if r['date']==cutoff))
            if price*100 > D(10000):
                reasons.append('REFERENCE_PRICE_BELOW_MINIMUM_LOT_BUDGET')
            rejections.append({'code':code,'stage':'ADMISSION_NOT_ORDER','reasons':reasons})
            continue
        basic = refs['basic'][code][0]
        industry = refs['industry'][code][0]
        if basic['code']!='sh.'+code or basic['status']!='1' or basic['type']!='1' or basic['outDate']:
            raise ValueError('证券身份或上市状态不符')
        raw = read_rows(snapshot/'daily_unadjusted_valuation'/f'{code}.csv')
        last20 = [r for r in raw if r['date']<=cutoff][-20:]
        if len(last20)!=20 or any(not r['amount'] for r in last20):
            raise ValueError('成交额窗口不足')
        evidence = QualityEvidence(
            evidence_id=sha(facts_path)+':'+code, checked_on=day,
            mainboard=code.startswith(('600','601','603','605')), nonfinancial=industry['industry'].startswith('D44'),
            listed_on=date.fromisoformat(basic['ipoDate']), is_st=last20[-1]['isST']!='0',
            standard_audit=True, material_risk=False,
            annuals=tuple(Annual(a['year'],date.fromisoformat(fact['annual_available_on']),float(a['parent_profit']),float(a['operating_cashflow']),float(a['roe_percent'])) for a in fact['annuals']),
            ttm_parent_profit=float(metrics[code]['ttm_parent_profit']), ttm_available_on=date.fromisoformat(fact['h1_available_on']),
            median_amount_20d=float(median(D(r['amount']) for r in last20)), stock_code=code)
        decision = entry_decision(evidence=evidence, rows=all_rows[code], calendar=calendar, signal_day=day, market_day=date.fromisoformat(cutoff))
        signals.append({'code':code,'recorded_at':now.isoformat(),'market_cutoff':cutoff,**decision})
        qualified.append({'code':code,'industry':industry['industry'],'industry_as_of':day.isoformat(),'decision':decision})
    plan = plan_openings(signals=qualified, positions=[],cash=D(100000),as_of=day,monthly_buys=0,pending_buy_count=0,decision_window_verified=True)
    orders = [{**p, 'order_id':p['plan_id'], 'status':'PENDING_EXECUTION', 'created_at':now.isoformat(),
               'execution_day':next_day.isoformat(), 'expires_on':next_day.isoformat(), 'shares':None,
               'execution_requirements':['REFRESH_RISK_AND_CORPORATE_ACTIONS','VERIFIED_EXECUTION_BAR_AND_LIMITS','BUDGET_AND_T_PLUS_ONE']}
              for p in plan['plans']]
    sources = {str((p/'manifest.json').relative_to(root)):sha(p/'manifest.json') for p in (snapshot,reference,announcements)}
    sources.update(evidence_sources)
    for name in ('MANUAL_FINANCIAL_FACTS_20260929.json','MANUAL_RISK_REVIEW_20260929.md','MANUAL_FORWARD_LAUNCH_V1.md'):
        sources['docs/'+name]=sha(root/'docs'/name)
    return dict(status='FORWARD_ORDERS_PENDING' if orders else 'FORWARD_EVALUATED_NO_ORDER',
                account_id='manual_research_100k_v1', generated_at=now.isoformat(), market_cutoff=cutoff,
                sources=sources, signals=signals, orders=orders, rejections=rejections+plan['rejections'],
                financial_checks=metrics, trades=[], positions=[], cash='100000.00', equity='100000.00',
                strategy_return=None, automation_enabled=False,
                limitations=['仅核验三只主板观察候选，不代表所有触发候选均已核验','公司经营风险仍存在','PENDING不是成交，执行日需刷新证据'])
