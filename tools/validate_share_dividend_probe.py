"""独立检查分红探测响应，不改原始快照，不授予正式账本准入。"""
import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


def numeric(value):
    try:
        number = Decimal(value)
        return number.is_finite() and number >= 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def check_event(row, code, year):
    errors, reviews = [], []
    expected = ('sh.' if code.startswith(('5', '6', '9')) else 'sz.') + code
    if row.get('code') != expected:
        errors.append('SECURITY_MISMATCH')
    try:
        register, ex, pay = [date.fromisoformat(row.get(key, '')) for key in
                             ('dividRegistDate', 'dividOperateDate', 'dividPayDate')]
        if not register < ex <= pay:
            errors.append('INVALID_DATE_ORDER')
        if ex.year != year:
            errors.append('QUERY_YEAR_MISMATCH')
    except (ValueError, TypeError):
        errors.append('MISSING_OR_INVALID_DATE')
    if not numeric(row.get('dividCashPsBeforeTax')):
        errors.append('INVALID_GROSS_CASH')
    if not numeric(row.get('dividCashPsAfterTax')):
        reviews.append('NET_CASH_NOT_SINGLE_NUMBER')
    for key in ('dividStocksPs', 'dividReserveToStockPs'):
        if not numeric(row.get(key)):
            reviews.append('UNRESOLVED_' + key)
    return errors, reviews


def validate(folder):
    folder = folder.resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'COLLECTED_PENDING_INDEPENDENT_VALIDATION':
        raise ValueError('仅校验完整采集的探测批次')
    codes, entries = manifest['codes'], manifest['entries']
    expected = {(code, kind) for code in codes for kind in ('dividend', 'adjust_factor')}
    actual = [(e['code'], e['category']) for e in entries]
    if (len(codes) != len(set(codes)) or len(actual) != len(expected)
            or set(actual) != expected or manifest['expected_file_count'] != len(expected)
            or manifest['year_type'] != 'operate'):
        raise ValueError('查询矩阵或计数不一致')
    payloads = {}
    for entry in entries:
        path = (folder / entry['path']).resolve()
        if path.parent != folder:
            raise ValueError('路径越界')
        raw = path.read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('文件完整性失败')
        payload = json.loads(raw)
        if len(payload['rows']) != entry['rows']:
            raise ValueError('响应行数不一致')
        if any(set(row) != set(payload['fields']) for row in payload['rows']):
            raise ValueError('响应字段不一致')
        payloads[entry['code'], entry['category']] = payload['rows']
    results = []
    for code in codes:
        dividends = payloads[code, 'dividend']
        factors = payloads[code, 'adjust_factor']
        errors, reviews = [], ['OFFICIAL_NOTICE_CROSSCHECK_REQUIRED', 'TAX_POLICY_REQUIRED']
        for row in dividends:
            bad, unknown = check_event(row, code, manifest['year'])
            errors.extend(bad)
            reviews.extend(unknown)
        dividend_dates = [r.get('dividOperateDate') for r in dividends]
        factor_dates = [r.get('dividOperateDate') for r in factors]
        if len(dividend_dates) != len(set(dividend_dates)):
            errors.append('DUPLICATE_DIVIDEND_DATE')
        if set(dividend_dates) != set(factor_dates):
            reviews.append('EVENT_FACTOR_DATE_MISMATCH')
        if not dividends:
            reviews.append('EMPTY_RESPONSE_COVERAGE_UNKNOWN')
        results.append({'code': code, 'event_count': len(dividends),
                        'errors': sorted(set(errors)), 'review_required': sorted(set(reviews))})
    return {'status': 'FAIL' if any(r['errors'] for r in results) else 'STRUCTURAL_CHECK_PASS_REVIEW_REQUIRED',
            'ledger_admission': 'BLOCKED', 'snapshot': str(folder), 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    report = validate(args.snapshot)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report['status'] == 'FAIL' else 0)
