#!/usr/bin/env python
"""只读探测 BaoStock 沪深300历史成分的最早覆盖边界。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import RetryingBaoStockSource  # noqa: E402


QUERY_DATES = (
    "2005-04-08", "2005-12-30", "2006-01-04", "2006-03-31",
    "2006-06-30", "2006-09-29", "2006-12-29", "2007-12-28",
    "2007-07-20", "2007-07-23", "2007-07-24", "2007-07-25",
    "2007-07-26", "2007-07-27",
    "2007-07-30", "2007-08-06", "2007-08-13", "2007-08-20",
    "2008-12-31", "2009-12-31", "2010-01-04", "2011-01-04",
)


def main() -> int:
    observations = []
    with RetryingBaoStockSource(max_queries_per_session=4) as source:
        for query_date in QUERY_DATES:
            result = source.hs300_members(query_date)
            observations.append({
                "query_date": query_date,
                "rows": len(result.rows),
                "update_dates": sorted({row.get("updateDate", "") for row in result.rows}),
            })
    print(json.dumps(observations, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
