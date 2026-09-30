"""审计策略持仓在公司行动生效日前是否实际暴露；不修改账本。"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    positions = list(csv.DictReader(args.positions.open(encoding="utf-8", newline="")))
    by_code = {}
    for row in positions:
        by_code.setdefault(row["code"], []).append(row)
    results = []
    events_root = args.events.resolve()
    for event_path in sorted(events_root.glob("*.json")):
        event = json.loads(event_path.read_text(encoding="utf-8"))
        if "source_code" not in event or "effective_date" not in event:
            continue
        code = event.get("source_code", "")
        effective = date.fromisoformat(event["effective_date"])
        prior = [row for row in by_code.get(code, []) if date.fromisoformat(row["trade_date"]) < effective]
        latest = max(prior, key=lambda row: row["trade_date"]) if prior else None
        exposed = latest is not None and float(latest.get("research_value", "0") or 0) > 0
        results.append({
            "event_file": str(event_path.relative_to(ROOT.resolve())),
            "source_code": code,
            "effective_date": event["effective_date"],
            "event_type": event.get("event_type"),
            "strategy_exposed_before_event": exposed,
            "latest_prior_position": latest,
            "settlement_status": "REQUIRES_EVENT_SETTLEMENT" if exposed else "NO_MAIN_STRATEGY_EXPOSURE",
        })
    output = {
        "status": "EXPOSURE_AUDIT_ONLY",
        "event_count": len(results),
        "exposed_event_count": sum(item["strategy_exposed_before_event"] for item in results),
        "results": results,
        "limitations": ["研究positions是研究价值，不是实际股数", "不证明替代停牌口径没有暴露", "不结算换股、零碎股或现金选择权"],
    }
    destination = ROOT / "reports" / "STRATEGY_CORPORATE_ACTION_EXPOSURE_20260924.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "event_count": output["event_count"], "exposed_event_count": output["exposed_event_count"]}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
