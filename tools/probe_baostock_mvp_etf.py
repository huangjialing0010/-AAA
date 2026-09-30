#!/usr/bin/env python
"""只读探测510300的证券身份、复权因子和分红覆盖。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import RetryingBaoStockSource  # noqa: E402


def main() -> int:
    with RetryingBaoStockSource(max_queries_per_session=10) as source:
        basic = source.stock_basic("510300")
        factors = source.adjust_factor("510300", "2012-01-01", "2026-09-02")
        dividends = []
        for year in range(2012, 2027):
            result = source.dividend("510300", year, year_type="operate")
            dividends.extend(result.rows)
    print(json.dumps({
        "basic_fields": basic.fields,
        "basic_rows": basic.rows,
        "adjust_factor_fields": factors.fields,
        "adjust_factor_rows": factors.rows,
        "dividend_rows": dividends,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
