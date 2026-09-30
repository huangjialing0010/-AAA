"""验证事件复核登记表与行情终止候选清单的一致性。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    registry = json.loads((ROOT / "reports/EVENT_REVIEW_REGISTRY_20260908.json").read_text(encoding="utf-8"))
    candidates = json.loads((ROOT / "reports/EVENT_CANDIDATE_AUDIT_20260908.json").read_text(encoding="utf-8"))
    candidate_codes = {item["code"] for item in candidates.get("candidates", [])}
    reviewed_codes = set(registry.get("reviewed_codes", {}))
    unknown = sorted(reviewed_codes - candidate_codes)
    output = {"status": "PASS" if not unknown else "FAIL", "candidate_count": len(candidate_codes), "reviewed_count": len(reviewed_codes), "unknown_registry_codes": unknown}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not unknown else 1


if __name__ == "__main__":
    raise SystemExit(main())
