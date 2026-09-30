"""比较 Tushare daily 探测样本与本地 canonical 原始日线。"""
from __future__ import annotations

import csv, json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    probe = json.loads((root / "data/imports/tushare_probe_20260924/probe.json").read_text(encoding="utf-8"))
    items = probe["daily"]["data"]["items"]
    fields = probe["daily"]["data"]["fields"]
    diffs = []
    checked = 0
    for item in items:
        ext = dict(zip(fields, item)); code = ext["ts_code"].split(".")[0]; path = root / f"data/canonical/stock_selection_v1/prices/{code}.csv"
        local = None
        if path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    if row["trade_date"] == "2026-09-02": local = row; break
        checked += 1
        if local is None:
            diffs.append({"code": code, "reason": "MISSING_LOCAL_ROW"}); continue
        mapping = {"open": "open", "high": "high", "low": "low", "close": "close", "pre_close": "preclose"}
        for ext_field, local_field in mapping.items():
            if round(float(ext[ext_field]), 4) != round(float(local[local_field]), 4):
                diffs.append({"code": code, "field": ext_field, "external": ext[ext_field], "local": local[local_field]})
    result = {"status": "PASS" if not diffs else "REVIEW", "checked": checked, "differences": diffs, "evidence_class": "CROSS_SOURCE_PRICE_PROBE_NOT_RULE_EVIDENCE", "stk_limit_status": "ACCESS_DENIED"}
    out = root / "reports/TUSHARE_DAILY_PROBE_COMPARISON_20260924.json"; out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
