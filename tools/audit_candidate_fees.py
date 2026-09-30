"""对整数股候选订单计算理论费用；不生成成交、不计算收益。"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ashare_lab.selection.fees import FeeRuleProfile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--effective-from", required=True)
    parser.add_argument("--commission-rate", default="0.0003")
    parser.add_argument("--commission-minimum", default="5")
    parser.add_argument("--stamp-tax-sell-rate", default="0.0005")
    args = parser.parse_args()
    profile = FeeRuleProfile(
        profile_id="EXPLICIT_COST_AUDIT_PROFILE",
        effective_from=date.fromisoformat(args.effective_from),
        commission_rate=Decimal(args.commission_rate),
        commission_minimum=Decimal(args.commission_minimum),
        stamp_tax_sell_rate=Decimal(args.stamp_tax_sell_rate),
    )
    source = json.loads(args.translation.read_text(encoding="utf-8"))
    rows = [row for row in source["records"] if args.start <= row["order_date"] <= args.end]
    total = Decimal("0")
    requested_total = sum(Decimal(row["requested_notional"]) for row in rows)
    results = []
    for row in rows:
        fee = None
        if row["reason"] == "CANDIDATE_NOT_FILL" and row["candidate_shares"] and row["raw_open_unadjusted"]:
            fee = profile.calculate(
                side=row["side"], price=Decimal(row["raw_open_unadjusted"]),
                shares=int(row["candidate_shares"]), day=date.fromisoformat(row["order_date"])
            )
            total += fee
        results.append({"order_id": row["order_id"], "fee": str(fee) if fee is not None else None,
                        "fee_status": "THEORETICAL_ONLY" if fee is not None else "NOT_CALCULATED"})
    output = {
        "status": "COST_AUDIT_NOT_FILL",
        "start": args.start, "end": args.end,
        "profile": {"profile_id": profile.profile_id, "effective_from": args.effective_from,
                     "commission_rate": args.commission_rate,
                     "commission_minimum": args.commission_minimum,
                     "stamp_tax_sell_rate": args.stamp_tax_sell_rate},
        "order_count": len(rows),
        "requested_notional_total": str(requested_total.quantize(Decimal("0.01"))),
        "theoretical_fee_total": str(total.quantize(Decimal("0.01"))),
        "theoretical_fee_bps": str((total / requested_total * Decimal("10000")).quantize(Decimal("0.01"))) if requested_total else None,
        "results": results,
        "limitations": ["候选数量不等于成交数量", "理论费用不是历史实际扣费", "未计滑点、涨跌停和盘口"],
    }
    stamp = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    destination = ROOT / "reports" / f"CANDIDATE_FEE_AUDIT_{stamp}.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "order_count": len(rows), "theoretical_fee_total": output["theoretical_fee_total"]}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
