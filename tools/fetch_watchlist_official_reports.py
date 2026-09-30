#!/usr/bin/env python
"""下载观察名单5份官方2026半年报PDF并封存，仅用于本地核对。"""

from __future__ import annotations

import hashlib
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "docs" / "CURRENT_QUALITY_FORWARD_PILOT_V1.md"
SOURCES = {
    "300033": "https://static.cninfo.com.cn/finalpage/2026-08-22/1225491732.PDF",
    "300760": "https://static.cninfo.com.cn/finalpage/2026-08-29/1225529856.PDF",
    "300896": "https://static.cninfo.com.cn/finalpage/2026-08-21/1225487534.PDF",
    "600674": "https://static.cninfo.com.cn/finalpage/2026-08-15/1225475192.PDF",
    "605499": "https://static.cninfo.com.cn/finalpage/2026-07-31/1225449594.PDF",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_payload(payload: bytes) -> bytes:
    """上交所部分静态文件即使客户端未声明解压也会返回gzip内容。"""
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    return payload


def main() -> int:
    now = datetime.now(timezone.utc)
    output = PROJECT_ROOT / "data" / "imports" / now.strftime("official_watchlist_reports_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for code, url in SOURCES.items():
        path = output / "reports" / f"{code}_2026H1.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 local-research-audit"})
        with urlopen(request, timeout=60) as response:
            payload = decode_payload(response.read())
        with path.open("xb") as handle:
            handle.write(payload)
        if path.stat().st_size < 10_000 or path.read_bytes()[:5] != b"%PDF-":
            raise RuntimeError(f"{code} 下载结果不是有效PDF")
        entries.append({
            "path": path.relative_to(output).as_posix(),
            "stock_code": code,
            "source_url": url,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "official_2026H1_parent_netprofit_crosscheck_for_watchlist",
        "files": len(entries),
        "bytes": sum(item["bytes"] for item in entries),
        "entries": entries,
        "plan": {"path": PLAN_PATH.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256_file(PLAN_PATH)},
        "implementation": {"fetcher_sha256": sha256_file(Path(__file__).resolve())},
        "limits": [
            "官方PDF仅限本地研究核对，不公开再分发",
            "摘要文件只用于核对主要会计数据，不替代全文风险审阅",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SEALED", "snapshot": str(output), "files": len(entries), "bytes": manifest["bytes"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
