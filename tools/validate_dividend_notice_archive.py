"""从本地公告 PDF 独立提取关键字段并与封存清单/先前证据比对。"""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path
from pypdf import PdfReader

EXPECTED = {
    '600036': {'file': '2025070301746_c.pdf', 'register': '2025/7/10', 'ex': '2025/7/11', 'pay': '2025/7/11', 'gross': '2.000'},
    '601857_annual': {'file': '2025061600751_c.pdf', 'register': '2025/6/24', 'ex': '2025/6/25', 'pay': '2025/6/25', 'gross': '0.25'},
    '601857_interim': {'file': '2025090801098_c.pdf', 'register': '2025/9/16', 'ex': '2025/9/17', 'pay': '2025/9/17', 'gross': '0.22'},
    '688009': {'file': '2025071700958_c.pdf', 'register': '2025/7/24', 'ex': '2025/7/25', 'pay': '2025/7/25', 'gross': '0.17'},
}

def compact(text: str) -> str:
    return re.sub(r'\s+', '', text).replace('Ａ', 'A').replace('（', '(').replace('）', ')')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'COLLECTED_PENDING_INDEPENDENT_VALIDATION':
        raise ValueError('公告清单状态不允许验收')
    by_file = {entry['path']: entry for entry in manifest['entries']}
    results = []
    for identity, expected in EXPECTED.items():
        path = folder / expected['file']
        entry = by_file.get(path.name)
        errors, text = [], ''
        if entry is None or not path.is_file():
            errors.append('MISSING_MANIFEST_ENTRY')
        else:
            raw = path.read_bytes()
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                errors.append('HASH_OR_SIZE_MISMATCH')
            reader = PdfReader(str(path))
            text = compact(''.join(page.extract_text() or '' for page in reader.pages))
            if not reader.pages:
                errors.append('NO_PAGES')
            for key in ('register', 'ex', 'pay', 'gross'):
                if expected[key].replace('/', '') not in text.replace('/', ''):
                    errors.append('MISSING_' + key.upper())
            # 仅在已核对的中国通号公告中要求文本证据，不对其他公司臆测送转结论。
            if identity == '688009' and ('不送红股' not in text or '不进行资本公积转增股本' not in text):
                errors.append('MISSING_NO_BONUS_EVIDENCE')
        results.append({'identity': identity, 'file': expected['file'], 'status': 'PASS' if not errors else 'FAIL', 'errors': errors, 'extracted_chars': len(text)})
    output = {'status': 'PASS' if all(row['status'] == 'PASS' for row in results) else 'FAIL',
              'evidence_class': 'LOCAL_PRIMARY_NOTICE_FIELD_CHECK', 'results': results,
              'ledger_admission': 'BLOCKED',
              'limitations': ['PDF文本提取不是视觉审阅，个别字体字形可能丢失', '仅验证四笔样本，不证明全历史完整', '税务资格仍需按账户身份和买入批次处理']}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if output['status'] == 'PASS' else 1)

if __name__ == '__main__':
    main()
