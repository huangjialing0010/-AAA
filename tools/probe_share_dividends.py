"""对明确指定证券保存分红及复权因子原始响应；不授予账本准入。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.python-packages'))
sys.path.insert(0, str(ROOT / 'src'))
from ashare_lab.sources.baostock_source import RetryingBaoStockSource, market_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codes', nargs='+', required=True)
    parser.add_argument('--year', type=int, required=True)
    args = parser.parse_args()
    codes = list(dict.fromkeys(args.codes))
    for code in codes:
        market_code(code)
    if not 1990 <= args.year < datetime.now().year:
        raise ValueError('探测仅使用已结束的自然年')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'data/imports' / f'baostock_share_dividends_probe_{stamp}'
    folder.mkdir()
    manifest = {'status': 'INCOMPLETE', 'source': 'BaoStock',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'codes': codes, 'year': args.year, 'year_type': 'operate',
                'expected_file_count': len(codes) * 2, 'entries': [],
                'limitations': ['仅接口响应探测，不证明事件完整或到账日准确',
                                '空响应不等于没有公司行动；未经独立验证不得进入账本']}
    # 只在退出时写一次清单；中断留下的无清单目录仍不准入。
    try:
        with RetryingBaoStockSource(max_attempts=2) as source:
            for code in codes:
                for kind in ('dividend', 'adjust_factor'):
                    result = (source.dividend(code, args.year, year_type='operate')
                              if kind == 'dividend' else
                              source.adjust_factor(code, f'{args.year}-01-01', f'{args.year}-12-31'))
                    payload = {'fields': result.fields, 'rows': result.rows}
                    path = folder / f'{code}_{kind}.json'
                    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
                    with path.open('xb') as handle:
                        handle.write(raw)
                    manifest['entries'].append({'path': path.name, 'bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest(), 'code': code,
                        'category': kind, 'rows': len(result.rows),
                        'retrieved_at': datetime.now(timezone.utc).isoformat()})
                    print(f'{code} {kind}: {len(result.rows)} rows', flush=True)
        manifest['status'] = 'COLLECTED_PENDING_INDEPENDENT_VALIDATION'
    finally:
        with (folder / 'manifest.json').open('x', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        print(str(folder), flush=True)


if __name__ == '__main__':
    main()
