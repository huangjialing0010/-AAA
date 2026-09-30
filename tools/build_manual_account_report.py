"""Build immutable reports from the actual first-order journal."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.selection.manual_account_report import summarize, render
from ashare_lab.selection.manual_live import sha
from audit_manual_launch import audit


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    folder=ROOT/'reports/manual_forward_20260929T074758514815Z'
    audit(folder)
    run=read(folder/'run.json')
    run_hash=sha(folder/'run.json')
    sources={str((folder/'run.json').relative_to(ROOT)):run_hash}
    events=[]
    for manifest_path in sorted((ROOT/'reports').glob('manual_execution_*/manifest.json')):
        manifest=read(manifest_path)
        if manifest['order_id']!=run['orders'][0]['order_id']:
            continue
        path=manifest_path.parent/'event.json'
        if sha(path)!=manifest['event_sha256']:
            raise ValueError('Journal changed')
        event=read(path)
        matched=False
        for name,expected in event['sources'].items():
            source=(ROOT/name).resolve()
            if not source.is_relative_to(ROOT.resolve()) or sha(source)!=expected:
                raise ValueError('Journal source changed')
            if source==(folder/'run.json').resolve() and expected==run_hash:
                matched=True
        if not matched:
            raise ValueError('Journal not bound to this launch')
        events.append(event)
        sources[str(path.relative_to(ROOT))]=sha(path)
        sources[str(manifest_path.relative_to(ROOT))]=sha(manifest_path)
    now=datetime.now(timezone.utc)
    report=summarize(run,events,now=now)
    implementation={name:sha(ROOT/name) for name in ('tools/build_manual_account_report.py',
        'src/ashare_lab/selection/manual_account_report.py')}
    identity=hashlib.sha256(json.dumps({'sources':sources,'day':report['report_day'],
        'implementation':implementation},sort_keys=True).encode()).hexdigest()
    for previous in (ROOT/'reports').glob('manual_paper_*/manifest.json'):
        prior=read(previous)
        if prior.get('report_identity')==identity:
            for name,expected in prior['files'].items():
                if sha(previous.parent/name)!=expected:
                    raise ValueError('Existing report changed')
            print('ALREADY_REPORTED',previous.parent)
            return
    output=ROOT/'reports'/now.strftime('manual_paper_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    report['sources']=sources
    with (output/'report.json').open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,default=str)
    for period in ('daily','weekly','monthly'):
        with (output/(period+'.md')).open('x',encoding='utf-8') as handle:
            handle.write(render(report,period))
    manifest={'kind':'MANUAL_ACCOUNT_REPORT_V1','report_identity':identity,'implementation':implementation,
              'files':{name:sha(output/name) for name in ('report.json','daily.md','weekly.md','monthly.md')}}
    with (output/'manifest.json').open('x',encoding='utf-8') as handle:
        json.dump(manifest,handle,indent=2)
    print(output)
    print(json.dumps({'status':report['order_status'],'equity':report['equity'],
                      'pnl':report['net_pnl'],'counts':report['counts']},ensure_ascii=False))


if __name__=='__main__':main()
