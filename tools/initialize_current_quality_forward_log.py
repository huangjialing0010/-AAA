#!/usr/bin/env python
"""依据观察名单准入决定初始化前向事件日志；不生成订单或成交。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "docs" / "CURRENT_QUALITY_FORWARD_LOG_CONTRACT_V1.md"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def entry(root: Path, path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("admission", type=Path)
    args = parser.parse_args()
    admission_path = args.admission.resolve()
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    if admission.get("status") != "ADMITTED_FOR_FORWARD_OBSERVATION_ONLY":
        raise RuntimeError("观察名单未准入前向观察")
    report = PROJECT_ROOT / "reports" / admission["watchlist_report"]
    watch_path = report / "watchlist.csv"
    watchlist = read_csv(watch_path)
    if {row["stock_code"] for row in watchlist} != set(admission["watchlist_codes"]):
        raise RuntimeError("准入决定与观察名单代码不一致")

    now = datetime.now(timezone.utc)
    output = PROJECT_ROOT / "reports" / now.strftime("current_quality_forward_log_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    signal_fields = [
        "signal_id", "recorded_at", "observed_date", "stock_code", "name", "mechanism",
        "event_type", "actionable", "reason", "rule_version", "source_watchlist",
    ]
    signals = [{
        "signal_id": f"BASELINE:{row['mechanism']}:{row['stock_code']}:{admission['data_cutoff']}",
        "recorded_at": admission["admitted_at"],
        "observed_date": admission["data_cutoff"],
        "stock_code": row["stock_code"],
        "name": row["name"],
        "mechanism": row["mechanism"],
        "event_type": "BASELINE_PRE_FREEZE",
        "actionable": "false",
        "reason": "PRE_FREEZE_NO_BACKFILL",
        "rule_version": "CURRENT_QUALITY_FORWARD_PILOT_V1",
        "source_watchlist": admission["watchlist_report"],
    } for row in watchlist]
    schemas = {
        "signals.csv": signal_fields,
        "orders.csv": ["order_id", "signal_id", "planned_trade_date", "stock_code", "side", "target_weight", "requested_shares", "status", "created_at"],
        "trades.csv": ["trade_id", "order_id", "trade_date", "stock_code", "side", "shares", "price_unadjusted", "commission", "stamp_tax", "slippage", "total_cash_change"],
        "rejections.csv": ["rejection_id", "order_id", "signal_id", "stock_code", "rejected_at", "reason", "evidence"],
        "positions.csv": ["as_of", "stock_code", "shares", "sellable_shares", "cost_basis", "market_price_unadjusted", "market_value"],
        "daily_reconciliation.csv": ["as_of", "cash", "positions_market_value", "equity", "position_count", "order_count", "trade_count", "status", "note"],
    }
    rows_by_name: dict[str, list[dict[str, Any]]] = {
        "signals.csv": signals,
        "orders.csv": [],
        "trades.csv": [],
        "rejections.csv": [],
        "positions.csv": [],
        "daily_reconciliation.csv": [{
            "as_of": admission["freeze_date"], "cash": "", "positions_market_value": "", "equity": "",
            "position_count": 0, "order_count": 0, "trade_count": 0,
            "status": "NOT_APPLICABLE_CAPITAL_UNSET", "note": "CAPITAL_AND_EXIT_RULES_UNSET",
        }],
    }
    entries = []
    for name, fields in schemas.items():
        path = output / name
        write_csv(path, fields, rows_by_name[name])
        entries.append(entry(output, path, len(rows_by_name[name])))
    manifest = {
        "schema_version": 1,
        "status": "FROZEN_WAITING_FOR_POST_FREEZE_SIGNAL",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "freeze_date": admission["freeze_date"],
        "historical_data_cutoff": admission["data_cutoff"],
        "watchlist_count": len(watchlist),
        "baseline_signal_count": len(signals),
        "actionable_signal_count": 0,
        "order_count": 0,
        "trade_count": 0,
        "position_count": 0,
        "execution_status": "CAPITAL_AND_EXIT_RULES_UNSET",
        "lineage": {
            "admission_path": str(admission_path),
            "admission_sha256": sha256_file(admission_path),
            "watchlist_csv_sha256": sha256_file(watch_path),
        },
        "contract": {"path": CONTRACT_PATH.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256_file(CONTRACT_PATH)},
        "implementation": {"initializer_sha256": sha256_file(Path(__file__).resolve())},
        "entries": entries,
        "next_action": "REFRESH_AFTER_FIRST_POST_FREEZE_COMPLETED_WEEK",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "output": str(output), "baseline_signals": len(signals), "orders": 0, "trades": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
