"""Read-only account-cycle probe; never submits or books orders.

No ledger completeness or corporate-action approval is inferred from invocation.
Optional reviewed valuation inputs must bind the exact current journal.
"""
import json
import argparse
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from audit_manual_launch import audit
from ashare_lab.selection.manual_live import sha, sealed
from ashare_lab.selection.manual_quote import SHANGHAI
from ashare_lab.selection.manual_account_replay import replay_first_journal
from ashare_lab.selection.manual_daily_valuation import value_day


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_journal(root, folder):
    root, folder = root.resolve(), folder.resolve()
    if not folder.is_relative_to(root):
        raise ValueError('Journal outside workspace')
    run_path = folder/'run.json'
    run = read(run_path)
    order_id = run['orders'][0]['order_id']
    sources = {run_path.relative_to(root).as_posix(): sha(run_path)}
    events = []
    for directory in sorted((root/'reports').glob('manual_execution_*')):
        if not directory.is_dir():
            continue
        # A half-written directory must not silently disappear from the ledger.
        manifest_path, event_path = directory/'manifest.json', directory/'event.json'
        if not manifest_path.is_file() or not event_path.is_file():
            raise ValueError('Uncommitted execution directory: '+directory.name)
        manifest = read(manifest_path)
        if sha(event_path) != manifest['event_sha256']:
            raise ValueError('Execution event hash mismatch')
        event = read(event_path)
        if event['order_id'] != manifest['order_id']:
            raise ValueError('Manifest order mismatch')
        if event['order_id'] != order_id:
            continue
        matched = False
        for name, expected in event['sources'].items():
            source = (root/name).resolve()
            if not source.is_relative_to(root) or sha(source) != expected:
                raise ValueError('Journal source changed or escaped workspace')
            matched |= source == run_path and expected == sha(run_path)
            sources[source.relative_to(root).as_posix()] = expected
        if not matched:
            raise ValueError('Event not bound to launch')
        events.append(event)
        for path in (manifest_path, event_path):
            sources[path.relative_to(root).as_posix()] = sha(path)
    return run, events, sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--review')
    args = parser.parse_args()
    folder = ROOT/'reports/manual_forward_20260929T074758514815Z'
    audit(folder)
    run, events, sources = load_journal(ROOT, folder)
    now = datetime.now(SHANGHAI)
    account, proof = replay_first_journal(run, events, now=now)
    inputs = {'ledger_day': None, 'actions_verified': None, 'quotes': {}}
    if args.review:
        inputs, review_sources = load_review(ROOT, args.review, run['account_id'],
            {lot.code for lot in account.shares.lots}, sources, now)
        sources.update(review_sources)
    assessment = value_day(account, initial_cash=Decimal(run['cash']), now=now,
                           **inputs)
    print(json.dumps({'checked_at': now.isoformat(), 'account_id': run['account_id'],
                      'mode': 'READ_ONLY_PROBE_NO_ORDERS_NO_LEDGER_WRITES',
                      'replay': proof, 'valuation': assessment, 'sources': sources,
                      'next_action': 'BIND_CURRENT_LEDGER_ACTION_REVIEW_AND_SEALED_CLOSE_QUOTES'},
                     ensure_ascii=False, indent=2, default=str))


def load_review(root, review_path, account_id, codes, ledger_sources, now):
    root = root.resolve()
    def checked(name):
        path = (root/name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Review path outside workspace')
        return path
    path = checked(review_path)
    review = read(path)
    stamp = datetime.fromisoformat(review['reviewed_at'])
    day = now.astimezone(SHANGHAI).date()
    if (stamp.tzinfo is None or stamp > now or stamp.astimezone(SHANGHAI).date() != day
            or review['day'] != day.isoformat() or review['account_id'] != account_id
            or sorted(review['codes']) != sorted(codes)):
        raise ValueError('Review scope or time mismatch')
    if review['ledger_sources'] != ledger_sources:
        raise ValueError('Review does not bind complete current journal')
    sources = review['source_hashes']
    for name, expected in {**ledger_sources, **sources}.items():
        if sha(checked(name)) != expected:
            raise ValueError('Review source hash mismatch')
    for name, expected in ledger_sources.items():
        if sources.get(name) != expected:
            raise ValueError('Ledger absent from review sources')
    for key in ('ledger_complete', 'corporate_actions_verified'):
        if review.get(key) is not None and type(review[key]) is not bool:
            raise ValueError('Review flags must be boolean or null')
        if review.get(key) is True and not review.get('rationale', {}).get(key):
            raise ValueError('Missing review rationale')
    if review.get('corporate_actions_verified') is True:
        action_sources = review.get('action_sources', [])
        if not action_sources or any(name not in sources for name in action_sources):
            raise ValueError('Missing corporate-action evidence')
    quotes, bound = {}, dict(sources)
    if set(review.get('quote_paths', {})) - set(codes):
        raise ValueError('Quote outside held securities')
    for code, name in review.get('quote_paths', {}).items():
        quote_path = checked(name)
        manifest = sealed(quote_path.parent)
        if (not any(e['path'] == quote_path.name for e in manifest['entries'])
                or sources.get(quote_path.relative_to(root).as_posix()) != sha(quote_path)):
            raise ValueError('Quote not bound to sealed review')
        observed = datetime.fromisoformat(manifest['created_at'])
        if observed.tzinfo is None or observed > stamp:
            raise ValueError('Quote observed after review')
        if name in review.get('action_sources', []):
            raise ValueError('Quote is not corporate-action evidence')
        quotes[code] = {'payload': read(quote_path), 'observed_at': observed,
                        'evidence_id': sha(quote_path)}
        mp = quote_path.parent/'manifest.json'
        bound[mp.relative_to(root).as_posix()] = sha(mp)
    bound[path.relative_to(root).as_posix()] = sha(path)
    return {'ledger_day': day if review.get('ledger_complete') is True else None,
            'actions_verified': review.get('corporate_actions_verified'), 'quotes': quotes}, bound


if __name__ == '__main__':
    main()
