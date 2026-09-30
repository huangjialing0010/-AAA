"""检查研究截止日后的行情是否足以启动前向验证。"""
from __future__ import annotations

import argparse, csv, json
from collections import Counter
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-dir", type=Path, required=True)
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    dates = Counter(); files = 0; rows_after = 0
    for path in args.price_dir.glob("*.csv"):
        files += 1
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                day = row.get("trade_date", "")
                if day > args.cutoff:
                    dates[day] += 1
                    rows_after += 1
    observed = sorted(dates)
    result = {
        "status": "WAITING_FOR_COMPLETE_SIGNAL_DATE" if not observed or max(dates.values()) < files * 0.95 else "COVERAGE_REVIEW_REQUIRED",
        "evidence_class": "FORWARD_VALIDATION_START_AUDIT",
        "cutoff": args.cutoff,
        "price_files": files,
        "post_cutoff_rows": rows_after,
        "post_cutoff_dates": [{"date": d, "code_rows": dates[d]} for d in observed],
        "latest_common_date": observed[-1] if observed else None,
        "next_action": "补齐截止日后的全市场封存行情，并等待至少一个完整月末信号及其后成交窗口",
        "live_admission": "BLOCKED",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
