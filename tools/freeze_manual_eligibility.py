"""Freeze sourced eligibility before the open using actual wall clock, never backdate."""
import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.selection.manual_quote import SHANGHAI
from ashare_lab.selection.manual_live import sha


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--review',required=True,type=Path)
    args=parser.parse_args()
    path=args.review.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Review must be within workspace')
    review=json.loads(path.read_text(encoding='utf-8'))
    now=datetime.now(SHANGHAI)
    if now.date().isoformat()!=review['day'] or now.time()>time(9,25):
        raise ValueError('Can only freeze on execution day before 09:25; no backdating')
    if review.get('code')!='600886' or not review.get('source_hashes') or not review.get('rationale'):
        raise ValueError('Missing source-backed candidate review')
    for key in ('non_st','no_material_risk','corporate_actions_verified'):
        if review.get(key) is not None and type(review[key]) is not bool:
            raise ValueError('Use true/false/null, not text or numbers')
    for name,expected in review['source_hashes'].items():
        source=(ROOT/name).resolve()
        if not source.is_relative_to(ROOT.resolve()) or sha(source)!=expected:
            raise ValueError('Review source changed')
    review['review_source_sha256']=sha(path)
    review['frozen_at']=now.isoformat()
    output=ROOT/'data/imports'/now.astimezone(timezone.utc).strftime('manual_eligibility_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    frozen=output/'eligibility.json'
    with frozen.open('x',encoding='utf-8') as handle:
        json.dump(review,handle,ensure_ascii=False,indent=2)
    manifest=dict(status='SEALED',created_at=now.isoformat(),source='DERIVED_PREOPEN_REVIEW_NOT_EXTERNAL_DATA',
        entries=[dict(path=frozen.name,bytes=frozen.stat().st_size,sha256=sha(frozen))])
    with (output/'manifest.json').open('x',encoding='utf-8') as handle:
        json.dump(manifest,handle,indent=2)
    print(output)


if __name__=='__main__':main()
