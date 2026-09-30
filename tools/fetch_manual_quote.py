"""Seal a public quote response; this is not trading permission."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://push2.eastmoney.com/api/qt/stock/get?secid=1.600886&fields=f43,f44,f45,f46,f51,f52,f57,f58,f59,f60,f86,f292'


def main():
    with urlopen(Request(URL, headers={'User-Agent':'ashare-paper-research/1.0'}), timeout=20) as response:
        raw = response.read()
    payload = json.loads(raw)
    if payload.get('rc') != 0 or payload.get('data', {}).get('f57') != '600886':
        raise ValueError('Wrong security or query failed')
    now = datetime.now(timezone.utc)
    folder = ROOT/'data/imports'/now.strftime('manual_quote_%Y%m%dT%H%M%S%fZ')
    folder.mkdir(exist_ok=False)
    with (folder/'quote.json').open('xb') as handle:
        handle.write(raw)
    manifest = {'status':'SEALED', 'source':'Eastmoney public quote', 'url':URL,
                'created_at':now.isoformat(), 'admission':'NOT_EXECUTION_PERMISSION',
                'entries':[{'path':'quote.json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}
    with (folder/'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(folder)


if __name__ == '__main__':
    main()
