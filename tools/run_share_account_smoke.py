"""保存合成账户端到端演练；不是历史收益或前向交易。"""
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill
from ashare_lab.selection.cash_dividend_account import CashDividendAccount, apply_dividends
from ashare_lab.selection.dividend_registration import register_dividend
from ashare_lab.selection.order_gate import ExecutionRules
from ashare_lab.selection.checked_execution import apply_checked_execution
from ashare_lab.selection.account_reconciliation import reconcile
from ashare_lab.selection.fees import FeeRuleProfile


def run():
    account = CashDividendAccount(ShareAccount(D(2000)))
    fee_profile = FeeRuleProfile('SYNTHETIC_FEE_V1', date(2025, 1, 1), D('0.0003'), D('5'), D('0.0005'))
    records = []
    def execute(fill, signal, tradable=True):
        nonlocal account
        rules = ExecutionRules(fill.day, fill.code, 'SYNTHETIC_RULES', 100, 100,
                               D(9), D(11), 'ROUND100_WITH_WHOLE_REMAINDER')
        account, decision, sales = apply_checked_execution(account, fill, signal_day=signal,
            rules=rules, tradable=tradable, execution_evidence_id='SYNTHETIC_EXECUTION',
            fee_profile=fee_profile)
        records.append({'type': 'ORDER_RESULT', 'input': asdict(fill),
                        'signal_day': signal, 'rules': asdict(rules), 'tradable': tradable,
                        'decision': asdict(decision), 'sales': sales,
                        'cash_after': account.shares.cash})
    execute(Fill('buy', '600036', 'BUY', date(2025, 7, 9), 0, 100, D(10), D(5),
                 date(2025, 7, 10)), date(2025, 7, 8))
    execute(Fill('blocked', '600036', 'BUY', date(2025, 7, 10), 0, 100, D(10), D(5),
                 date(2025, 7, 11)), date(2025, 7, 9), False)
    registered = register_dividend(event_id='SYNTHETIC_DIVIDEND', code='600036',
        register_date=date(2025, 7, 10), ex_date=date(2025, 7, 11), pay_date=date(2025, 7, 11),
        gross_per_share=D(2), payment_per_share=D(2), evidence_id='SYNTHETIC',
        snapshot_date=date(2025, 7, 10), close_snapshot_id='SYNTHETIC_CLOSE', lots=account.shares.lots)
    account, audit = apply_dividends(account, date(2025, 7, 11), [registered.entitlement])
    records.append({'type': 'DIVIDEND', 'registration': asdict(registered), 'audit': audit,
                    'cash_after': account.shares.cash})
    execute(Fill('sell', '600036', 'SELL', date(2025, 7, 11), 0, 100, D(10), D('5.50')), date(2025, 7, 10))
    reconciliation = reconcile(account, D(2000))
    checks = {'cash_matches_hand_calculation': account.shares.cash == D('2189.50'),
              'positions_closed': not account.shares.lots,
              'rejection_recorded': records[1]['decision']['status'] == 'REJECTED',
              'independent_balances_match': reconciliation['status'] == 'BALANCES_MATCH'}
    return {'schema_version': 2, 'initial_cash': '2000',
            'status': 'PASS' if all(checks.values()) else 'FAIL',
            'evidence_class': 'SYNTHETIC_INTEGRATION_ONLY', 'checks': checks,
            'records': records, 'reconciliation': reconciliation,
            'final_account': asdict(account), 'live_admission': 'BLOCKED',
            'limitations': ['非真实市场成交，不能用于收益评价', '未处理递延税、撮合和实际到账时段',
                            '仅保存演练结果，尚无生产账户恢复或自动调度']}


if __name__ == '__main__':
    report = run()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'reports' / f'share_account_smoke_{stamp}'
    folder.mkdir()
    def encode(value):
        if isinstance(value, (D, date)): return str(value)
        if isinstance(value, frozenset): return sorted(value)
        raise TypeError(type(value).__name__)
    raw = json.dumps(report, ensure_ascii=False, indent=2, default=encode).encode('utf-8')
    with (folder / 'result.json').open('xb') as handle: handle.write(raw)
    manifest = {'evidence_class': report['evidence_class'], 'entries': [
        {'path': 'result.json', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    with (folder / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'checks': report['checks']}, ensure_ascii=False))
    print(folder)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)
