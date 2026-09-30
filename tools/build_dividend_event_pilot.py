"""把四笔已核对公告整理成阻断状态的标准分红事件，不直接接入账本。"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = [
    ('600036', '2024-annual', '2025-07-10', '2025-07-11', '2025-07-11', '2.000', '2025070301746_c.pdf'),
    ('601857', '2024-annual', '2025-06-24', '2025-06-25', '2025-06-25', '0.25', '2025061600751_c.pdf'),
    ('601857', '2025-interim', '2025-09-16', '2025-09-17', '2025-09-17', '0.22', '2025090801098_c.pdf'),
    ('688009', '2024-annual', '2025-07-24', '2025-07-25', '2025-07-25', '0.17', '2025071700958_c.pdf'),
]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'COLLECTED_PENDING_INDEPENDENT_VALIDATION':
        raise ValueError('原始公告目录尚未处于可整理状态')
    output = ROOT / 'data/canonical' / 'dividend_events_pilot'
    output.mkdir(exist_ok=False)
    events = []
    for code, plan, register, ex, pay, gross, pdf in EVENTS:
        entry = next((item for item in manifest['entries'] if item['path'] == pdf), None)
        if entry is None:
            raise ValueError(f'公告未在清单中: {pdf}')
        event_id = f'{code}:DIVIDEND:{plan}:{ex}'
        events.append({
            'event_id': event_id, 'event_type': 'CASH_DIVIDEND', 'code': code,
            'plan_label': plan, 'register_date': register, 'ex_date': ex, 'pay_date': pay,
            'gross_cash_per_share': gross,
            'default_a_share_cash_credit_per_share': gross,
            'tax_status': 'DEFERRED_OR_PROFILE_DEPENDENT',
            'bonus_shares_per_share': '0' if code == '688009' else 'UNKNOWN',
            'capital_reserve_shares_per_share': '0' if code == '688009' else 'UNKNOWN',
            'source_notice': {'path': f'data/imports/{source.name}/{pdf}', 'sha256': entry['sha256']},
            'evidence_class': 'LOCAL_PRIMARY_NOTICE_FIELD_CHECK',
            'ledger_admission': 'BLOCKED_UNTIL_TAX_PROFILE_AND_LOT_SCOPE',
        })
    payload = {'schema_version': 1, 'status': 'CANONICAL_PILOT_BLOCKED',
               'generated_at': datetime.now(timezone.utc).isoformat(),
               'source_snapshot': f'data/imports/{source.name}', 'events': events,
               'limitations': ['仅四笔公告样本', '未证明完整历史覆盖', '税务配置未确定，不可直接作为账户现金事件']}
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    path = output / 'events.json'
    path.write_bytes(raw)
    out_manifest = {'status': payload['status'], 'expected_file_count': 1,
                    'entries': [{'path': path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    (output / 'manifest.json').write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': payload['status'], 'event_count': len(events), 'path': str(path)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
