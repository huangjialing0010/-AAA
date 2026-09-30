"""生成 zzshare 与本地规则推算的审计快照，不作为 paper replay 规则文件。"""
from __future__ import annotations
import csv, hashlib, json
from pathlib import Path
from ashare_lab.sources.inferred_rule_audit import infer_rule

def main():
    root=Path(__file__).resolve().parents[1]; snap=root/"data/imports/zzshare_rule_snapshot_20260902_smallpages_20260924"; ext={}
    for p in sorted(list(snap.glob("page_*.json"))+list(snap.glob("tail_*.json"))):
        payload=json.loads(p.read_text(encoding="utf-8"));
        for row in ((payload.get("data") or {}).get("list") or []): ext[row["ts_code"].split(".")[0]]=row
    outdir=root/"data/canonical/zzshare_rule_audit_20260902"; outdir.mkdir(parents=True,exist_ok=True); out=outdir/"audit.csv"
    fields=["trade_date","code","upper_limit","lower_limit","source_is_paused","source_is_st","local_tradestatus","local_volume","inferred_upper_limit","inferred_lower_limit","rounding_difference","buyable","sellable","suspend_reason","source"]
    rows=[]
    for path in sorted((root/"data/canonical/stock_selection_v1/prices").glob("*.csv")):
        code=path.stem; local=None
        with path.open(encoding="utf-8",newline="") as f:
            for item in csv.DictReader(f):
                if item["trade_date"]=="2026-09-02": local=item; break
        if local is None or code not in ext: continue
        e=ext[code]; rule=infer_rule(local); diff="MATCH"
        if rule and (round(float(e["high_limit"]),2)!=round(rule.limit_up,2) or round(float(e["low_limit"]),2)!=round(rule.limit_down,2)): diff="ROUNDING_REVIEW"
        rows.append({"trade_date":"2026-09-02","code":code,"upper_limit":e.get("high_limit"),"lower_limit":e.get("low_limit"),"source_is_paused":e.get("is_paused"),"source_is_st":e.get("is_st"),"local_tradestatus":local.get("tradestatus"),"local_volume":local.get("volume"),"inferred_upper_limit":rule.limit_up if rule else "","inferred_lower_limit":rule.limit_down if rule else "","rounding_difference":diff,"buyable":"","sellable":"","suspend_reason":"","source":"zzshare"})
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    digest=hashlib.sha256(out.read_bytes()).hexdigest(); manifest={"status":"PENDING_VALIDATION","evidence_class":"EXTERNAL_RULE_AUDIT_ONLY","source":"zzshare","trade_date":"2026-09-02","rows":len(rows),"fields":fields,"files":[{"path":"audit.csv","bytes":out.stat().st_size,"sha256":digest}],"limitations":["仅包含 873 个本地同日重叠代码","buyable/sellable/suspend_reason 未提供，不得进入 paper replay","需完成许可与独立重叠验证"]}
    (outdir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps({"rows":len(rows),"sha256":digest,"status":manifest["status"]},ensure_ascii=False))
if __name__=="__main__": main()
