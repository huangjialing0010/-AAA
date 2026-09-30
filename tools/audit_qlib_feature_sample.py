"""多证券特征字段抽样对照。"""
from __future__ import annotations
import csv, json, math, statistics
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import build_point_in_time_samples

def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--count',type=int,default=20); parser.add_argument('--output',default='reports/QLIB_FEATURE_SAMPLE_AUDIT_20260908.json'); args=parser.parse_args()
    if args.count < 1: raise ValueError('count必须大于0')
    signal='2023-06-30'; results=[]; mismatches=[]; skipped=[]
    for path in sorted((ROOT/'data/canonical/stock_selection_v1/prices').glob('*.csv')):
        if len(results) >= args.count: break
        rows=read(path); index=next((i for i,r in enumerate(rows) if r['trade_date']==signal),None)
        if index is None or index<20 or index+2>=len(rows): skipped.append(path.stem); continue
        sample=next((r for r in build_point_in_time_samples(rows) if r['trade_date']==signal),None)
        if sample is None: skipped.append(path.stem); continue
        close=[float(r['close_qfq']) for r in rows]
        amount=[float(rows[k]['amount']) for k in range(index-4,index+1)]
        volume=[float(rows[k]['volume']) for k in range(index-4,index+1)]
        returns=[close[k]/close[k-1]-1 for k in range(index-18,index+1)]
        expected={'return_5':format(close[index]/close[index-5]-1,'.12f'),'return_20':format(close[index]/close[index-20]-1,'.12f'),'volatility_20':format(statistics.stdev(returns)*math.sqrt(252),'.12f'),'amount_median_5':format(statistics.median(amount),'.12f'),'volume_median_5':format(statistics.median(volume),'.12f')}
        mism={k:{'expected':v,'actual':sample[k]} for k,v in expected.items() if v!=sample[k]}
        results.append({'code':path.stem,'status':'PASS' if not mism else 'FAIL','mismatches':mism})
        if mism: mismatches.append(path.stem)
    out={'status':'PASS' if not mismatches and len(results)==args.count else 'FAIL','signal_date':signal,'requested_codes':args.count,'audited_codes':len(results),'skipped_before_sample':skipped,'failed_codes':mismatches,'results':results}
    output=ROOT/args.output; output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2)); return 0 if out['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
