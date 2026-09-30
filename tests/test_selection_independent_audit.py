from __future__ import annotations
import copy
import csv
import math
import sys
import unittest
from pathlib import Path
from decimal import Decimal as D
from datetime import date,timedelta
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools')]
from selection_independent_audit import audit_portfolio, independent_factor, audit_factors
from ashare_lab.selection.engine import run_research_engine
from ashare_lab.selection.strategy import factor_observations, rank_candidates


class IndependentAuditTests(unittest.TestCase):
    def test_source_based_factor_audit_rejects_balanced_fake_ranks(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); entries=[]; observations=[]
            calendar=[(date(2023,1,1)+timedelta(days=i)).isoformat() for i in range(253)]
            for c in range(4):
                code=f'{c:06d}'
                rows=[dict(trade_date=d,code=code,open='100',high='101',low='99',close='100',preclose='100',
                           close_qfq=str(100+i*(c+1)*.02+math.sin(i)*(c+1)),
                           volume='1000000',amount='50000000',tradestatus='1',is_st='0') for i,d in enumerate(calendar)]
                path=root/(code+'.csv')
                with path.open('w',encoding='utf-8',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
                entries.append(dict(code=code,path=path.name))
                observations.extend(factor_observations(rows,[calendar[-1]],trading_dates=calendar))
            ranked=rank_candidates(observations)
            for i,r in enumerate(ranked,1): r['rank']=str(i)
            membership={calendar[-1]:{r['code'] for r in ranked}}
            audit_factors(root,entries,ranked,calendar,[calendar[-1]],membership)
            ranked[0]['momentum_12_1']='9.000000000000'
            with self.assertRaises(ValueError): audit_factors(root,entries,ranked,calendar,[calendar[-1]],membership)

    def scenario(self):
        dates=['2024-01-30','2024-01-31','2024-02-01','2024-02-02']
        rows=[dict(trade_date=d,code='000001',open=str(10+i),close=str(11+i),high=str(12+i),low=str(9+i),
                   open_qfq=str(8+i),close_qfq=str(9+i),volume='100000',tradestatus='1',pct_chg='1') for i,d in enumerate(dates)]
        schedule={dates[0]:['000001'], dates[2]:['000001']}
        result=run_research_engine(dates,rows,schedule)
        provider=lambda code,d: next((r for r in rows if r['code']==code and r['trade_date']==d),None)
        return result,schedule,provider

    def test_independent_replay_matches_daily_market_accounting(self):
        r,s,p=self.scenario()
        audit_portfolio(r.ledger,r.positions,r.orders,r.trades,s,p)

    def test_balanced_but_fabricated_equity_is_rejected(self):
        r,s,p=self.scenario(); r=copy.deepcopy(r)
        r.ledger[-1]['equity']=str(D(r.ledger[-1]['equity'])+100)
        r.ledger[-1]['holdings_value']=str(D(r.ledger[-1]['holdings_value'])+100)
        r.positions[-1]['research_value']=str(D(r.positions[-1]['research_value'])+100)
        with self.assertRaises(ValueError): audit_portfolio(r.ledger,r.positions,r.orders,r.trades,s,p)

    def test_fake_order_rejection_is_rejected(self):
        r,s,p=self.scenario(); r=copy.deepcopy(r)
        r.orders[0]['status']='REJECTED'; r.orders[0]['reason']='SUSPENDED_OR_ZERO_VOLUME'
        r.trades=[]
        with self.assertRaises(ValueError): audit_portfolio(r.ledger,r.positions,r.orders,r.trades,s,p)

    def test_stress_costs_and_missing_market_sell_are_replayed(self):
        from ashare_lab.selection.engine import ResearchCostConfig
        r,s,p=self.scenario()
        dates=[x['trade_date'] for x in r.ledger]
        rows=[p('000001',d) for d in dates]
        schedule={dates[0]:['000001'],dates[2]:['000002']}
        rows[-1]=dict(rows[-1],tradestatus='0',volume='0')
        result=run_research_engine(dates,rows,schedule,costs=ResearchCostConfig().scaled(D(2)))
        provider=lambda code,d: next((r for r in rows if r['code']==code and r['trade_date']==d),None)
        audit_portfolio(result.ledger,result.positions,result.orders,result.trades,schedule,provider,D(2))


if __name__=='__main__': unittest.main()
