"""只初始化研究账户；不生成信号、订单、成交或策略收益。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = 'manual_research_100k_v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for prior in (ROOT / 'reports').glob('manual_paper_*/opening.json'):
        if json.loads(prior.read_text(encoding='utf-8')).get('account_id') == ACCOUNT_ID:
            raise RuntimeError(f'账户已开户，不重复创建: {prior}')
    now = datetime.now(timezone.utc)
    documents = ['docs/MANUAL_ACCOUNT_OPENING_V1.md',
                 'docs/MANUAL_LOW_FREQUENCY_STRATEGY_V1.md',
                 'docs/MANUAL_PAPER_REPORTING_AND_REVIEW_V2.md']
    opening = {
        'account_id': ACCOUNT_ID, 'created_at': now.isoformat(),
        'status': 'OPEN_BUYING_BLOCKED', 'currency': 'CNY',
        'capital_purpose': 'RESEARCH_SCENARIO_NOT_USER_COMMITMENT',
        'initial_capital': '100000.00', 'cash': '100000.00',
        'stock_value': '0.00', 'equity': '100000.00',
        'positions': [], 'orders': [], 'trades': [],
        'strategy_return': None, 'strategy_version': None,
        'automation_enabled': False,
        'blockers': ['EXECUTABLE_STRATEGY_NOT_FROZEN',
                     'LATEST_DATA_AND_ELIGIBILITY_NOT_VERIFIED',
                     'CURRENT_STRATEGY_EXECUTION_PIPELINE_NOT_CONNECTED'],
        'documents': {name: digest(ROOT / name) for name in documents},
    }
    output = ROOT / 'reports' / now.strftime('manual_paper_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    with (output / 'opening.json').open('x', encoding='utf-8') as handle:
        json.dump(opening, handle, ensure_ascii=False, indent=2)
    with (output / 'README.md').open('x', encoding='utf-8') as handle:
        handle.write('# 模拟账户开户记录\n\n'
                     f'账户：{ACCOUNT_ID}；UTC开户时刻：{now.isoformat()}。\n\n'
                     '研究本金10万元；现金10万元；股票市值0元；净资产10万元。\n\n'
                     '状态：账户已建立，暂停买入。订单0、成交0、持仓0。策略收益不可计算。\n\n'
                     '尚缺可执行规则冻结、最新数据及资格核验、当前策略成交链接入。'
                     '没有启用自动调度，不是已运行的交易策略。\n\n'
                     '风险：全现金可能错过行情；不得以放松核验来制造成交。\n')
    manifest = {'account_id': ACCOUNT_ID, 'type': 'ACCOUNT_OPENING_ONLY',
                'entries': {name: digest(output / name) for name in ('opening.json', 'README.md')}}
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2)
    print(output)


if __name__ == '__main__':
    main()
