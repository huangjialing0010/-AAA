"""运行小范围 Qlib 风格样本实验；只报告预测质量，不生成交易订单。"""

from __future__ import annotations

import csv
import json
import math
import statistics
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.qlib_mini import build_point_in_time_samples
from ashare_lab.qlib_model import RidgeRankModel


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def rank_corr(rows: list[dict[str, str]], scores: list[float]) -> float | None:
    if len(rows) < 3:
        return None
    actual = [float(row["label_forward_return"]) for row in rows]
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: (values[index], index))
        result = [0.0] * len(values)
        for rank, index in enumerate(order): result[index] = float(rank)
        return result
    a, b = ranks(actual), ranks(scores)
    ma, mb = statistics.mean(a), statistics.mean(b)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    denominator = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return numerator / denominator if denominator else None


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--count',type=int,default=50); parser.add_argument('--output',default='reports/QLIB_MINI_EXPERIMENT_20260908.json'); args=parser.parse_args()
    if args.count < 1: raise ValueError('count必须大于0')
    price_dir = ROOT / "data/canonical/stock_selection_v1/prices"
    codes = sorted(path.stem for path in price_dir.glob("*.csv"))[:args.count]
    samples: list[dict[str, str]] = []
    for code in codes:
        samples.extend(build_point_in_time_samples(read(price_dir / f"{code}.csv")))
    train = [row for row in samples if "2018-01-01" <= row["trade_date"] < "2023-01-01"]
    valid = [row for row in samples if "2023-01-01" <= row["trade_date"] < "2024-01-01"]
    test = [row for row in samples if "2024-01-01" <= row["trade_date"] <= "2026-08-31"]
    model = RidgeRankModel(alpha=10.0).fit(train)
    result = {"status": "PASS", "generated_at": datetime.now(timezone.utc).isoformat(), "codes": len(codes), "sample_counts": {"train": len(train), "validation": len(valid), "test": len(test)}, "alpha": 10.0, "rank_ic": {}}
    for name, rows in (("validation", valid), ("test", test)):
        by_date: dict[str, list[dict[str, str]]] = {}
        for row in rows: by_date.setdefault(row["trade_date"], []).append(row)
        values = [rank_corr(group, model.predict(group)) for group in by_date.values()]
        values = [value for value in values if value is not None]
        by_year = {}
        for year in sorted({date[:4] for date in by_date}):
            year_values = [rank_corr(group, model.predict(group)) for date, group in by_date.items() if date[:4] == year]
            year_values = [value for value in year_values if value is not None]
            by_year[year] = {"dates": len(year_values), "mean": statistics.mean(year_values) if year_values else None, "median": statistics.median(year_values) if year_values else None}
        result["rank_ic"][name] = {"dates": len(values), "mean": statistics.mean(values) if values else None, "median": statistics.median(values) if values else None, "by_year": by_year}
    output = ROOT / args.output
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
