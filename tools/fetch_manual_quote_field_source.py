"""Archive supplier field definitions for audit only; never execute vendor code."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
ROOT=Path(__file__).resolve().parents[1]


def main():
    url='https://quote.eastmoney.com/newstatic/build/vendor.js'
    with urlopen(Request(url,headers={'User-Agent':'ashare-field-audit/1.0'}),timeout=20) as response:
        raw=response.read()
    text=raw.decode('utf-8')
    expected=['r[r["今开"]=14]', 'r[r["涨停价"]=176]', 'r[r["跌停价"]=177]',
              '(o,14,{fid:["f46","f59","f60"]', '(o,176,{fid:["f51","f59","f60"]',
              '(o,177,{fid:["f52","f59","f60"]']
    if not all(fragment in text for fragment in expected):
        raise ValueError('Field definitions changed; manual semantic review required')
    now=datetime.now(timezone.utc)
    output=ROOT/'data/imports'/now.strftime('manual_quote_fields_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    with (output/'vendor.js').open('xb') as handle:handle.write(raw)
    manifest=dict(status='SEALED',source=url,created_at=now.isoformat(),
        purpose='FIELD_DEFINITION_EVIDENCE_ONLY_NOT_RUNTIME_OR_REUSED_IMPLEMENTATION',
        entries=[dict(path='vendor.js',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())],
        checks={'open':'enum14=f46','upper_limit':'enum176=f51','lower_limit':'enum177=f52'},
        limitations=['Price-scale helper and full field set require review',
                     'Not proof of execution-day tradability or risk clearance'])
    with (output/'manifest.json').open('x',encoding='utf-8') as handle:
        json.dump(manifest,handle,ensure_ascii=False,indent=2)
    print(output)


if __name__=='__main__':main()
