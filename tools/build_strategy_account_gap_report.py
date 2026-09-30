"""汇总研究订单与真实整数股账户之间的差距；只做审计，不生成成交。"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    with args.orders.open(encoding="utf-8", newline="") as handle:
        orders = [row for row in csv.DictReader(handle)
                  if args.start <= row["order_date"] <= args.end]
    translation = json.loads(args.translation.read_text(encoding="utf-8"))
    records = [row for row in translation["records"]
               if args.start <= row["order_date"] <= args.end]
    reason_counts = Counter(row["reason"] for row in records)
    side_counts = Counter(row["side"] for row in records)
    candidate_shares = sum(int(row["candidate_shares"] or 0) for row in records)
    notional = sum(Decimal(row["requested_notional"]) for row in orders)
    candidate_notional = sum(
        Decimal(row["candidate_shares"]) * Decimal(row["raw_open_unadjusted"])
        for row in records
        if row["candidate_shares"] and row["raw_open_unadjusted"]
    )
    blockers = [
        "RESEARCH_ORDER_NOT_REAL_FILL: 研究CSV中的FILLED只是研究账本状态，不是历史成交回报",
        "NO_INITIAL_REAL_SHARE_LOTS: 没有可审计的期初现金、持仓批次和冻结数量",
        "NO_VERIFIED_PRICE_LIMIT_RULE: 本窗口未逐笔绑定涨跌停、停牌和盘口证据",
        "NO_COMPLETE_CORPORATE_ACTION_COVERAGE: 分红等公司行动尚未覆盖全组合",
        "NO_FEE_SLIPPAGE_MAPPING: 本审计未将佣金、印花税、滑点映射到逐笔成交",
    ]
    output = {
        "status": "NOT_READY_REAL_SHARE_ACCOUNT",
        "scope": "LIMITED_WINDOW_STRATEGY_ACCOUNT_GAP_AUDIT",
        "start": args.start,
        "end": args.end,
        "order_count": len(orders),
        "side_counts": dict(side_counts),
        "requested_notional_total": str(notional.quantize(Decimal("0.01"))),
        "candidate_share_total": candidate_shares,
        "candidate_open_notional_total": str(candidate_notional.quantize(Decimal("0.01"))),
        "reason_counts": dict(reason_counts),
        "translation_status": translation.get("status"),
        "blockers": blockers,
        "conclusion": "策略订单已完成有限窗口的整数股候选转换审计，但尚未形成可用于收益结论的真实股数账户回放。",
    }
    stamp = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    destination = ROOT / "reports" / f"STRATEGY_REAL_SHARE_GAP_{stamp}.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("status", "order_count", "reason_counts", "candidate_share_total")}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
