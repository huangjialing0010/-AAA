"""核对已验收研究运行，盘点真实股数账本所需数据；不推断公司行动。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path):
    with path.open(encoding='utf-8', newline='') as handle:
        yield from csv.DictReader(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    if run.parent != (ROOT / 'reports').resolve():
        raise ValueError('运行目录必须位于本项目 reports 下')
    manifest_path = run / 'run_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    validation = manifest.get('independent_validation', {})
    if manifest.get('status') != 'SEALED' or validation.get('status') != 'PASS':
        raise ValueError('研究运行尚未封存并通过独立验收')
    quality_path = (ROOT / validation['report']).resolve()
    if quality_path.parent != (ROOT / 'reports').resolve():
        raise ValueError('验收报告路径不在 reports 下')
    quality = json.loads(quality_path.read_text(encoding='utf-8'))
    if quality.get('status') != 'PASS' or quality.get('run') != run.name:
        raise ValueError('验收报告与运行不匹配')
    for entry in manifest['entries']:
        path = (run / entry['path']).resolve()
        if path.parent != run or not path.is_file():
            raise ValueError('运行文件路径错误或不存在')
        if path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
            raise ValueError(f'运行文件发生变动: {entry["path"]}')

    target_sets = {
        str(count): sorted({row['code'] for row in rows(run / f'targets_{count}.csv')})
        for count in (20, 30, 40)
    }
    held_spans = {}
    for row in rows(run / 'portfolio_30_positions.csv'):
        code, day = row['code'], row['trade_date']
        span = held_spans.setdefault(code, {'first_observed_position': day, 'last_observed_position': day, 'position_days': 0})
        span['first_observed_position'] = min(span['first_observed_position'], day)
        span['last_observed_position'] = max(span['last_observed_position'], day)
        span['position_days'] += 1
    candidates = sorted({row['code'] for row in rows(run / 'factor_candidates.csv')})
    evidence = {}
    for pattern in ('corporate_actions_*', 'delisting_events_*'):
        for folder in sorted((ROOT / 'data/imports').glob(pattern)):
            for path in sorted(folder.glob('[0-9]*.json')):
                event = json.loads(path.read_text(encoding='utf-8'))
                code = event.get('source_code') or event.get('code')
                if code:
                    evidence.setdefault(code, []).append({
                        'path': path.relative_to(ROOT).as_posix(),
                        'sha256': sha256(path),
                        'event_type': event.get('event_type'),
                        'settlement_readiness': 'NOT_ASSESSED_BY_THIS_INVENTORY',
                    })
    output = {
        'status': 'INVENTORY_COMPLETE_NOT_LEDGER_READY',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'run': run.name,
        'run_manifest_sha256': sha256(manifest_path),
        'quality_report_sha256': sha256(quality_path),
        'research_gate': manifest['research_gate'],
        'target_code_counts': {key: len(value) for key, value in target_sets.items()},
        'main_held_code_count': len(held_spans),
        'factor_candidate_code_count': len(candidates),
        'main_held_codes_with_event_files': sorted(set(held_spans) & set(evidence)),
        'main_held_codes_without_event_files': sorted(set(held_spans) - set(evidence)),
        'target_codes': target_sets,
        'main_held_spans': dict(sorted(held_spans.items())),
        'available_event_files': evidence,
        'required_evidence': [
            '逐证券现金分红登记日、除权日、到账日、税额及来源',
            '送转、配股、换股的数量比例和可卖日期及零碎股规则',
            '逐日真实股数、可卖股数、现金、应收款及独立对账',
        ],
        'limitations': [
            '没有事件文件不代表没有公司行动；已有事件文件也不证明分红覆盖完整',
            '持仓日期仅用于工作量排序，不能作为未来删除候选或忽略事件的依据',
            '前复权收益不能直接换算成真实股数；当前研究成绩不等于可交易账户成绩',
        ],
    }
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = ROOT / 'reports' / f'SHARE_LEDGER_INPUT_INVENTORY_{stamp}.json'
    with destination.open('x', encoding='utf-8') as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: output[key] for key in ('status', 'run', 'target_code_counts', 'main_held_code_count', 'factor_candidate_code_count', 'main_held_codes_with_event_files')}, ensure_ascii=False))
    print(destination)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
