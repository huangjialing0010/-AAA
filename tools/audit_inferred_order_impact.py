"""将本地推算的涨跌停/停牌规则映射到策略订单，评估回测成交假设影响。"""
from __future__ import annotations

import argparse, csv, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.sources.inferred_rule_audit import infer_rule


def read(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=Path, required=True)
    ap.add_argument("--price-dir", type=Path, default=ROOT / "data/canonical/stock_selection_v1/prices")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    orders = list(read(args.orders))
    needed_codes = {str(order["code"]).zfill(6) for order in orders}
    price_index = {}
    for code in needed_codes:
        path = args.price_dir / f"{code}.csv"
        if path.exists():
            price_index[code] = {r["trade_date"]: r for r in read(path)}

    counts = Counter(); samples = []; suspended_samples = []; total = 0
    for order in orders:
        total += 1
        code = str(order["code"]).zfill(6)
        row = price_index.get(code, {}).get(order["order_date"])
        if row is None:
            counts["MISSING_PRICE_ROW"] += 1
            continue
        rule = infer_rule(row)
        if rule is None:
            counts["MISSING_RULE_INPUT"] += 1
            continue
        try:
            open_price = float(row["open"])
        except (KeyError, ValueError, TypeError):
            counts["INVALID_OPEN"] += 1
            continue
        if row.get("tradestatus") != "1":
            counts["SUSPENDED_ORDER_DAY"] += 1
            if len(suspended_samples) < 100:
                suspended_samples.append({"order_id": order["order_id"], "code": code, "order_date": order["order_date"], "side": order["side"], "order_status": order.get("status"), "tradestatus": row.get("tradestatus"), "volume": row.get("volume"), "open": row.get("open"), "close": row.get("close")})
        if order["side"] == "BUY" and abs(open_price - rule.limit_up) <= 0.001:
            counts["BUY_AT_INFERRED_LIMIT_UP"] += 1
            if len(samples) < 20:
                samples.append({"order_id": order["order_id"], "code": code, "order_date": order["order_date"], "side": order["side"], "order_status": order.get("status"), "open": open_price, "limit": rule.limit_up, "reason": "BUY_AT_INFERRED_LIMIT_UP"})
        if order["side"] == "SELL" and abs(open_price - rule.limit_down) <= 0.001:
            counts["SELL_AT_INFERRED_LIMIT_DOWN"] += 1
            if len(samples) < 20:
                samples.append({"order_id": order["order_id"], "code": code, "order_date": order["order_date"], "side": order["side"], "order_status": order.get("status"), "open": open_price, "limit": rule.limit_down, "reason": "SELL_AT_INFERRED_LIMIT_DOWN"})
    result = {
        "status": "PASS",
        "evidence_class": "INFERRED_ORDER_IMPACT_NOT_EXTERNAL_EXECUTION_EVIDENCE",
        "orders": str(args.orders),
        "orders_total": total,
        "counts": dict(counts),
        "samples": samples,
        "suspended_samples": suspended_samples,
        "interpretation": "仅评估开盘价是否等于推算涨跌停价；不等价于盘口可成交性，也不改变回测结果。",
        "promotion": "BLOCKED_EXTERNAL_RULE_SNAPSHOT_MISSING",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
