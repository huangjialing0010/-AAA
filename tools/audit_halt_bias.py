"""只读审计历史停牌记录对资格和低波动排名的影响。"""
import bisect, csv, json, math, statistics
from collections import Counter
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.selection.strategy import MembershipHistory, factor_observations, rank_candidates

def read(p):
    with p.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def main():
    root=ROOT/'data/canonical/stock_selection_v1'; cal=[r['trade_date'] for r in read(root/'trade_calendar.csv')]
    membership=MembershipHistory(read(root/'membership_weekly.csv')); dates=[d for d in cal if d[:7]<'2026-09']
    signal_dates=[d for d in dates if d in {'2012-12-31','2018-12-28','2023-01-31'}]
    price_dir=root/'prices'; halt_hist=Counter(); monthly=[]; rank_halt=[]
    policies = {
        'strict': dict(max_window_halt_days=0, max_consecutive_halt_days=0),
        'short_halt_5d': dict(max_window_halt_days=5, max_consecutive_halt_days=5),
        'ignore_history_halt': dict(max_window_halt_days=253, max_consecutive_halt_days=253),
        'cooldown_5d': dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=5),
        'cooldown_10d': dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=10),
        'cooldown_20d': dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=20),
    }
    for day in signal_dates:
        ci=bisect.bisect_left(cal,day); members=membership.as_of(day); total=0; halted=0; longhalt=0; policy_obs={k:[] for k in policies}; policy_halted={k:{} for k in policies}
        for code in members:
            rows=read(price_dir/(code+'.csv')); ds=[r['trade_date'] for r in rows]; i=bisect.bisect_left(ds,day)
            if i>=len(rows) or ds[i]!=day or i<252: continue
            window=rows[i-252:i+1]; total+=1; h=[r for r in window if r['tradestatus']!='1']; halted += bool(h)
            longest=0; run=0
            for r in window:
                run=run+1 if r['tradestatus']!='1' else 0; longest=max(longest,run)
            longhalt += longest>=10
            halt_hist.update(r['trade_date'] for r in h)
            for name, policy in policies.items():
                o=factor_observations(rows,[day],trading_dates=cal,**policy)
                if o: policy_obs[name].extend(o); policy_halted[name][code]=bool(h)
        rank_halt.append({'signal_date':day,'eligible_before_halt':total,'policies':{
            name:{'factor_observations':len(policy_obs[name]),'top30_halted':sum(policy_halted[name].get(r['code'],False) for r in rank_candidates(policy_obs[name])[:30])}
            for name in policies}})
        monthly.append({'signal_date':day,'window_rows':total,'any_halt':halted,'long_halt_10d':longhalt})
    print(json.dumps({'status':'PASS','signal_dates':signal_dates,'sample':monthly,'rank_sample':rank_halt,'common_halt_dates':halt_hist.most_common(20)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
