"""比较 zzshare 完整单日规则快照与本地推算规则。"""
from __future__ import annotations
import csv, json
from pathlib import Path
from ashare_lab.sources.inferred_rule_audit import infer_rule

def main():
    root=Path(__file__).resolve().parents[1]; snap=root/"data/imports/zzshare_rule_snapshot_20260902_smallpages_20260924"; ext={}
    for p in sorted(list(snap.glob("page_*.json"))+list(snap.glob("tail_*.json"))):
        payload=json.loads(p.read_text(encoding="utf-8"));
        for row in ((payload.get("data") or {}).get("list") or []): ext[row["ts_code"].split(".")[0]]=row
    match=[]; diff=[]; missing=[]; overlap=0
    for path in (root/"data/canonical/stock_selection_v1/prices").glob("*.csv"):
        code=path.stem; row=None
        with path.open(encoding="utf-8",newline="") as f:
            for item in csv.DictReader(f):
                if item["trade_date"]=="2026-09-02": row=item; break
        if row is None: missing.append(code); continue
        if code not in ext: continue
        overlap += 1; e=ext[code]; rule=infer_rule(row)
        if rule and round(float(e["high_limit"]),2)==round(rule.limit_up,2) and round(float(e["low_limit"]),2)==round(rule.limit_down,2): match.append(code)
        else: diff.append({"code":code,"external_high_limit":e.get("high_limit"),"external_low_limit":e.get("low_limit"),"inferred_high_limit":rule.limit_up if rule else None,"inferred_low_limit":rule.limit_down if rule else None})
    result={"status":"PASS" if not diff else "REVIEW","external_rows":len(ext),"local_files":940,"same_date_overlap":overlap,"limit_match":len(match),"limit_difference":len(diff),"local_missing_same_date":len(missing),"differences":diff[:50],"evidence_class":"EXTERNAL_RULE_CROSS_SOURCE_VALIDATION_CANDIDATE","promotion":"BLOCKED_LICENSE_AND_LOCAL_COVERAGE_REVIEW"}
    out=root/"reports/ZZSHARE_FULL_RULE_COMPARISON_20260924.json"; out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
