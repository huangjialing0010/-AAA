"""保存已人工核对的四份官方公告原件，不覆盖既有快照。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ('600036', '2025-07-11', 'https://www.hkexnews.hk/listedco/listconews/sehk/2025/0703/2025070301746_c.pdf'),
    ('601857', '2025-06-25', 'https://www.hkexnews.hk/listedco/listconews/sehk/2025/0616/2025061600751_c.pdf'),
    ('601857', '2025-09-17', 'https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0908/2025090801098_c.pdf'),
    ('688009', '2025-07-25', 'https://www.hkexnews.hk/listedco/listconews/sehk/2025/0717/2025071700958_c.pdf'),
]


def main():
    folder = ROOT / 'data/imports' / datetime.now(timezone.utc).strftime('dividend_notice_pilot_%Y%m%dT%H%M%S%fZ')
    folder.mkdir()
    manifest = {'status': 'INCOMPLETE', 'expected_file_count': len(SOURCES),
                'created_at': datetime.now(timezone.utc).isoformat(), 'entries': [],
                'limitations': ['公告样本不证明全历史覆盖', '原件采集不等于账本准入']}
    try:
        for code, ex_date, url in SOURCES:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as response:
                raw = response.read(10_000_001)
                final_url = response.url
            if len(raw) > 10_000_000 or not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-2048:]:
                raise ValueError('响应不是完整的小型PDF，拒绝保存为公告')
            path = folder / url.rsplit('/', 1)[1]
            with path.open('xb') as handle:
                handle.write(raw)
            manifest['entries'].append({'path': path.name, 'source_url': url,
                'final_url': final_url, 'code': code, 'ex_date': ex_date,
                'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'retrieved_at': datetime.now(timezone.utc).isoformat()})
            print(f'{code} {ex_date}: {len(raw)} bytes', flush=True)
        manifest['status'] = 'COLLECTED_PENDING_INDEPENDENT_VALIDATION'
    finally:
        with (folder / 'manifest.json').open('x', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        print(folder, flush=True)


if __name__ == '__main__':
    main()
