import csv,sys,json
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from selection_independent_audit import audit_portfolio
def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
r=ROOT/'reports/stock_selection_mvp_20260908T065725Z_short5'; c=ROOT/'data/canonical/stock_selection_v1'; cm=json.loads((c/'manifest.json').read_text(encoding='utf-8'))
price={e['code']:c/e['path'] for e in cm['price_entries']}
cache={}
def provider(code,day):
    key=(code,day[:4])
    if key not in cache:
        with price[code].open(encoding='utf-8',newline='') as f: cache[key]={x['trade_date']:x for x in csv.DictReader(f) if x['trade_date'].startswith(day[:4])}
    return cache[key].get(day)
for label in ['portfolio_20','portfolio_30','portfolio_40','portfolio_30_cost_stress_2x']:
    targets=read(r/f'targets_{label.split("_")[1]}.csv') if label!='portfolio_30_cost_stress_2x' else read(r/'targets_30.csv')
    schedule={}
    for x in targets:schedule.setdefault(x['signal_date'],[]).append(x['code'])
    try:
        audit_portfolio(read(r/f'{label}_ledger.csv'),read(r/f'{label}_positions.csv'),read(r/f'{label}_orders.csv'),read(r/f'{label}_trades.csv'),schedule,provider,Decimal(2) if label.endswith('2x') else Decimal(1)); print(label,'PASS')
    except Exception as e: print(label,'FAIL',e); break
