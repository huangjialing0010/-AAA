"""审计 zzshare 单日快照与本地 canonical 的 2026-09-02 覆盖缺口。"""
from __future__ import annotations
import csv, json
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]; snap=root/"data/imports/zzshare_rule_snapshot_20260902_smallpages_20260924"; ext=set()
    for p in sorted(list(snap.glob("page_*.json"))+list(snap.glob("tail_*.json"))):
        payload=json.loads(p.read_text(encoding="utf-8")); ext.update(str(r.get("ts_code","")).split(".")[0] for r in ((payload.get("data") or {}).get("list") or []))
    rows=[]
    for path in sorted((root/"data/canonical/stock_selection_v1/prices").glob("*.csv")):
        code=path.stem; dates=[]
        with path.open(encoding="utf-8",newline="") as f:
            for row in csv.DictReader(f): dates.append(row.get("trade_date",""))
        if "2026-09-02" not in dates:
            rows.append({"code":code,"first_date":min(dates) if dates else None,"last_date":max(dates) if dates else None,"rows":len(dates),"external_present":code in ext})
    result={"status":"REVIEW","target_date":"2026-09-02","missing_codes":len(rows),"rows":rows,"interpretation":"本地缺行不得用外部价格静默填补；需按数据覆盖和证券状态分别处理"}
    out=root/"reports/ZZSHARE_LOCAL_COVERAGE_GAP_20260924.json"; out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps({"missing_codes":len(rows),"sample":rows[:10]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
