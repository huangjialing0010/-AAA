import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_manual_account_cycle import load_journal, load_review


class CycleSourceTests(unittest.TestCase):
    def test_review_binding_unknowns_and_stale_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            evidence=root/'ledger.json'
            evidence.write_text('{}',encoding='utf-8')
            sources={'ledger.json':hashlib.sha256(evidence.read_bytes()).hexdigest()}
            now=datetime(2026,9,30,12,tzinfo=timezone.utc)
            review={'account_id':'TEST','day':'2026-09-30','reviewed_at':now.isoformat(),
                    'codes':[],'ledger_sources':sources,'source_hashes':sources,
                    'ledger_complete':None,'corporate_actions_verified':None}
            path=root/'review.json'
            def save():path.write_text(json.dumps(review),encoding='utf-8')
            save()
            values,_=load_review(root,'review.json','TEST',set(),sources,now)
            self.assertIsNone(values['ledger_day'])
            self.assertIsNone(values['actions_verified'])
            review['ledger_sources']={};save()
            with self.assertRaises(ValueError):load_review(root,'review.json','TEST',set(),sources,now)
            review['ledger_sources']=sources
            review['corporate_actions_verified']=True
            review['rationale']={'corporate_actions_verified':'checked'};save()
            with self.assertRaises(ValueError):load_review(root,'review.json','TEST',set(),sources,now)
            review['corporate_actions_verified']=None
            review['account_id']='OTHER';save()
            with self.assertRaises(ValueError):load_review(root,'review.json','TEST',set(),sources,now)

    def fixture(self, root):
        launch=root/'reports/manual_forward_test'
        launch.mkdir(parents=True)
        run=launch/'run.json'
        run.write_text(json.dumps({'orders':[{'order_id':'TEST'}]}),encoding='utf-8')
        event_dir=root/'reports/manual_execution_test'
        event_dir.mkdir()
        event={'order_id':'TEST','sources':{run.relative_to(root).as_posix():
                                          hashlib.sha256(run.read_bytes()).hexdigest()}}
        self.save_event(event_dir,event)
        return launch,event_dir,event

    def save_event(self,directory,event):
        path=directory/'event.json'
        path.write_text(json.dumps(event),encoding='utf-8')
        (directory/'manifest.json').write_text(json.dumps({'order_id':'TEST',
            'event_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}),encoding='utf-8')

    def test_valid_source_and_repeat_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);launch,_,_=self.fixture(root)
            first=load_journal(root,launch)
            self.assertEqual(first,load_journal(root,launch))
            self.assertEqual(len(first[1]),1)

    def test_source_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);launch,_,_=self.fixture(root)
            with (launch/'run.json').open('a',encoding='utf-8') as handle:handle.write(' ')
            with self.assertRaises(ValueError):load_journal(root,launch)

    def test_half_written_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);launch,_,_=self.fixture(root)
            (root/'reports/manual_execution_partial').mkdir()
            with self.assertRaises(ValueError):load_journal(root,launch)

    def test_manifest_order_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);launch,directory,event=self.fixture(root)
            event['order_id']='OTHER'
            self.save_event(directory,event)
            with self.assertRaises(ValueError):load_journal(root,launch)
