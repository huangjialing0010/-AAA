"""独立验证 paper replay bundle 的完整性与对账结果。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.bundle / "manifest.json"
    result_path = args.bundle / "result.json"
    errors = []
    if not manifest_path.is_file():
        errors.append("MISSING_MANIFEST")
        manifest = {}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "SEALED":
            errors.append("MANIFEST_NOT_SEALED")
    if not result_path.is_file():
        errors.append("MISSING_RESULT")
        result = {}
    else:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
    entry = next((item for item in entries if item.get("path") == "result.json"), None)
    if entry is None:
        errors.append("RESULT_NOT_IN_MANIFEST")
    elif entry.get("sha256") != sha256(result_path):
        errors.append("RESULT_SHA256_MISMATCH")
    if result.get("reconciliation", {}).get("status") != "BALANCES_MATCH":
        errors.append("RECONCILIATION_NOT_BALANCED")
    if result.get("live_admission") != "BLOCKED":
        errors.append("LIVE_ADMISSION_NOT_BLOCKED")
    status = "PASS" if not errors else "BLOCKED"
    output = {"status": status, "bundle": str(args.bundle), "errors": errors}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
