"""封存302132代码沿革、复权与缺口的原始响应，等待独立验证。"""
from __future__ import annotations
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.python-packages'), str(ROOT / 'src')]
from ashare_lab.sources.baostock_source import RetryingBaoStockSource


def main():
    root = ROOT / 'data/imports' / datetime.now(timezone.utc).strftime('lineage_302132_%Y%m%dT%H%M%SZ')
    root.mkdir(exist_ok=False)
    requests = [
        ('old_history_raw_1', '300114', '2010-01-01', '2015-12-31', '3'),
        ('old_history_raw_2', '300114', '2016-01-01', '2021-12-31', '3'),
        ('old_history_raw_3', '300114', '2022-01-01', '2025-02-14', '3'),
        ('old_gap_raw', '300114', '2012-09-07', '2012-09-11', '3'),
        ('old_gap_qfq', '300114', '2012-09-07', '2012-09-11', '2'),
        ('new_gap_raw', '302132', '2012-09-07', '2012-09-11', '3'),
        ('old_boundary_qfq', '300114', '2025-02-14', '2025-02-14', '2'),
        ('old_boundary_raw', '300114', '2025-02-14', '2025-02-14', '3'),
        ('new_boundary_qfq', '302132', '2025-02-17', '2025-02-17', '2'),
        ('new_boundary_raw', '302132', '2025-02-17', '2025-02-17', '3'),
    ]
    entries = []
    with RetryingBaoStockSource() as source:
        for name, code, start, end, flag in requests:
            result = source.daily(code, start, end, adjustflag=flag)
            entries.append(save(root, name, result, dict(code=code, start=start, end=end, adjustflag=flag)))
            print('evidence ' + name, flush=True)
        result = source.adjust_factor('302132', '2025-02-01', '2026-09-02')
        entries.append(save(root, 'new_factors', result, dict(code='302132', start='2025-02-01', end='2026-09-02')))
    manifest = dict(status='SEALED_PENDING_INDEPENDENT_VALIDATION', source='BaoStock',
                    created_at=datetime.now(timezone.utc).isoformat(), entries=entries,
                    decision='docs/DECISION_20260907_RESEARCH_VALIDATION.md')
    (root/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(str(root), flush=True)


def save(root, name, result, query):
    path = root/(name+'.csv')
    with path.open('x', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=result.fields); w.writeheader(); w.writerows(result.rows)
    return dict(path=path.name, query=query, rows=len(result.rows), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
