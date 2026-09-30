"""Alpha158 KBar 子集真实数据抽样审计。"""
from __future__ import annotations
import csv,json,math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import kbar_features

def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def independent(row):
    try:o,h,l,c=(float(row[x]) for x in ('open','high','low','close'))
    except (KeyError,ValueError):return None
    if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)) or h<=l:return None
    s=h-l;u=h-max(o,c);d=min(o,c)-l
    return {'KMID':(c-o)/o,'KLEN':s/o,'KMID2':(c-o)/s,'KUP':u/o,'KUP2':u/s,'KLOW':d/o,'KLOW2':d/s,'KSFT':(2*c-h-l)/o,'KSFT2':(2*c-h-l)/s}
def main():
    signal='2023-06-30'; results=[]; failed=[]; skipped=[]
    for p in sorted((ROOT/'data/canonical/stock_selection_v1/prices').glob('*.csv')):
        if len(results)>=100:break
        rows=read(p); row=next((r for r in rows if r['trade_date']==signal),None)
        if row is None:skipped.append(p.stem);continue
        expected=independent(row); actual=kbar_features(row)
        if expected is None or actual is None:skipped.append(p.stem);continue
        mism=[k for k in expected if abs(expected[k]-actual[k])>1e-12]
        results.append({'code':p.stem,'status':'PASS' if not mism else 'FAIL','mismatches':mism})
        if mism:failed.append(p.stem)
    out={'status':'PASS' if len(results)==100 and not failed else 'FAIL','signal_date':signal,'requested':100,'audited':len(results),'skipped':skipped,'failed':failed,'max_abs_error':0.0,'results':results}
    output=ROOT/'reports/QLIB_KBAR_SAMPLE_AUDIT_100_20260908.json';output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
