"""运行严格拒绝式 shadow replay；没有规则证据的订单不入账。"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--initial-cash", default="1000000.00")
    args = parser.parse_args()

    with args.orders.open(encoding="utf-8", newline="") as handle:
        orders = [row for row in csv.DictReader(handle)
                  if args.start <= row["order_date"] <= args.end]
    translation = json.loads(args.translation.read_text(encoding="utf-8"))
    translated = {row["order_id"]: row for row in translation["records"]}
    decisions = []
    reasons = Counter()
    for order in orders:
        candidate = translated.get(order["order_id"])
        if candidate is None:
            reason = "MISSING_TRANSLATION"
        elif candidate["reason"] != "CANDIDATE_NOT_FILL":
            reason = candidate["reason"]
        else:
            # 不能从 OHLC 推断涨跌停、盘口或可成交价格，故无规则证据即拒绝。
            reason = "MISSING_VERIFIED_RULES"
        reasons[reason] += 1
        decisions.append({
            "order_id": order["order_id"],
            "order_date": order["order_date"],
            "code": order["code"],
            "side": order["side"],
            "candidate_shares": candidate.get("candidate_shares") if candidate else None,
            "decision": "REJECTED",
            "reason": reason,
            "booked": False,
        })
    output = {
        "status": "BLOCKED_NO_RULE_EVIDENCE",
        "scope": "STRICT_SHADOW_REPLAY_NO_FILL",
        "start": args.start,
        "end": args.end,
        "initial_cash_assumption": args.initial_cash,
        "order_count": len(orders),
        "booked_count": 0,
        "rejected_count": len(decisions),
        "reason_counts": dict(reasons),
        "decisions": decisions,
        "limitations": [
            "没有实际历史成交回报，因此不计算策略收益",
            "没有逐笔涨跌停、盘口和费用证据，全部候选成交拒绝",
            "初始现金仅作为接口参数记录，不代表真实账户资产",
        ],
    }
    stamp = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    destination = ROOT / "reports" / f"STRATEGY_SHADOW_REPLAY_{stamp}.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("status", "order_count", "booked_count", "reason_counts")}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
