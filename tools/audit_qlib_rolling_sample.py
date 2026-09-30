"""Alpha158 ROC/MA/STD20 真实数据抽样审计。"""
from __future__ import annotations
import csv,json,math,statistics
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import rolling_features, volume_rolling_features, price_position_features
def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def main():
    signal='2023-06-30';results=[];skipped=[];failed=[]
    for p in sorted((ROOT/'data/canonical/stock_selection_v1/prices').glob('*.csv')):
        if len(results)>=100:break
        rows=read(p);i=next((j for j,r in enumerate(rows) if r['trade_date']==signal),None)
        if i is None or i<20:skipped.append(p.stem);continue
        try: prices=[float(rows[k]['close']) for k in range(i-20,i+1)]
        except (KeyError,ValueError):skipped.append(p.stem);continue
        if any(not math.isfinite(x) or x<=0 for x in prices):skipped.append(p.stem);continue
        latest=prices[-1]; expected={'ROC20':prices[0]/latest,'MA20':statistics.mean(prices)/latest,'STD20':statistics.stdev(prices)/latest}
        try: volumes=[float(rows[k]['volume']) for k in range(i-20,i+1)]
        except (KeyError,ValueError): skipped.append(p.stem);continue
        if any(not math.isfinite(x) or x<0 for x in volumes) or volumes[-1]<=0: skipped.append(p.stem);continue
        expected.update({'VMA20':statistics.mean(volumes)/volumes[-1],'VSTD20':statistics.stdev(volumes)/volumes[-1]})
        try: highs=[float(rows[k]['high']) for k in range(i-20,i+1)]; lows=[float(rows[k]['low']) for k in range(i-20,i+1)]
        except (KeyError,ValueError): skipped.append(p.stem);continue
        if any(not math.isfinite(x) or x<=0 for x in highs+lows) or max(highs)<=min(lows): skipped.append(p.stem);continue
        expected.update({'MAX20':max(highs)/latest,'MIN20':min(lows)/latest,'RSV20':(latest-min(lows))/(max(highs)-min(lows))})
        actual={**rolling_features(rows,i,window=20),**volume_rolling_features(rows,i,window=20),**price_position_features(rows,i,window=20)};m=[k for k in expected if abs(expected[k]-actual[k])>1e-12];results.append({'code':p.stem,'status':'PASS' if not m else 'FAIL','mismatches':m});failed.extend([p.stem] if m else [])
    out={'status':'PASS' if len(results)==100 and not failed else 'FAIL','signal_date':signal,'requested':100,'audited':len(results),'skipped':skipped,'failed':failed,'results':results};(ROOT/'reports/QLIB_ROLLING_SAMPLE_AUDIT_100_20260908.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
