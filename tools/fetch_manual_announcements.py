"""抓取一个沪市候选的官方公告列表及原文，供风险与公司行动审阅。"""
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'


def fetch(url, data=None):
    req = Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0', 'Referer':'https://www.cninfo.com.cn/'})
    with urlopen(req, timeout=30) as response:
        value = response.read()
    return gzip.decompress(value) if value.startswith(b'\x1f\x8b') else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--org-id', required=True)
    parser.add_argument('--since', required=True)
    parser.add_argument('--through', required=True)
    args = parser.parse_args()
    if len(args.code) != 6 or not args.code.isdigit() or date.fromisoformat(args.since) > date.fromisoformat(args.through):
        raise ValueError('无效查询范围')
    output = ROOT/'data/imports'/datetime.now(timezone.utc).strftime(f'manual_announcements_{args.code}_%Y%m%dT%H%M%SZ')
    output.mkdir(exist_ok=False)
    entries, announcements = [], []

    def save(name, payload, **metadata):
        with (output/name).open('xb') as handle:
            handle.write(payload)
        entries.append(dict(path=name, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload), **metadata))

    page, total = 1, None
    while total is None or len(announcements) < total:
        params = dict(pageNum=page, pageSize=30, column='sse', stock=args.code+','+args.org_id,
                      tabName='fulltext', seDate=args.since+'~'+args.through, isHLtitle='false')
        payload = fetch(API, urlencode(params).encode())
        result = json.loads(payload)
        count = result['totalAnnouncement']
        if type(count) is not int or count < 0 or (total is not None and count != total):
            raise ValueError('公告计数变化或无效')
        total = count
        rows = result.get('announcements') or []
        if not rows and len(announcements) < total:
            raise ValueError('分页缺失')
        if any(row['secCode'] != args.code for row in rows):
            raise ValueError('证券代码不匹配')
        save(f'page_{page}.json', payload, query=params, source=API)
        announcements.extend(rows)
        page += 1
    if len(announcements) != total or len({r['announcementId'] for r in announcements}) != total:
        raise ValueError('公告重复或数量不符')
    for row in announcements:
        suffix = row['adjunctUrl']
        if not suffix.startswith('finalpage/') or not suffix.lower().endswith('.pdf') or '..' in suffix:
            raise ValueError('非预期公告路径')
        url = 'https://static.cninfo.com.cn/'+suffix
        payload = fetch(url)
        if not payload.startswith(b'%PDF-'):
            raise ValueError('公告不是PDF')
        save(suffix.rsplit('/', 1)[-1], payload, source=url, code=args.code,
             title=row['announcementTitle'], announcement_id=row['announcementId'])
        print(row['announcementTitle'], flush=True)
    with (output/'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(dict(status='SEALED', code=args.code, since=args.since, through=args.through,
                       fetched_at=datetime.now(timezone.utc).isoformat(), announcement_count=total,
                       entries=entries, purpose='REQUIRES_CONTENT_REVIEW_NOT_RISK_CLEARANCE'),
                  handle, ensure_ascii=False, indent=2)
    print(output)


if __name__ == '__main__':
    main()
