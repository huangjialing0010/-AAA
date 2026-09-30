"""运行已封存规则快照上的 paper replay；不连接券商。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.selection.cash_dividend_account import CashDividendAccount  # noqa: E402
from ashare_lab.selection.fees import HistoricalAshareFeeProfile  # noqa: E402
from ashare_lab.selection.paper_replay import replay_orders  # noqa: E402
from ashare_lab.selection.share_account import ShareAccount  # noqa: E402
from ashare_lab.selection.account_reconciliation import reconcile  # noqa: E402
from ashare_lab.sources.rule_snapshot import load_rule_snapshot  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--effective-from", default="2020-01-01")
    parser.add_argument("--stamp-tax-change-date", default="2023-08-28")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bundle-dir", type=Path)
    args = parser.parse_args()
    if not args.rules.is_file():
        print(json.dumps({"status": "BLOCKED_MISSING_RULE_SNAPSHOT", "rules": str(args.rules)}, ensure_ascii=False))
        return 2
    manifest_path = args.rules.parent / "manifest.json"
    if not manifest_path.is_file():
        print(json.dumps({"status": "BLOCKED_MISSING_RULE_MANIFEST", "manifest": str(manifest_path)}, ensure_ascii=False))
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED":
        print(json.dumps({"status": "BLOCKED_RULE_MANIFEST_NOT_SEALED", "manifest_status": manifest.get("status")}, ensure_ascii=False))
        return 2
    relative = args.rules.resolve().relative_to(args.rules.parent.resolve()).as_posix()
    entries = manifest.get("entries", manifest.get("files", []))
    entry = next((item for item in entries if item.get("path") == relative), None)
    if entry is None or entry.get("sha256") != sha256(args.rules):
        print(json.dumps({"status": "BLOCKED_RULE_MANIFEST_HASH_MISMATCH", "path": relative}, ensure_ascii=False))
        return 2
    with args.orders.open(encoding="utf-8", newline="") as handle:
        orders = list(csv.DictReader(handle))
    rules = load_rule_snapshot(args.rules)
    days = sorted({day for _, day in rules})
    next_trade_day = {days[index]: days[index + 1] for index in range(len(days) - 1)}
    profile = HistoricalAshareFeeProfile(
        "HISTORICAL_PAPER_V1", date.fromisoformat(args.effective_from), Decimal("0.0003"),
        Decimal("5"), Decimal("0.001"), Decimal("0.0005"), date.fromisoformat(args.stamp_tax_change_date)
    )
    account, decisions = replay_orders(
        orders=orders, rules_by_key=rules,
        account=CashDividendAccount(ShareAccount(Decimal(args.initial_cash))),
        fee_profile=profile, next_trade_day=next_trade_day,
    )
    reconciliation = reconcile(account, Decimal(args.initial_cash))
    reconciliation["expected_cash"] = str(reconciliation["expected_cash"])
    output = {
        "status": "PAPER_REPLAY_COMPLETED_WITH_DECISIONS",
        "order_count": len(orders),
        "booked_count": sum(decision.status == "BOOKED" for decision in decisions),
        "rejected_count": sum(decision.status == "REJECTED" for decision in decisions),
        "decision_reasons": {},
        "final_cash": str(account.shares.cash),
        "reconciliation": reconciliation,
        "decisions": [{"fill_id": item.fill_id, "status": item.status, "reason": item.reason,
                       "rule_evidence_id": item.rule_evidence_id,
                       "execution_evidence_id": item.execution_evidence_id} for item in decisions],
        "final_lots": [{"lot_id": lot.lot_id, "code": lot.code, "shares": lot.shares,
                        "acquired_on": str(lot.acquired_on), "sellable_on": str(lot.sellable_on)}
                       for lot in account.shares.lots],
        "live_admission": "BLOCKED",
    }
    for decision in decisions:
        output["decision_reasons"][decision.reason] = output["decision_reasons"].get(decision.reason, 0) + 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.bundle_dir:
        args.bundle_dir.mkdir(parents=True, exist_ok=True)
        result_path = args.bundle_dir / "result.json"
        raw = (json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        result_path.write_bytes(raw)
        manifest = {"status": "SEALED", "evidence_class": "PAPER_REPLAY_AUDIT",
                    "entries": [{"path": "result.json", "bytes": len(raw),
                                  "sha256": hashlib.sha256(raw).hexdigest()}]}
        (args.bundle_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
