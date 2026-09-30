"""独立回放单个公司行动；不改主策略账本。"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.selection.corporate_actions import apply_corporate_action  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--shares", type=Decimal, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8"))
    source = event["source_code"]
    result = apply_corporate_action({source: args.shares}, event)
    output = {
        "status": "PILOT_ONLY_BLOCKED_FOR_LIVE_ACCOUNT",
        "event_type": event.get("event_type"),
        "effective_date": event.get("effective_date"),
        "source_code": source,
        "source_shares": str(args.shares),
        "converted_shares": {code: str(value) for code, value in result["positions"].items()},
        "fractional_shares": {code: str(value) for code, value in result.get("fractional_shares", {}).items()},
        "cash_settlement_required": result.get("cash_settlement_required", False),
        "cash_option": event.get("cash_option"),
        "limitations": [
            "零碎股处理和现金选择权未知，未入账",
            "不改变主策略历史结果",
            "只验证换股比例和零碎股暴露",
        ],
    }
    destination = ROOT / "reports" / f"CORPORATE_ACTION_PILOT_{source}_{event['effective_date']}.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
