"""为规则快照原始CSV生成可追溯 manifest；不修改CSV内容。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--connector-version", required=True)
    args = parser.parse_args()
    with args.csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = sum(1 for _ in reader)
    relative = args.csv.resolve().relative_to(args.root.resolve()).as_posix()
    manifest = {
        "schema_version": 1,
        "status": "COLLECTED_PENDING_INDEPENDENT_VALIDATION",
        "source": args.source,
        "connector_version": args.connector_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expected_file_count": 1,
        "fields": fields,
        "entries": [{"path": relative, "bytes": args.csv.stat().st_size,
                     "sha256": sha256(args.csv), "rows": rows}],
    }
    destination = args.root / "manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "rows": rows, "path": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
