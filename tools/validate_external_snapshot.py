"""校验外部行情快照的 manifest、SHA256 与规则字段；失败即阻塞。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ashare_lab.sources.external_snapshot import validate_rule_fields


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
    args = parser.parse_args()
    errors: list[str] = []
    manifest_path = args.root / "manifest.json"
    if not manifest_path.is_file():
        errors.append("MISSING_MANIFEST")
        manifest = {}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "SEALED":
            errors.append("MANIFEST_NOT_SEALED")
    if not args.csv.is_file():
        errors.append("MISSING_CSV")
        fields: set[str] = set()
    else:
        with args.csv.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            if not fields:
                errors.append("MISSING_CSV_FIELDS")
            row_count = 0
            for row_number, row in enumerate(reader, start=2):
                row_count += 1
                try:
                    date.fromisoformat(row.get("trade_date", ""))
                except ValueError:
                    errors.append(f"INVALID_TRADE_DATE:row={row_number}")
                if len(row.get("code", "")) != 6 or not row.get("code", "").isdigit():
                    errors.append(f"INVALID_CODE:row={row_number}")
                for field in ("upper_limit", "lower_limit"):
                    try:
                        if Decimal(row.get(field, "0")) <= 0:
                            raise InvalidOperation
                    except (InvalidOperation, ValueError):
                        errors.append(f"INVALID_{field.upper()}:row={row_number}")
                for field in ("buyable", "sellable"):
                    if row.get(field) not in {"0", "1", "true", "false", "True", "False"}:
                        errors.append(f"INVALID_{field.upper()}:row={row_number}")
            if row_count == 0:
                errors.append("EMPTY_CSV")
        missing = validate_rule_fields(fields)
        errors.extend(f"MISSING_FIELD:{field}" for field in missing)
    manifest_entries = []
    if isinstance(manifest, dict):
        manifest_entries = manifest.get("files", manifest.get("entries", []))
    try:
        relative_csv = args.csv.resolve().relative_to(args.root.resolve()).as_posix()
    except ValueError:
        relative_csv = args.csv.name
    entry = next((item for item in manifest_entries if item.get("path") == relative_csv), None)
    if entry is None and manifest:
        errors.append("CSV_NOT_IN_MANIFEST")
    elif entry is not None and entry.get("sha256") != sha256(args.csv):
        errors.append("SHA256_MISMATCH")
    status = "PASS" if not errors else "BLOCKED"
    output = {"status": status, "root": str(args.root), "csv": str(args.csv), "errors": errors}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
