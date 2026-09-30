"""分页抓取 zzshare 单日规则字段，保存原始响应和待验证清单。"""
from __future__ import annotations

import argparse, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--page-size", type=int, default=500)
    ap.add_argument("--max-pages", type=int, default=10)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []; files = []; errors = []
    for page in range(args.max_pages):
        offset = page * args.page_size
        query = urlencode({"trade_date": args.date, "limit": args.page_size, "offset": offset, "candle_mode": 0})
        url = f"https://api.zizizaizai.com/v3/market/kline/day?{query}"
        try:
            req = Request(url, headers={"User-Agent": "ashare-lab-rule-probe/0.1"})
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            errors.append({"page": page, "offset": offset, "error": type(exc).__name__ + ": " + str(exc)})
            break
        page_path = args.output / f"page_{page:03d}.json"
        page_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append({"path": page_path.name, "bytes": page_path.stat().st_size, "sha256": sha(page_path)})
        page_rows = ((payload.get("data") or {}).get("list") or [])
        rows.extend(page_rows)
        if len(page_rows) < args.page_size:
            break
        time.sleep(0.5)
    manifest = {
        "status": "PENDING_VALIDATION" if not errors else "FETCH_INCOMPLETE",
        "source": "zzshare",
        "trade_date": args.date,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "page_size": args.page_size,
        "pages": len(files),
        "rows": len(rows),
        "files": files,
        "errors": errors,
        "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "prev_close", "high_limit", "low_limit", "is_paused", "is_st"],
        "limitations": ["待核验历史覆盖、重复记录、服务条款和与第二来源的重叠一致性", "未进入 canonical 或正式回测"],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "rows": len(rows), "pages": len(files), "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
