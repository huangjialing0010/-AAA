"""保存已发现官方年报证据；不推断股票资格。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import gzip
import argparse

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ('605499', '2026-03-31', 'https://static.cninfo.com.cn/finalpage/2026-03-31/1225063528.PDF'),
    ('600674', '2026-04-18', 'https://static.cninfo.com.cn/finalpage/2026-04-18/1225117474.PDF'),
)
POWER_SOURCES = (
    ('600886', '2026-04-30', 'https://static.cninfo.com.cn/finalpage/2026-04-30/1225257588.PDF'),
    ('600886', '2026-08-28', 'https://static.cninfo.com.cn/finalpage/2026-08-28/1225520619.PDF'),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=('base', 'power'), default='base')
    args = parser.parse_args()
    output = ROOT / 'data/imports' / datetime.now(timezone.utc).strftime('manual_candidate_financial_%Y%m%dT%H%M%SZ')
    output.mkdir(exist_ok=False)
    entries = []
    for code, published, url in (POWER_SOURCES if args.profile == 'power' else SOURCES):
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 local-research'}), timeout=30) as response:
            data = response.read()
        if data.startswith(b'\x1f\x8b'):
            data = gzip.decompress(data)
        if not data.startswith(b'%PDF-'):
            raise ValueError('响应不是PDF')
        name = url.rsplit('/', 1)[-1]
        with (output/name).open('xb') as handle:
            handle.write(data)
        entries.append(dict(path=name, code=code, disclosed_on=published, url=url, bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest()))
        print(code, name, len(data), flush=True)
    manifest = dict(status='SEALED', purpose='CURRENT_FINANCIAL_EVIDENCE_NOT_HISTORICAL',
                    created_at=datetime.now(timezone.utc).isoformat(), entries=entries,
                    limitations=['摘要不替代完整风险审阅', '比较列仅按本次披露日用于前向判断'])
    with (output/'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(output)


if __name__ == '__main__':
    main()
