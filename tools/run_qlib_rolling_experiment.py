"""运行无未来数据泄漏的年度滚动训练 IC 实验。"""
from __future__ import annotations
import csv,json,math,statistics
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.qlib_mini import build_point_in_time_samples
from ashare_lab.qlib_model import RidgeRankModel
def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def rank_corr(rows,scores):
    if len(rows)<3:return None
    y=[float(r['label_forward_return']) for r in rows]
    def rank(v):
        order=sorted(range(len(v)),key=lambda i:(v[i],i));out=[0.0]*len(v)
        for n,i in enumerate(order):out[i]=n
        return out
    a,b=rank(y),rank(scores);ma,mb=statistics.mean(a),statistics.mean(b);den=math.sqrt(sum((x-ma)**2 for x in a)*sum((x-mb)**2 for x in b));return sum((x-ma)*(z-mb) for x,z in zip(a,b))/den if den else None
def main():
    d=ROOT/'data/canonical/stock_selection_v1/prices';codes=sorted(p.stem for p in d.glob('*.csv'))[:100];samples=[]
    for code in codes:samples.extend(build_point_in_time_samples(read(d/f'{code}.csv')))
    output={'status':'PASS','codes':len(codes),'sample_counts':{},'years':{}}
    for year in ('2024','2025','2026'):
        train=[r for r in samples if r['trade_date']<year+'-01-01' and r['trade_date']>='2018-01-01'];test=[r for r in samples if r['trade_date'].startswith(year)]
        model=RidgeRankModel(alpha=10.0).fit(train); by={}; groups={}
        for row in test: groups.setdefault(row['trade_date'],[]).append(row)
        for date in sorted(groups):
            group=groups[date];by[date]=rank_corr(group,model.predict(group))
        values=[v for v in by.values() if v is not None];output['sample_counts'][year]={'train':len(train),'test':len(test)};output['years'][year]={'dates':len(values),'mean':statistics.mean(values) if values else None,'median':statistics.median(values) if values else None}
    out=ROOT/'reports/QLIB_ROLLING_EXPERIMENT_100_20260908.json';out.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(output,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
