"""获取真实候选当前证券资料、行业和交易日历，原样保存JSON。"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.python-packages'))
sys.path.insert(0, str(ROOT/'src'))
from ashare_lab.sources.baostock_source import BaoStockSource


def consume(result):
    if str(result.error_code) != '0':
        raise RuntimeError(result.error_msg)
    rows = []
    while result.next():
        values = result.get_row_data()
        if len(values) != len(result.fields):
            raise ValueError('字段数量错误')
        rows.append(dict(zip(result.fields, values)))
    if str(result.error_code) != '0' or not rows:
        raise ValueError('查询失败或为空')
    return rows


def main():
    output = ROOT/'data/imports'/datetime.now(timezone.utc).strftime('manual_reference_%Y%m%dT%H%M%SZ')
    output.mkdir(exist_ok=False)
    data = {'basic': {}, 'industry': {}}
    with BaoStockSource(socket_timeout_seconds=20) as source:
        for code in ('600886', '600674', '605499'):
            data['basic'][code] = consume(source.client.query_stock_basic(code='sh.'+code))
            data['industry'][code] = consume(source.client.query_stock_industry(code='sh.'+code))
        data['calendar'] = consume(source.client.query_trade_dates(start_date='2026-09-01', end_date='2026-10-31'))
    path = output/'reference.json'
    with path.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    manifest = dict(status='SEALED', source='BaoStock', created_at=datetime.now(timezone.utc).isoformat(),
                    purpose='CURRENT_REFERENCE_NOT_HISTORICAL_INDUSTRY',
                    entries=[dict(path=path.name, bytes=path.stat().st_size,
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
    with (output/'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(output)
    print(json.dumps({k:v for k,v in data.items() if k!='calendar'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
