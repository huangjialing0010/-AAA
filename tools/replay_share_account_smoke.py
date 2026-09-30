"""从已保存的合成演练输入重放，校验每步输出；不是独立撮合验证。"""
import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill
from ashare_lab.selection.cash_dividend_account import CashDividendAccount, apply_dividends
from ashare_lab.selection.checked_execution import apply_checked_execution
from ashare_lab.selection.order_gate import ExecutionRules
from ashare_lab.selection.dividend_registration import register_dividend


def normalize(value):
    def encode(item):
        if isinstance(item, (Decimal, date)): return str(item)
        if isinstance(item, frozenset): return sorted(item)
        raise TypeError(type(item).__name__)
    return json.loads(json.dumps(value, default=encode))


def replay(report):
    if report.get('schema_version') != 2 or report.get('evidence_class') != 'SYNTHETIC_INTEGRATION_ONLY':
        raise ValueError('仅支持完整输入的第二版合成演练')
    account = CashDividendAccount(ShareAccount(Decimal(report['initial_cash'])))
    for index, record in enumerate(report['records']):
        if record['type'] == 'ORDER_RESULT':
            data = dict(record['input'])
            data['day'] = date.fromisoformat(data['day'])
            data['sellable_on'] = date.fromisoformat(data['sellable_on']) if data['sellable_on'] else None
            for key in ('price', 'fees'): data[key] = Decimal(data[key])
            rules = dict(record['rules'])
            rules['day'] = date.fromisoformat(rules['day'])
            for key in ('lower_price', 'upper_price'): rules[key] = Decimal(rules[key])
            account, decision, sales = apply_checked_execution(account, Fill(**data),
                signal_day=date.fromisoformat(record['signal_day']), rules=ExecutionRules(**rules),
                tradable=record['tradable'], execution_evidence_id=record['decision']['execution_evidence_id'])
            if normalize(asdict(decision)) != record['decision'] or normalize(sales) != record['sales']:
                raise ValueError(f'第{index}步成交决策或卖出批次不一致')
        elif record['type'] == 'DIVIDEND':
            saved = record['registration']
            data = dict(saved['entitlement'])
            data.pop('shares')
            for key in ('register_date', 'ex_date', 'pay_date'): data[key] = date.fromisoformat(data[key])
            for key in ('gross_per_share', 'payment_per_share'): data[key] = Decimal(data[key])
            registered = register_dividend(**data, snapshot_date=data['register_date'],
                close_snapshot_id=saved['close_snapshot_id'], lots=account.shares.lots)
            if normalize(asdict(registered)) != saved:
                raise ValueError('从持仓重新生成的分红资格不一致')
            account, audit = apply_dividends(account, data['ex_date'], [registered.entitlement])
            if normalize(audit) != record['audit']:
                raise ValueError('分红审计记录不一致')
        else:
            raise ValueError('不支持的演练事件')
        if str(account.shares.cash) != record['cash_after']:
            raise ValueError(f'第{index}步现金不一致')
    if normalize(asdict(account)) != report['final_account']:
        raise ValueError('重放最终账户与保存结果不一致')
    return {'status': 'REPLAY_MATCH', 'steps': len(report['records']),
            'scope': 'SYNTHETIC_SAME_ENGINE_REPLAY', 'live_admission': 'BLOCKED'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.folder / 'manifest.json').read_text(encoding='utf-8'))
    entry, = manifest['entries']
    if entry['path'] != 'result.json': raise ValueError('非预期文件')
    raw = (args.folder / 'result.json').read_bytes()
    if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('保存文件完整性失败')
    print(json.dumps(replay(json.loads(raw)), ensure_ascii=False))
