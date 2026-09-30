"""审计标准层中行情终止候选；只生成候选，不推断退市事件。"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data" / "canonical" / "stock_selection_v1"
REPORT = ROOT / "reports" / "EVENT_CANDIDATE_AUDIT_20260908.json"
CUTOFF = "2026-08-31"
RESEARCH_START_DATE = "2010-01-01"


def main() -> int:
    manifest = json.loads((CANONICAL / "manifest.json").read_text(encoding="utf-8"))
    membership_counts: Counter[str] = Counter()
    with (CANONICAL / "membership_weekly.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["observed_date"] >= RESEARCH_START_DATE:
                membership_counts[row["code"]] += 1
    reviewed: dict[str, dict[str, object]] = {}
    imports = ROOT / "data" / "imports"
    for path in sorted(list(imports.glob("delisting_events_*/[0-9]*.json")) + list(imports.glob("corporate_actions_*/[0-9]*.json"))):
        event = json.loads(path.read_text(encoding="utf-8"))
        code = event.get("code") or event.get("source_code")
        if code:
            reviewed[str(code)] = {"snapshot": path.parent.name, "event_type": event.get("event_type"), "evidence_status": "EVIDENCE_ONLY"}
    registry = ROOT / "reports" / "EVENT_REVIEW_REGISTRY_20260908.json"
    if registry.is_file():
        for code, event_type in json.loads(registry.read_text(encoding="utf-8")).get("reviewed_codes", {}).items():
            reviewed[str(code)] = {"registry": registry.name, "event_type": event_type, "evidence_status": "EVIDENCE_ONLY"}
    candidates: list[dict[str, object]] = []
    for entry in manifest["price_entries"]:
        path = CANONICAL / entry["path"]
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        last_date = rows[-1]["trade_date"] if rows else ""
        if last_date >= CUTOFF:
            continue
        code = entry["code"]
        evidence = reviewed.get(code, {})
        membership_observations = membership_counts[code]
        candidates.append({
            "code": code,
            "last_observed_date": last_date or "UNKNOWN",
            "row_count": len(rows),
            "halt_rows": sum(row.get("tradestatus") == "0" for row in rows),
            "classification": "EMPTY_PRICE_FILE" if not rows else "REQUIRES_EVENT_SOURCE_REVIEW",
            "research_start_date": RESEARCH_START_DATE,
            "research_membership_observations": membership_observations,
            "research_price_scope": "IN_SCOPE" if membership_observations else "OUTSIDE_SCOPE",
            "event_evidence": evidence or None,
        })
    candidates.sort(key=lambda item: (str(item["last_observed_date"]), str(item["code"])))
    output = {
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canonical_cutoff": CUTOFF,
        "canonical_status": manifest.get("status"),
        "candidate_count": len(candidates),
        "empty_price_file_count": sum(item["classification"] == "EMPTY_PRICE_FILE" for item in candidates),
        "empty_price_file_in_research_scope_count": sum(
            item["classification"] == "EMPTY_PRICE_FILE" and item["research_price_scope"] == "IN_SCOPE"
            for item in candidates
        ),
        "empty_price_file_outside_research_scope_count": sum(
            item["classification"] == "EMPTY_PRICE_FILE" and item["research_price_scope"] == "OUTSIDE_SCOPE"
            for item in candidates
        ),
        "requires_event_source_review_count": sum(item["classification"] == "REQUIRES_EVENT_SOURCE_REVIEW" for item in candidates),
        "reviewed_evidence_count": sum(bool(item["event_evidence"]) for item in candidates),
        "decision": "候选清单仅用于来源核验；不自动推断退市、不替换结算价、不解除账本阻断",
        "candidates": candidates,
    }
    REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
