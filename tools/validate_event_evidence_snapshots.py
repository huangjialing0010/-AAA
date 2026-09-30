"""验证退市事件证据快照的清单、哈希与结构。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_lab.selection.events import validate_event_evidence  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    roots = sorted((ROOT / "data" / "imports").glob("delisting_events_*/"))
    checked = 0
    errors: list[str] = []
    for snapshot in roots:
        manifest_path = snapshot / "manifest.json"
        if not manifest_path.is_file():
            errors.append(f"{snapshot.name}: 缺少 manifest.json")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "SEALED":
            errors.append(f"{snapshot.name}: 状态不是 SEALED")
        for entry in manifest.get("files", []):
            path = snapshot / entry["path"]
            checked += 1
            if not path.is_file():
                errors.append(f"{snapshot.name}/{entry['path']}: 文件不存在")
                continue
            if path.stat().st_size != entry.get("bytes") or sha256(path) != entry.get("sha256"):
                errors.append(f"{snapshot.name}/{entry['path']}: 哈希或字节数不匹配")
                continue
            try:
                validate_event_evidence(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{snapshot.name}/{entry['path']}: {exc}")
    output = {"status": "PASS" if not errors else "FAIL", "snapshot_count": len(roots), "file_count": checked, "errors": errors}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
