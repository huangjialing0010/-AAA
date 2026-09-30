"""比较 zzshare 规则字段探测与本地日线及推算规则。"""
from __future__ import annotations
import csv, json
from pathlib import Path
from ashare_lab.sources.inferred_rule_audit import infer_rule


def main():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "data/imports/zzshare_probe_20260924/probe.json").read_text(encoding="utf-8"))["response"]["data"]["list"]
    results=[]
    for ext in data:
        code=ext["ts_code"].split(".")[0]; local=None
        local_path = root / f"data/canonical/stock_selection_v1/prices/{code}.csv"
        if local_path.exists():
            with local_path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    if row["trade_date"] == "2026-09-02": local=row; break
        item={"code":code,"external_high_limit":ext.get("high_limit"),"external_low_limit":ext.get("low_limit")}
        if local is None: item["status"]="MISSING_LOCAL_ROW"
        else:
            rule=infer_rule(local); item["status"]="MATCH" if rule and round(float(ext["high_limit"]),2)==round(rule.limit_up,2) and round(float(ext["low_limit"]),2)==round(rule.limit_down,2) else "LIMIT_RULE_DIFFERENCE"; item["local_inferred_high_limit"]=rule.limit_up if rule else None; item["local_inferred_low_limit"]=rule.limit_down if rule else None
        results.append(item)
    out={"status":"PASS" if all(x["status"]=="MATCH" for x in results) else "REVIEW","evidence_class":"EXTERNAL_RULE_PROBE_NOT_CANONICAL","rows":results,"promotion":"BLOCKED_UNTIL_FULL_SNAPSHOT_AND_LICENSE_REVIEW"}
    (root/"reports/ZZSHARE_RULE_PROBE_COMPARISON_20260924.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
