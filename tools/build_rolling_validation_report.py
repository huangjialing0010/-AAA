"""从已封存研究账本生成滚动稳定性报告；不把历史滚动结果称为前向验证。"""
from __future__ import annotations

import argparse, csv, json
from pathlib import Path


def load(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def equity(rows):
    return [float(r["equity"]) for r in rows]


def windows(rows, size=252, step=21):
    values = equity(rows); out = []
    for end in range(size, len(values), step):
        start = values[end - size]
        finish = values[end]
        peak = values[end - size]
        drawdown = 0.0
        for value in values[end - size : end + 1]:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1.0)
        out.append({"start": rows[end - size]["trade_date"], "end": rows[end]["trade_date"], "return": finish / start - 1.0, "max_drawdown": drawdown})
    return out


def yearly(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["trade_date"][:4], []).append(row)
    result = []
    for year, group in sorted(groups.items()):
        if len(group) < 200:
            continue
        result.append({"year": year, "trading_days": len(group), "return": float(group[-1]["equity"]) / float(group[0]["equity"]) - 1.0})
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", type=Path, required=True)
    ap.add_argument("--benchmark", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    strategy = load(args.strategy); benchmark = load(args.benchmark)
    sw = windows(strategy); bw = windows(benchmark)
    result = {
        "status": "PASS",
        "evidence_class": "HISTORICAL_ROLLING_STABILITY_NOT_FORWARD_VALIDATION",
        "strategy_yearly": yearly(strategy),
        "benchmark_yearly": yearly(benchmark),
        "strategy_rolling_252d": {"windows": len(sw), "positive_fraction": sum(x["return"] > 0 for x in sw) / len(sw) if sw else None, "min_return": min((x["return"] for x in sw), default=None), "max_return": max((x["return"] for x in sw), default=None), "worst_drawdown": min((x["max_drawdown"] for x in sw), default=None)},
        "benchmark_rolling_252d": {"windows": len(bw), "positive_fraction": sum(x["return"] > 0 for x in bw) / len(bw) if bw else None, "min_return": min((x["return"] for x in bw), default=None), "max_return": max((x["return"] for x in bw), default=None), "worst_drawdown": min((x["max_drawdown"] for x in bw), default=None)},
        "forward_validation": "NOT_STARTED_DATA_CUTOFF_2026-09-01",
        "limitations": ["滚动窗口仍使用已观察历史，不能称为真正前向验证", "账本为研究组合而非真实股数账户", "未包含外部逐日涨跌停和盘口证据"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
