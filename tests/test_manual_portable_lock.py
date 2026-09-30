import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from manual_process_lock import exclusive_lock


class PortableLockTests(unittest.TestCase):
    def test_excludes_second_holder_and_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'account.lock'
            with self.assertRaisesRegex(RuntimeError, 'intentional'):
                with exclusive_lock(path):
                    with self.assertRaises(OSError):
                        with exclusive_lock(path):
                            self.fail('second writer acquired lock')
                    raise RuntimeError('intentional')
            with exclusive_lock(path):
                self.assertTrue(path.exists())
