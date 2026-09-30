"""核对当前已知未完成的启动依赖；不提供跳过检查的参数。"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ashare_lab.selection.manual_startup import startup_check


def main():
    opening_path = ROOT / 'reports/manual_paper_20260929T055058319048Z/opening.json'
    opening = json.loads(opening_path.read_text(encoding='utf-8'))
    manifest = json.loads((opening_path.parent / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['entries'].items():
        if hashlib.sha256((opening_path.parent / name).read_bytes()).hexdigest() != expected:
            raise ValueError('开户文件哈希不符')
    snapshots = sorted((ROOT / 'data/imports').glob('baostock_current_quality_valuation_*'))
    snapshot = snapshots[-1]
    path = snapshot / 'manifest.json'
    price_manifest = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    # 当前尚未生成这些正式证据；不能使用观察名单准入代替。
    result = startup_check(strategy_frozen=False, eligibility_verified=False,
                           execution_connected=False,
                           snapshot_sealed=price_manifest.get('status') == 'SEALED',
                           verified_data_cutoff=None, required_data_cutoff='2026-09-28')
    now = datetime.now(timezone.utc)
    result.update(account_id=opening['account_id'], checked_at=now.isoformat(),
                  type='STARTUP_PREFLIGHT_NOT_STRATEGY_RUN',
                  required_data_cutoff='2026-09-28',
                  requested_cutoff_basis='PREVIOUS_CALENDAR_DATE_NOT_TRADING_CALENDAR_VALIDATION',
                  snapshot=str(snapshot),
                  opening_sha256=hashlib.sha256(opening_path.read_bytes()).hexdigest(),
                  snapshot_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  checker_sha256=hashlib.sha256((ROOT/'src/ashare_lab/selection/manual_startup.py').read_bytes()).hexdigest())
    output = ROOT / 'reports' / now.strftime('manual_startup_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    with (output / 'startup.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(output)
    return 2 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
