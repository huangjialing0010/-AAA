"""用封存不复权行情重放四笔分红的会计流程；不代表策略信号或收益。"""
from __future__ import annotations
import csv, json, sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ashare_lab.selection.share_account import ShareAccount, Fill, book_fill
from ashare_lab.selection.cash_dividend_account import CashDividendAccount, apply_dividends
from ashare_lab.selection.dividend_registration import register_dividend
from ashare_lab.selection.dividend_ledger import DividendEntitlement
from ashare_lab.selection.dividend_tax import deferred_dividend_tax

ROOT = Path(__file__).resolve().parents[1]
PRICE_ROOT = ROOT / 'data/canonical/stock_selection_v1/prices'
EVENTS = ROOT / 'data/canonical/dividend_events_pilot/events.json'

def prices(code):
    with (PRICE_ROOT / f'{code}.csv').open(encoding='utf-8', newline='') as handle:
        return {row['trade_date']: row for row in csv.DictReader(handle)}

def prior_next(table, day):
    days = sorted(date.fromisoformat(d) for d in table)
    prior = max((d for d in days if d < day), default=None)
    following = min((d for d in days if d > day), default=None)
    return prior, following

def main():
    payload = json.loads(EVENTS.read_text(encoding='utf-8'))
    results = []
    for event in payload['events']:
        code = event['code']; table = prices(code)
        register = date.fromisoformat(event['register_date'])
        buy_day, _ = prior_next(table, register)
        pay_day = date.fromisoformat(event['pay_date'])
        _, sell_day = prior_next(table, pay_day)
        if buy_day is None or sell_day is None:
            raise ValueError(f'行情区间不足: {code} {event["event_id"]}')
        buy_row, sell_row = table[str(buy_day)], table[str(sell_day)]
        if buy_row['tradestatus'] != '1' or sell_row['tradestatus'] != '1':
            raise ValueError('试点买卖日不可交易')
        buy_price = Decimal(buy_row['open']); sell_price = Decimal(sell_row['open'])
        account = CashDividendAccount(ShareAccount(Decimal('10000')))
        buy = Fill('BUY-' + event['event_id'], code, 'BUY', buy_day, 0, 100, buy_price, Decimal('0'), register)
        shares, _ = book_fill(account.shares, buy)
        account = CashDividendAccount(shares, account.dividends, buy_day)
        registered = register_dividend(event_id=event['event_id'], code=code,
            register_date=register, ex_date=date.fromisoformat(event['ex_date']), pay_date=pay_day,
            gross_per_share=Decimal(event['gross_cash_per_share']), payment_per_share=Decimal(event['default_a_share_cash_credit_per_share']),
            evidence_id=event['source_notice']['sha256'], snapshot_date=register,
            close_snapshot_id=f'PRICE_CLOSE:{code}:{register}', lots=account.shares.lots)
        account, _ = apply_dividends(account, pay_day, [registered.entitlement])
        cash_after_dividend = account.shares.cash
        sell = Fill('SELL-' + event['event_id'], code, 'SELL', sell_day, 0, 100, sell_price, Decimal('0'))
        shares, sales = book_fill(account.shares, sell)
        account = CashDividendAccount(shares, account.dividends, sell_day)
        tax = deferred_dividend_tax(gross_cash=Decimal(event['gross_cash_per_share']), shares=100,
            acquired_on=buy_day, register_date=register, sale_day=sell_day)
        results.append({'event_id': event['event_id'], 'code': code, 'buy_day': str(buy_day),
                        'buy_open_unadjusted': str(buy_price), 'register_date': str(register),
                        'pay_date': str(pay_day), 'sell_day': str(sell_day), 'sell_open_unadjusted': str(sell_price),
                        'cash_after_dividend_before_sale': str(cash_after_dividend),
                        'sale_lots': sales, 'deferred_tax': tax, 'source_notice_sha256': event['source_notice']['sha256'],
                        'evidence_class': 'HISTORICAL_PRICE_ACCOUNTING_PILOT_NOT_STRATEGY'})
    output = {'status': 'PASS', 'event_count': len(results), 'results': results,
              'assumptions': ['买入登记日前最近交易日原始开盘价', '每笔100股', '零交易成本仅用于公司行动会计试点',
                              '默认自然人A股现金到账，税负卖出时计算'],
              'limitations': ['没有使用策略信号或历史组合持仓', '没有证明实际成交、盘口、滑点或完整事件覆盖',
                              '不构成收益率或实盘建议'], 'ledger_admission': 'BLOCKED'}
    rendered = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    stamp = date.today().strftime('%Y%m%d')
    destination = ROOT / 'reports' / f'DIVIDEND_HISTORICAL_PRICE_PILOT_{stamp}.json'
    destination.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    print(f'REPORT={destination}')

if __name__ == '__main__':
    main()
