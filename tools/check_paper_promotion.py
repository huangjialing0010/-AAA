"""检查研究结果是否满足纸面账户升级条件；缺任一硬闸门即阻塞。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED = {
    "research_validation": "PASS",
    "shadow_replay": "PASS",
    "forward_validation": "PASS",
    "external_connector_authorization": "PASS",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.readiness.read_text(encoding="utf-8"))
    missing = []
    for key, expected in REQUIRED.items():
        value = payload.get(key)
        if value != expected:
            missing.append({"gate": key, "actual": value, "required": expected})
    status = "PROMOTION_ALLOWED" if not missing else "PROMOTION_BLOCKED"
    output = {
        "status": status,
        "readiness": str(args.readiness),
        "missing_gates": missing,
        "decision": "不得创建纸面账户或发送订单" if missing else "允许进入纸面账户审批流程",
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if status == "PROMOTION_ALLOWED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
