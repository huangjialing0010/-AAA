"""重建分页快照清单，保留不完整状态和失败记录。"""
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--snapshot",type=Path,required=True); args=ap.parse_args(); snap=args.snapshot
    files=[]; rows=0
    for p in sorted(list(snap.glob("page_*.json")) + list(snap.glob("tail_*.json"))):
        payload=json.loads(p.read_text(encoding="utf-8")); batch=(payload.get("data") or {}).get("list") or []; rows+=len(batch); h=hashlib.sha256(p.read_bytes()).hexdigest(); files.append({"path":p.name,"bytes":p.stat().st_size,"sha256":h})
    manifest={"status":"PENDING_VALIDATION","source":"zzshare","trade_date":"20260902","retrieved_at":datetime.now(timezone.utc).isoformat(),"pages":len(files),"rows":rows,"files":files,"errors":[],"fields":["ts_code","trade_date","open","high","low","close","prev_close","high_limit","low_limit","is_paused","is_st"],"limitations":["待验证全市场覆盖、重复、许可和跨源一致性","未进入 canonical 或正式回测"]}
    (snap/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps({"pages":len(files),"rows":rows,"status":manifest["status"]},ensure_ascii=False))
if __name__=="__main__": main()
