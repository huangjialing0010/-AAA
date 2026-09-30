"""审计策略订单能否转换为整数股候选数量；不生成成交或改变账户。"""
from __future__ import annotations
import argparse, csv, json
from collections import Counter
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICE_ROOT = ROOT / 'data/canonical/stock_selection_v1/prices'

def money(value):
    return Decimal(value).quantize(Decimal('0.01'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orders', type=Path, required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    price_cache = {}
    records, reasons = [], Counter()
    with args.orders.open(encoding='utf-8', newline='') as handle:
        source = csv.DictReader(handle)
        for order in source:
            day = order['order_date']
            if not args.start <= day <= args.end:
                continue
            code = order['code']
            if code not in price_cache:
                path = PRICE_ROOT / f'{code}.csv'
                if not path.is_file():
                    price_cache[code] = {}
                else:
                    with path.open(encoding='utf-8', newline='') as price_file:
                        price_cache[code] = {row['trade_date']: row for row in csv.DictReader(price_file)}
            row = price_cache[code].get(day)
            requested = Decimal(order['requested_notional'])
            reason = 'CANDIDATE_NOT_FILL'; shares = None; raw_open = None
            if row is None:
                reason = 'MISSING_UNADJUSTED_PRICE'
            elif row.get('tradestatus') != '1' or Decimal(row.get('volume') or '0') <= 0:
                reason = 'NOT_CONFIRMED_TRADABLE'
            else:
                raw_open = Decimal(row['open'])
                increment = 200 if code.startswith('688') else 100
                shares = int((requested / raw_open / increment).to_integral_value(rounding=ROUND_DOWN)) * increment
                if shares < increment:
                    reason = 'CANDIDATE_BELOW_MINIMUM_SHARES'
            reasons[reason] += 1
            records.append({'order_id': order['order_id'], 'signal_date': order['signal_date'],
                'order_date': day, 'code': code, 'side': order['side'],
                'requested_notional': order['requested_notional'],
                'raw_open_unadjusted': str(raw_open) if raw_open is not None else None,
                'candidate_shares': shares, 'source_order_status': order['status'], 'reason': reason})
    output = {'status': 'AUDIT_ONLY_NOT_FILL', 'start': args.start, 'end': args.end,
              'order_count': len(records), 'reason_counts': dict(reasons), 'records': records,
              'limitations': ['候选数量不等于成交数量', '未验证价格上下限、盘口、费用、T+1和卖出批次',
                              '研究订单不是实际历史交易记录', '无复权转换，仍需公司行动和账户规则校验']}
    stamp = args.start.replace('-', '') + '_' + args.end.replace('-', '')
    destination = ROOT / 'reports' / f'STRATEGY_ORDER_SHARE_TRANSLATION_{stamp}.json'
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': output['status'], 'order_count': len(records), 'reason_counts': dict(reasons)}, ensure_ascii=False))
    print(destination)

if __name__ == '__main__':
    main()
