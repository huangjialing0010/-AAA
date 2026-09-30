#!/usr/bin/env python
"""只读探测单只股票的 BaoStock 日线查询，不写入数据文件。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import BaoStockSource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 BaoStock 单股日线连通性")
    parser.add_argument("stock_code")
    parser.add_argument("--start-date", default="2026-08-01")
    parser.add_argument("--end-date", default="2026-09-03")
    parser.add_argument("--adjustflag", default="3", choices=("1", "2", "3"))
    args = parser.parse_args()
    with BaoStockSource() as source:
        result = source.daily(
            args.stock_code,
            args.start_date,
            args.end_date,
            adjustflag=args.adjustflag,
        )
    returned_adjustflags = sorted({row.get("adjustflag", "") for row in result.rows})
    returned_codes = sorted({row.get("code", "") for row in result.rows})
    adjustflag_ranges = {}
    for flag in returned_adjustflags:
        flag_dates = [row["date"] for row in result.rows if row.get("adjustflag", "") == flag]
        adjustflag_ranges[flag] = {
            "rows": len(flag_dates),
            "min_date": min(flag_dates),
            "max_date": max(flag_dates),
        }
    sample_fields = ("date", "code", "open", "high", "low", "close", "preclose", "adjustflag")

    def sample(row: dict[str, str]) -> dict[str, str]:
        return {field: row.get(field, "") for field in sample_fields}

    print(json.dumps({
        "stock_code": args.stock_code,
        "requested_adjustflag": args.adjustflag,
        "returned_adjustflags": returned_adjustflags,
        "returned_adjustflag_ranges": adjustflag_ranges,
        "returned_codes": returned_codes,
        "rows": len(result.rows),
        "min_date": result.rows[0]["date"] if result.rows else None,
        "max_date": result.rows[-1]["date"] if result.rows else None,
        "first_row": sample(result.rows[0]) if result.rows else None,
        "last_row": sample(result.rows[-1]) if result.rows else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
