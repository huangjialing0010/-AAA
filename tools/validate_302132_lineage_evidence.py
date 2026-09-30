"""独立核验代码沿革证据；不引用抓取器或标准层的计算代码。"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def verify(root):
    m = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    names = {'old_gap_raw','old_gap_qfq','new_gap_raw','old_boundary_qfq',
             'old_boundary_raw','new_boundary_qfq','new_boundary_raw','new_factors',
             'old_history_raw_1','old_history_raw_2','old_history_raw_3'}
    if {e['path'] for e in m['entries']} != {n+'.csv' for n in names} or len(m['entries']) != 11:
        raise ValueError('沿革证据文件集合错误')
    tables = {}
    for e in m['entries']:
        p = root/e['path']
        if p.stat().st_size != e['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest() != e['sha256']:
            raise ValueError('沿革证据哈希不匹配: '+e['path'])
        with p.open(encoding='utf-8', newline='') as f: rows = list(csv.DictReader(f))
        if len(rows) != e['rows']: raise ValueError('沿革证据行数错误')
        name = p.stem
        expected_code = '300114' if name.startswith('old_') else '302132'
        if any(r['code'] != 'sz.'+expected_code for r in rows): raise ValueError('响应身份错误')
        if name != 'new_factors':
            expected_flag = '2' if name.endswith('qfq') else '3'
            if any(r['adjustflag'] != expected_flag for r in rows): raise ValueError('响应复权口径错误')
            dates = [r['date'] for r in rows]
            expected_dates = (['2012-09-07','2012-09-10','2012-09-11'] if name.startswith('old_gap') else
                              ['2012-09-07','2012-09-11'] if name=='new_gap_raw' else
                              ['2025-02-14'] if name.startswith('old_boundary') else ['2025-02-17'])
            if name.startswith('old_history_raw_'):
                ranges={'1':('2010-01-01','2015-12-31'),'2':('2016-01-01','2021-12-31'),'3':('2022-01-01','2025-02-14')}
                start,end=ranges[name[-1]]
                if dates != sorted(set(dates)) or not dates or len(dates)>=2000 or any(not start<=d<=end for d in dates):
                    raise ValueError('旧代码历史分段日期异常')
            elif dates != expected_dates: raise ValueError('证据日期集合改变，需重新诊断: '+name)
        tables[name] = rows
    old = {r['date']:r for r in tables['old_gap_raw']}
    history={r['date']:r for name,rows in tables.items() if name.startswith('old_history_raw_') for r in rows}
    for r in tables['old_gap_raw']+tables['old_boundary_raw']:
        if history.get(r['date']) != r: raise ValueError('旧代码整段与点查响应不一致')
    for r in tables['new_gap_raw']:
        for f in ('open','high','low','close','preclose','volume','amount','tradestatus','pctChg','isST'):
            if D(r[f]) != D(old[r['date']][f]): raise ValueError('旧新代码重叠不复权字段不同: '+f)
    gap = old['2012-09-10']
    if gap['tradestatus'] != '1' or D(gap['volume']) <= 0: raise ValueError('补缺行并非有效成交')
    if D(gap['preclose']) != D(old['2012-09-07']['close']) or D(gap['close']) != D(old['2012-09-11']['preclose']):
        raise ValueError('补缺日昨收衔接失败')
    for r in tables['old_gap_qfq']:
        if any(r[f] != old[r['date']][f] for f in ('volume','amount','tradestatus')):
            raise ValueError('补缺证据双口径成交字段不同')
    a=tables['old_boundary_qfq'][0]; b=tables['new_boundary_qfq'][0]
    ar=tables['old_boundary_raw'][0]; br=tables['new_boundary_raw'][0]
    if D(ar['close']) != D(br['preclose']): raise ValueError('代码切换不复权不连续')
    scale = D(b['preclose'])/D(a['close'])
    if not scale.is_finite() or scale <= 0: raise ValueError('无效桥接因子')
    factors = [r for r in tables['new_factors'] if r['dividOperateDate']=='2025-02-17']
    if len(factors)!=1: raise ValueError('缺少变更日复权因子')
    expected = D(factors[0]['foreAdjustFactor'])
    if abs(D(b['preclose'])/D(br['preclose'])-expected)>D('0.0000001'):
        raise ValueError('因子接口不能佐证桥接比例')
    for f in ('open','high','low','close'):
        if abs(D(b[f])-D(br[f])*expected)>D('0.0000001'): raise ValueError('变更日价格不是统一缩放')
    existing_root=ROOT/'data/imports/baostock_historical_daily_20260904T014923Z'
    existing={}
    for p in (existing_root/'daily_unadjusted/302132').glob('*.csv'):
        with p.open(encoding='utf-8',newline='') as f:
            existing.update((r['date'],r) for r in csv.DictReader(f) if r['date']<='2025-02-14')
    if set(history)-set(existing) != {'2012-09-10'} or set(existing)-set(history):
        raise ValueError('旧代码整段与既有响应的日期差异超出已核实缺口')
    differences=[]; amount_precision_differences=[]
    for d,r in existing.items():
        h=history[d]
        if r['tradestatus'] != h['tradestatus']: raise ValueError('旧新代码交易状态不同: '+d)
        fields=('open','high','low','close','preclose','volume','amount')
        if r['tradestatus']=='1':
            mismatches=[f for f in fields if D(r[f])!=D(h[f])]
            if mismatches:
                if d=='2019-02-20' and mismatches==['amount'] and D(h['amount'])==D('63016557.48') and D(r['amount'])==D('63016557'):
                    amount_precision_differences.append(dict(date=d,old_code_amount=h['amount'],new_code_amount=r['amount'],
                                                           note='新代码数值与旧代码成交额按元取整一致，采用当时有效旧代码原始值'))
                else: raise ValueError('旧新代码正常交易字段不同: '+d)
        elif any(r[f]!=h[f] for f in fields):
            differences.append(d)
    return dict(bridge_factor=str(scale), previous_date='2025-02-14', effective_date='2025-02-17',
                missing_date='2012-09-10', factor_source_value=str(expected),
                supplement_source='old_gap_raw.csv', supplement_rows=1,
                raw_history_sources=['old_history_raw_1.csv','old_history_raw_2.csv','old_history_raw_3.csv'],
                raw_overlap_rows=len(existing), suspended_representation_difference_dates=differences,
                amount_precision_differences=amount_precision_differences)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot',type=Path,required=True); args=ap.parse_args()
    root=args.snapshot.resolve()
    if root.parent != (ROOT/'data/imports').resolve() or not root.name.startswith('lineage_302132_'):
        raise ValueError('沿革快照路径不合法')
    result=verify(root)
    p=root/'manifest.json'; m=json.loads(p.read_text(encoding='utf-8'))
    if m['status'] != 'SEALED_PENDING_INDEPENDENT_VALIDATION': raise ValueError('快照不是待验证状态')
    m.update(status='SEALED', independent_validation=dict(status='PASS', **result,
             validated_at=datetime.now(timezone.utc).isoformat()))
    p.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
