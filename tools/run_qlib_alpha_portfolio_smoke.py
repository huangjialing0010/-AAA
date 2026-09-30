"""Alpha158 子集组合账本烟测；不接实盘，不覆盖基线结果。"""
from __future__ import annotations
import csv,json
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import build_point_in_time_samples
from ashare_lab.qlib_model import RidgeRankModel
from ashare_lab.selection.engine import DataGateBlocked, ResearchCostConfig,run_research_engine
from ashare_lab.selection.metrics import calculate_index_metrics,calculate_research_metrics
from ashare_lab.selection.strategy import MembershipHistory,month_end_dates
from run_stock_selection_mvp import CachedCsvMarketProvider
def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def filter_point_in_time_members(candidates,members):
    return [(code,row) for code,row in candidates if code in members]
def benchmark_dominance_decision(portfolio,benchmark):
    dominated=(portfolio['annualized_return']<=benchmark['annualized_return'] and abs(portfolio['max_drawdown'])>=abs(benchmark['max_drawdown']) and portfolio['sharpe_zero_rate']<=benchmark['sharpe_zero_rate'])
    return 'REJECTED_BENCHMARK_DOMINATED' if dominated else 'REQUIRES_FURTHER_VALIDATION'
def main():
    canonical=ROOT/'data/canonical/stock_selection_v1';manifest=json.loads((canonical/'manifest.json').read_text(encoding='utf-8'));entries=manifest['price_entries'];codes=sorted(e['code'] for e in entries)[:100]; rows_by={c:read(canonical/'prices'/f'{c}.csv') for c in codes}; samples_by_code={c:build_point_in_time_samples(rows_by[c]) for c in codes}; samples=[row for code in codes for row in samples_by_code[code]];membership=MembershipHistory(read(canonical/'membership_weekly.csv'))
    train=[r for r in samples if '2018-01-01'<=r['trade_date']<'2023-01-01'];model=RidgeRankModel(alpha=10.0).fit(train)
    calendar=[r['trade_date'] for r in read(canonical/'trade_calendar.csv') if '2012-12-01'<=r['trade_date']<='2026-08-31'];signals=[d for d in month_end_dates(calendar) if '2023-01-01'<=d<'2026-09-01'];by_date=defaultdict(list)
    for c in codes:
        by={r['trade_date']:r for r in samples_by_code[c]}
        for d in signals:
            row=by.get(d)
            if row is not None: by_date[d].append((c,row))
    schedule={};candidate_counts=[]
    for d in signals:
        candidates=filter_point_in_time_members(by_date[d],membership.as_of(d));candidate_counts.append(len(candidates))
        if not candidates: raise RuntimeError(f'{d} 历史成分过滤后没有模型候选')
        scores=model.predict([r for _,r in candidates]); ranked=sorted(zip(scores,candidates),key=lambda x:(-x[0],x[1][0])); schedule[d]=[c for _,(c,_) in ranked[:20]]
    provider=CachedCsvMarketProvider(canonical,entries)
    event_path=ROOT/'data/imports/delisting_events_000540_20260908/000540.json'; event_evidence={'000540':json.loads(event_path.read_text(encoding='utf-8'))} if event_path.is_file() else {}
    path=ROOT/'reports/QLIB_ALPHA_PORTFOLIO_SMOKE_100_20260908.json'
    try:
        result=run_research_engine(calendar,(),schedule,initial_equity=Decimal('1000000'),costs=ResearchCostConfig(),market_row_provider=provider,event_evidence=event_evidence)
    except DataGateBlocked as exc:
        message=str(exc)
        blocked={'status':'BLOCKED_BY_DATA_GATE','generated_at':datetime.now(timezone.utc).isoformat(),'model':'ridge_alpha158_subset_22','codes':len(codes),'train_samples':len(train),'signal_dates':len(signals),'target_count':20,'membership_source':'canonical/membership_weekly.csv','minimum_point_in_time_candidates':min(candidate_counts),'maximum_point_in_time_candidates':max(candidate_counts),'error_code':exc.code,'blocking_trade_date':exc.trade_date,'stale_days':exc.stale_days,'block_reason':exc.reason,'error':message,'decision':'不放宽价格连续性规则，不使用未来数据过滤候选，不生成可晋级组合结果'}
        path.write_text(json.dumps(blocked,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(blocked,ensure_ascii=False,indent=2)); return 2
    metrics=calculate_research_metrics(result.ledger,result.trades,'2023-01-01','2026-08-31');benchmark=calculate_index_metrics(read(ROOT/'data/canonical/mvp_510300_v2/daily.csv'),'2023-01-01','2026-08-31');decision=benchmark_dominance_decision(metrics,benchmark);out={'status':'PASS','generated_at':datetime.now(timezone.utc).isoformat(),'model':'ridge_alpha158_subset_22','codes':len(codes),'train_samples':len(train),'signal_dates':len(signals),'target_count':20,'membership_source':'canonical/membership_weekly.csv','minimum_point_in_time_candidates':min(candidate_counts),'maximum_point_in_time_candidates':max(candidate_counts),'metrics':metrics,'benchmark':'510300_CASH_DIVIDEND_TOTAL_RETURN','benchmark_metrics':benchmark,'active_total_return':metrics['total_return']-benchmark['total_return'],'active_annualized_return':metrics['annualized_return']-benchmark['annualized_return'],'strategy_decision':decision,'note':'固定训练边界、按信号日历史成分过滤的组合烟测，不是滚动训练，也不是盈利证明'}
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
