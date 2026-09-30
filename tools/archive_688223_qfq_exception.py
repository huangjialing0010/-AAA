"""另存688223异常双口径原始响应，只作证据，不作为可用行情快照。"""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'data/imports/baostock_historical_daily_20260904T014923Z'
    files = ['daily_qfq/688223/2022-01-01_2026-09-02.csv',
             'daily_unadjusted/688223/2022-01-01_2026-09-02.csv']
    for name in files:
        if not (source/name).is_file(): raise ValueError('证据响应尚未下载')
    target = ROOT/'data/imports'/datetime.now(timezone.utc).strftime('qfq_exception_688223_%Y%m%dT%H%M%SZ')
    target.mkdir(exist_ok=False)
    entries = []
    for name in files:
        original = source/name
        destination = target/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError('证据复制哈希不一致')
        entries.append(dict(path=name, source_snapshot=source.name, sha256=digest,
                            bytes=destination.stat().st_size,
                            query=dict(code='688223', start='2022-01-01', end='2026-09-02',
                                       adjustflag='2' if name.startswith('daily_qfq') else '3')))
    manifest = dict(status='EVIDENCE_ONLY_NOT_MARKET_DATA', entries=entries,
                    missing_qfq_date='2022-01-26', copied_at=datetime.now(timezone.utc).isoformat(),
                    decision='docs/DECISION_20260908_688223_MISSING_QFQ.md')
    with (target/'manifest.json').open('x', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(evidence_snapshot=str(target), files=len(entries), copy_hashes='PASS')))


if __name__ == '__main__':
    main()
