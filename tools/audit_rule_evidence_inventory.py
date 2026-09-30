"""盘点有限窗口行情文件中的交易规则相关证据字段；不推断涨跌停。"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICE_ROOT = ROOT / "data/canonical/stock_selection_v1/prices"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    with args.orders.open(encoding="utf-8", newline="") as handle:
        orders = [row for row in csv.DictReader(handle)
                  if args.start <= row["order_date"] <= args.end]
    codes = sorted({row["code"] for row in orders})
    required = ["open", "high", "low", "close", "preclose", "volume", "amount", "tradestatus", "is_st", "pct_chg"]
    optional_but_needed = ["upper_limit", "lower_limit", "suspend_reason", "buyable", "sellable", "commission", "stamp_tax"]
    field_presence = Counter()
    rows_seen = 0
    missing_rows = 0
    tradestatus = Counter()
    for code in codes:
        path = PRICE_ROOT / f"{code}.csv"
        if not path.is_file():
            missing_rows += 1
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not args.start <= row["trade_date"] <= args.end:
                    continue
                rows_seen += 1
                for field in required + optional_but_needed:
                    if field in row and row[field] not in (None, ""):
                        field_presence[field] += 1
                tradestatus[row.get("tradestatus", "")] += 1
    output = {
        "status": "RULE_EVIDENCE_INVENTORY_ONLY",
        "start": args.start,
        "end": args.end,
        "order_count": len(orders),
        "code_count": len(codes),
        "codes_without_price_file": missing_rows,
        "price_rows_seen": rows_seen,
        "tradestatus_counts": dict(tradestatus),
        "required_field_rows": {field: field_presence[field] for field in required},
        "missing_rule_fields": [field for field in optional_but_needed if not field_presence[field]],
        "conclusion": "已有行情可支持基础价格和tradestatus审计，但没有逐笔涨跌停、可买卖状态和费用证据，不能生成已核定交易规则。",
    }
    stamp = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    destination = ROOT / "reports" / f"RULE_EVIDENCE_INVENTORY_{stamp}.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("status", "order_count", "code_count", "price_rows_seen", "missing_rule_fields")}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
