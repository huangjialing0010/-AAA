"""Persist first-order checks; execution requires a separately sourced review."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.selection.manual_first_execution import assess_first_order
from ashare_lab.selection.manual_live import sealed, sha
from audit_manual_launch import audit


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def checked_path(value):
    path=(ROOT/value).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Evidence must be within workspace')
    return path


def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',default='reports/manual_forward_20260929T074758514815Z')
    parser.add_argument('--quote', help='Sealed raw quote directory')
    parser.add_argument('--review', help='Current execution review JSON with source hashes')
    args=parser.parse_args()
    folder=checked_path(args.run)
    audit(folder)
    run=read(folder/'run.json')
    order_id=run['orders'][0]['order_id']
    for prior in sorted((ROOT/'reports').glob('manual_execution_*/manifest.json')):
        manifest=read(prior)
        event_path=prior.parent/'event.json'
        if sha(event_path)!=manifest['event_sha256']:
            raise ValueError('Execution journal changed')
        event=read(event_path)
        if event['order_id']==order_id and event['status'] in ('SIMULATED_BOOKED','REJECTED','EXPIRED'):
            print(json.dumps({'status':'ALREADY_TERMINAL','event':str(event_path)},ensure_ascii=False))
            return
    sources={str((folder/'run.json').relative_to(ROOT)):sha(folder/'run.json')}
    quote=review=None
    if args.quote:
        quote_dir=checked_path(args.quote)
        sealed(quote_dir)
        quote=read(quote_dir/'quote.json')
        sources[str((quote_dir/'manifest.json').relative_to(ROOT))]=sha(quote_dir/'manifest.json')
    if args.review:
        review_path=checked_path(args.review)
        review=read(review_path)
        if not review.get('source_hashes'):
            raise ValueError('Review has no auditable sources')
        for name,expected in review['source_hashes'].items():
            if sha(checked_path(name))!=expected:
                raise ValueError('Review source changed')
        review['evidence_id']=sha(review_path)
        sources[str(review_path.relative_to(ROOT))]=sha(review_path)
        eligibility_path=checked_path(review['eligibility_path'])
        eligibility_manifest=sealed(eligibility_path.parent)
        if (not any(entry['path']==eligibility_path.name for entry in eligibility_manifest['entries'])
                or review['source_hashes'].get(str(eligibility_path.relative_to(ROOT)).replace('\\','/')) != sha(eligibility_path)):
            raise ValueError('Review does not bind sealed pre-open eligibility')
        eligibility=read(eligibility_path)
        if eligibility_manifest['created_at']!=eligibility['frozen_at']:
            raise ValueError('Eligibility seal time mismatch')
        for name,expected in eligibility['source_hashes'].items():
            if sha(checked_path(name))!=expected:
                raise ValueError('Pre-open eligibility source changed')
        review['eligibility']=eligibility
        sources[str((eligibility_path.parent/'manifest.json').relative_to(ROOT))]=sha(eligibility_path.parent/'manifest.json')
        if not args.quote or review['source_hashes'].get(str((quote_dir/'quote.json').relative_to(ROOT)).replace('\\','/')) != sha(quote_dir/'quote.json'):
            raise ValueError('Review must bind this exact quote.json using a slash-separated relative path')
    reference_dir=ROOT/'data/imports/manual_reference_20260929T062017Z'
    sealed(reference_dir)
    reference=read(reference_dir/'reference.json')
    days=[r['calendar_date'] for r in reference['calendar'] if r['is_trading_day']=='1']
    now=datetime.now(timezone.utc)
    event=assess_first_order(run,now=now,quote=quote,review=review,calendar=days)
    event['sources']=sources
    event['automation_status']='SEE_APP_SCHEDULER'
    output=ROOT/'reports'/now.strftime('manual_execution_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    with (output/'event.json').open('x',encoding='utf-8') as handle:
        json.dump(event,handle,ensure_ascii=False,indent=2,default=str)
    manifest={'event_sha256':sha(output/'event.json'),'order_id':order_id,
              'implementation':{name:sha(ROOT/name) for name in (
                  'tools/check_manual_execution.py','src/ashare_lab/selection/manual_first_execution.py',
                  'src/ashare_lab/selection/manual_execution.py','src/ashare_lab/selection/manual_quote.py')}}
    with (output/'manifest.json').open('x',encoding='utf-8') as handle:
        json.dump(manifest,handle,indent=2)
    print(json.dumps({'event':str(output/'event.json'),'status':event['status'],
                      'reason':event['reason'],'cash':event['cash'],'fills':len(event['fills'])},ensure_ascii=False))


def main():
    # OS lock releases even if this process crashes; the marker is never deleted.
    import msvcrt
    with (ROOT/'reports/manual_execution.lock').open('a+b') as lock:
        if lock.seek(0,2)==0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:
            run()
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__': main()
