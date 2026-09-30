"""单证券特征数值对照：独立公式重算并与适配器输出比较。"""
from __future__ import annotations
import csv, json, math
from decimal import Decimal
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import build_point_in_time_samples

def main() -> int:
    path=ROOT/'data/canonical/stock_selection_v1/prices/000001.csv'
    with path.open(encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    signal='2023-06-30'
    index=next(i for i,r in enumerate(rows) if r['trade_date']==signal)
    sample=next(r for r in build_point_in_time_samples(rows) if r['trade_date']==signal)
    close=[float(r['close_qfq']) for r in rows]
    amount=[float(r['amount']) for r in rows]
    volume=[float(r['volume']) for r in rows]
    returns=[close[k]/close[k-1]-1 for k in range(index-18,index+1)]
    expected={
        'return_5': format(close[index]/close[index-5]-1,'.12f'),
        'return_20': format(close[index]/close[index-20]-1,'.12f'),
        'volatility_20': format(__import__('statistics').stdev(returns)*math.sqrt(252),'.12f'),
        'amount_median_5': format(__import__('statistics').median(amount[index-4:index+1]),'.12f'),
        'volume_median_5': format(__import__('statistics').median(volume[index-4:index+1]),'.12f'),
    }
    fields=list(expected)
    mismatches={k:{'expected':expected[k],'actual':sample[k]} for k in fields if expected[k]!=sample[k]}
    result={'status':'PASS' if not mismatches else 'FAIL','code':'000001','signal_date':signal,'fields':{k:{'expected':expected[k],'actual':sample[k]} for k in fields},'mismatches':mismatches}
    out=ROOT/'reports/QLIB_FEATURE_VALUE_AUDIT_20260908.json'; out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if not mismatches else 1
if __name__=='__main__': raise SystemExit(main())
