"""Read-only independent audit of the first pending launch, not execution approval."""
import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from statistics import median
import argparse

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit(folder):
    manifest = load(folder/'manifest.json')
    for name, expected in manifest['files'].items():
        require(digest(folder/name) == expected, 'Launch artifact changed: '+name)
    run = load(folder/'run.json')
    for name, expected in run['sources'].items():
        path = (ROOT/name).resolve()
        require(path.is_relative_to(ROOT.resolve()), 'Source outside workspace')
        require(digest(path) == expected, 'Source changed: '+name)
        if path.name == 'manifest.json':
            source = load(path)
            if source.get('status') == 'SEALED':
                for entry in source['entries']:
                    child = (path.parent/entry['path']).resolve()
                    require(child.is_relative_to(path.parent.resolve()), 'Unsafe source path')
                    require(digest(child) == entry['sha256'], 'Snapshot content changed')
    facts = load(ROOT/'docs/MANUAL_FINANCIAL_FACTS_20260929.json')['candidates']
    fact = facts['600886']
    annuals = fact['annuals']
    ratio = sum(D(a['operating_cashflow']) for a in annuals)/sum(D(a['parent_profit']) for a in annuals)
    ttm = D(annuals[-1]['parent_profit'])+D(fact['h1_parent_profit'])-D(fact['prior_h1_parent_profit'])
    require(ratio >= D('.8') and ttm > 0, 'Financial gate failed')
    require(median(D(a['roe_percent']) for a in annuals) >= 10, 'ROE gate failed')
    require(str(ttm) == run['financial_checks']['600886']['ttm_parent_profit'], 'TTM mismatch')
    snapshot = ROOT/'data/imports/baostock_current_quality_valuation_20260929T055313Z'
    with (snapshot/'daily_qfq_valuation/600886.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    require(all(r['code'] == 'sh.600886' for r in rows), 'Wrong security')
    rows = sorted(rows, key=lambda r:r['date'])
    require(len({r['date'] for r in rows}) == len(rows), 'Duplicate dates')
    require(rows[-1]['date'] == run['market_cutoff'], 'Market cutoff mismatch')
    closes = [D(r['close']) for r in rows]
    pe = sorted(D(r['peTTM']) for r in rows if '2023-09-28' < r['date'] <= run['market_cutoff'] and D(r['peTTM']) > 0)
    require(len(pe) >= 504, 'Insufficient valuation history')
    index = D(len(pe)-1)*D('.8')
    lower = int(index)
    p80 = pe[lower]+(pe[lower+1]-pe[lower])*(index-lower)
    breakout = closes[-1]/max(closes[-121:-1])-1
    ma_change = sum(closes[-60:])/sum(closes[-80:-20])-1
    require(breakout > 0 and ma_change > 0 and D(rows[-1]['peTTM']) <= p80, 'R1 failed')
    require(rows[-1]['tradestatus'] == '1' and rows[-1]['isST'] == '0', 'Latest status failed')
    require(median(D(r['amount']) for r in rows[-20:]) >= 20000000, 'Liquidity failed')
    reference = load(ROOT/'data/imports/manual_reference_20260929T062017Z/reference.json')
    signal_day = datetime.fromisoformat(run['generated_at']).date().isoformat()
    days = sorted(r['calendar_date'] for r in reference['calendar'] if r['is_trading_day'] == '1')
    next_day = min(d for d in days if d > signal_day)
    require(run['market_cutoff'] >= max(d for d in days if d < signal_day), 'Stale data')
    require(len(run['orders']) == 1 and len(run['signals']) == 1, 'Unexpected first batch')
    order = run['orders'][0]
    require(order['code'] == run['signals'][0]['code'] == '600886', 'Order/signal mismatch')
    require(order['mechanism'] == 'R1' and order['signal_day'] == signal_day, 'Order scope mismatch')
    require(order['execution_day'] == order['expires_on'] == next_day, 'Execution date mismatch')
    require(order['industry'] == reference['industry']['600886'][0]['industry'], 'Industry mismatch')
    require(D(order['budget']) == D('10000') and order['shares'] is None, 'Budget/quantity mismatch')
    require(not run['trades'] and not run['positions'] and D(run['cash']) == 100000, 'Premature execution')
    require(run['strategy_return'] is None, 'Unfounded strategy return')
    return {'status':'PASS_PENDING_RECORD_ONLY', 'order_id':order['order_id'],
            'execution_day':next_day, 'cash_profit_ratio':str(ratio), 'ttm_profit':str(ttm),
            'breakout':str(breakout), 'ma_change':str(ma_change),
            'limitations':['Not a new PDF semantic review', 'Not execution approval',
                           'Execution-day evidence and scheduling still required']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.run_directory), ensure_ascii=False, indent=2))
