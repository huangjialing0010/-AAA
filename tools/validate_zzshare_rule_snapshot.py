"""校验 zzshare 分页原始快照的重复、字段和本地重叠情况。"""
from __future__ import annotations
import csv, json
import argparse
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    ap=argparse.ArgumentParser(); ap.add_argument("--snapshot", type=Path, default=root/"data/imports/zzshare_rule_snapshot_20260902_20260924"); args=ap.parse_args()
    snap=args.snapshot if args.snapshot.is_absolute() else root/args.snapshot
    manifest=json.loads((snap/"manifest.json").read_text(encoding="utf-8"))
    rows=[]
    for p in sorted(list(snap.glob("page_*.json")) + list(snap.glob("tail_*.json"))):
        payload=json.loads(p.read_text(encoding="utf-8")); rows.extend((payload.get("data") or {}).get("list") or [])
    keys=[(r.get("ts_code"),r.get("trade_date")) for r in rows]
    required=["ts_code","trade_date","open","high","low","close","prev_close","high_limit","low_limit","is_paused","is_st"]
    missing_fields=sorted({f for r in rows for f in required if f not in r})
    invalid_limits=[r for r in rows if r.get("high_limit") in (None,"") or r.get("low_limit") in (None,"")]
    ext_codes={str(r.get("ts_code","")).split(".")[0] for r in rows}; local_codes={p.stem for p in (root/"data/canonical/stock_selection_v1/prices").glob("*.csv")}
    result={"status":"REVIEW_LOCAL_COVERAGE_GAP" if local_codes-ext_codes else "PASS_PENDING_LICENSE_AND_CROSS_SOURCE_REVIEW","manifest_status":manifest["status"],"rows":len(rows),"unique_keys":len(set(keys)),"duplicates":len(rows)-len(set(keys)),"missing_fields":missing_fields,"invalid_limit_rows":len(invalid_limits),"distinct_codes":len(ext_codes),"local_price_files":len(local_codes),"local_code_overlap":len(ext_codes & local_codes),"local_codes_missing_from_snapshot":len(local_codes-ext_codes),"pages":manifest["pages"],"fetch_errors":manifest["errors"],"promotion":"BLOCKED_LOCAL_COVERAGE_GAP_OR_LICENSE_REVIEW" if local_codes-ext_codes else "BLOCKED_LICENSE_AND_CROSS_SOURCE_REVIEW"}
    out=root/"reports/ZZSHARE_RULE_SNAPSHOT_VALIDATION_20260924.json"; out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
