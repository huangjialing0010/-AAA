"""Archive the official current SSE rule page without changing trading assumptions."""
import hashlib
import json
import io
import zipfile
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]
URL='https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml'


def main():
    with urlopen(Request(URL,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as response:
        raw=response.read()
    text=raw.decode('utf-8')
    if '2026年7月6日' not in text or '959da0158c65434daa8a43a6e32be7ba.docx' not in text:
        raise ValueError('Official rule page requires manual review')
    payloads=[(URL,raw)]
    base='https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/10816482/files/'
    for name in ('959da0158c65434daa8a43a6e32be7ba.docx','f6bd756cdd8248f49b7795f62e73237f.docx'):
        with urlopen(Request(base+name,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as response:
            document=response.read()
        with zipfile.ZipFile(io.BytesIO(document)) as archive:
            xml=ET.fromstring(archive.read('word/document.xml'))
            paragraphs=[''.join(p.itertext()) for p in xml.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p')]
        if name.startswith('959') and not any('3.3.8' in p and '100股' in p for p in paragraphs):
            raise ValueError('Main rule attachment does not contain expected lot rule')
        payloads.append((base+name,document))
    now=datetime.now(timezone.utc)
    folder=ROOT/'data/imports'/now.strftime('manual_exchange_rules_%Y%m%dT%H%M%S%fZ')
    folder.mkdir(exist_ok=False)
    entries=[]
    for url,payload in payloads:
        name=url.rsplit('/',1)[-1]
        with (folder/name).open('xb') as handle:handle.write(payload)
        entries.append(dict(path=name,source=url,bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()))
    manifest=dict(status='SEALED',source=URL,created_at=now.isoformat(),
        purpose='OFFICIAL_RULE_TEXT_NOT_DAILY_TRADING_PERMISSION',
        entries=entries)
    with (folder/'manifest.json').open('x',encoding='utf-8') as handle:
        json.dump(manifest,handle,indent=2)
    print(folder)


if __name__=='__main__':main()
