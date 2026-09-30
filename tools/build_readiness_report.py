"""生成虚拟盘准入状态汇总；不改变任何策略或数据。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def compute_candidate_gates(candidate: dict) -> tuple[dict[str, str], dict[str, int]]:
    items = list(candidate.get("candidates") or [])
    candidate_count = int(candidate.get("candidate_count") or len(items))
    empty_count = int(candidate.get("empty_price_file_count") or 0)
    reviewed_count = int(candidate.get("reviewed_evidence_count") or 0)
    empty_items = [item for item in items if item.get("classification") == "EMPTY_PRICE_FILE"]
    empty_in_scope_count = sum(
        int(item.get("research_membership_observations", 1)) > 0 for item in empty_items
    )
    empty_outside_scope_count = len(empty_items) - empty_in_scope_count
    non_empty_items = [item for item in items if item.get("classification") != "EMPTY_PRICE_FILE"]
    non_empty_count = len(non_empty_items)
    reviewed_non_empty_count = sum(bool(item.get("event_evidence")) for item in non_empty_items)
    gates = {
        "price_history_coverage": "PASS" if empty_in_scope_count == 0 else "BLOCKED",
        "non_empty_event_evidence": (
            "PASS"
            if non_empty_count > 0 and reviewed_non_empty_count == non_empty_count
            else ("INCOMPLETE" if reviewed_non_empty_count else "BLOCKED")
        ),
        "event_evidence": (
            "PASS"
            if candidate_count > 0 and reviewed_count == candidate_count
            else ("INCOMPLETE" if reviewed_count else "BLOCKED")
        ),
    }
    counts = {
        "candidate_count": candidate_count,
        "empty_price_file_count": empty_count,
        "empty_price_file_in_research_scope_count": empty_in_scope_count,
        "empty_price_file_outside_research_scope_count": empty_outside_scope_count,
        "reviewed_evidence_count": reviewed_count,
        "non_empty_candidate_count": non_empty_count,
        "reviewed_non_empty_count": reviewed_non_empty_count,
    }
    return gates, counts


def main() -> int:
    canonical = read_json(ROOT / "data/canonical/stock_selection_v1/manifest.json")
    candidate = read_json(ROOT / "reports/EVENT_CANDIDATE_AUDIT_20260908.json")
    portfolio = read_json(ROOT / "reports/QLIB_ALPHA_PORTFOLIO_SMOKE_100_20260908.json")
    feature = read_json(ROOT / "reports/QLIB_FEATURE_SAMPLE_AUDIT_100_20260908.json")
    kbar = read_json(ROOT / "reports/QLIB_KBAR_SAMPLE_AUDIT_100_20260908.json")
    rolling = read_json(ROOT / "reports/QLIB_ROLLING_SAMPLE_AUDIT_100_20260908.json")
    corporate = read_json(ROOT / "reports/QLIB_ALPHA_SUBSET_EXPERIMENT_100_V2_20260908.json")
    event_snapshots = len(list((ROOT / "data/imports").glob("delisting_events_*/manifest.json")))
    action_snapshots = len(list((ROOT / "data/imports").glob("corporate_actions_*/manifest.json")))
    candidate_gates, counts = compute_candidate_gates(candidate)
    candidate_count = counts["candidate_count"]
    reviewed_count = counts["reviewed_evidence_count"]
    gates = {
        "canonical_data": "PASS" if canonical.get("status") == "SEALED" else "BLOCKED",
        "feature_audits": "PASS" if all(report.get("status") == "PASS" for report in (feature, kbar, rolling)) else "BLOCKED",
        "model_research": "PASS" if corporate.get("status") == "PASS" else "BLOCKED",
        **candidate_gates,
        "portfolio_smoke": portfolio.get("status", "MISSING"),
        "strategy_candidate": portfolio.get("strategy_decision", "NOT_EVALUATED"),
    }
    strategy_decision = portfolio.get("strategy_decision", "NOT_EVALUATED")
    portfolio_block = None
    if portfolio.get("status") != "PASS":
        portfolio_block = {"error_code": portfolio.get("error_code"), "blocking_trade_date": portfolio.get("blocking_trade_date"), "stale_days": portfolio.get("stale_days"), "block_reason": portfolio.get("block_reason", "UNKNOWN"), "error": portfolio.get("error"), "decision": portfolio.get("decision")}
    output = {
        "status": "NOT_READY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": gates,
        "data": {"canonical_status": canonical.get("status"), **counts, "review_coverage": (reviewed_count / candidate_count if candidate_count else 0), "delisting_snapshot_count": event_snapshots, "corporate_action_snapshot_count": action_snapshots},
        "portfolio_block": portfolio_block,
        "strategy_evaluation": {
            "decision": strategy_decision,
            "portfolio_metrics": portfolio.get("metrics"),
            "benchmark": portfolio.get("benchmark"),
            "benchmark_metrics": portfolio.get("benchmark_metrics"),
            "active_total_return": portfolio.get("active_total_return"),
            "active_annualized_return": portfolio.get("active_annualized_return"),
        },
        "decision": (
            "当前 Qlib 风格候选被510300基准全面支配，不晋级虚拟盘；继续验证已通过历史留出闸门的12-1动量与低波动候选"
            if strategy_decision == "REJECTED_BENCHMARK_DOMINATED"
            else "暂不进入虚拟盘；继续完成策略与账本验证"
        ),
    }
    path = ROOT / "reports/READINESS_GATE_STATUS_20260908.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
