"""审计本地 OHLCV 推算规则与现有交易状态字段的对齐情况。"""
from __future__ import annotations

import argparse, csv, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.sources.inferred_rule_audit import classify_row


def rows(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-dir", type=Path, default=ROOT / "data/canonical/stock_selection_v1/prices")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    total = eligible = limit_up = limit_down = suspended = 0
    by_board = Counter(); by_status = Counter(); differences = Counter()
    files = 0
    for path in sorted(args.price_dir.glob("*.csv")):
        files += 1
        for row in rows(path):
            day = row.get("trade_date", "")
            if not args.start <= day <= args.end:
                continue
            total += 1
            item = classify_row(row)
            if item is None:
                continue
            eligible += 1
            board = str(item["board"]); by_board[board] += 1
            if item["inferred_limit_up"]: limit_up += 1
            if item["inferred_limit_down"]: limit_down += 1
            if item["inferred_suspended"]: suspended += 1
            by_status[str(row.get("tradestatus"))] += 1
            # 现有数据只有交易状态，没有逐日 limit 字段；差异只审计可比的停牌语义。
            source_halt = row.get("tradestatus") != "1"
            if source_halt != bool(item["inferred_suspended"]):
                differences["tradestatus_vs_volume_heuristic"] += 1
    result = {
        "status": "PASS",
        "evidence_class": "INFERRED_RULE_AUDIT_NOT_EXTERNAL_EVIDENCE",
        "source": "data/canonical/stock_selection_v1/prices",
        "files": files,
        "window": {"start": args.start, "end": args.end},
        "rows_total": total,
        "rows_eligible": eligible,
        "inferred_limit_up_rows": limit_up,
        "inferred_limit_down_rows": limit_down,
        "inferred_suspended_rows": suspended,
        "boards": dict(by_board),
        "tradestatus": dict(by_status),
        "differences": dict(differences),
        "promotion": "BLOCKED_EXTERNAL_RULE_SNAPSHOT_MISSING",
        "limitations": [
            "涨跌停价格由前收和板块规则推算，不是外部逐日规则快照",
            "停牌识别含成交量启发式，不能替代供应商停牌原因",
            "公司行为、首日上市和特殊交易状态需单独证据",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
