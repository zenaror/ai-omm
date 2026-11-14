import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omm import backup_worker as worker
from omm.backup_state import read_backup_state


class BackupDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        self.remote = Path(self.tmp.name) / 'remote.git'
        self.git('init', '--bare', str(self.remote), cwd=Path(self.tmp.name))
        self.git('init', '-b', 'main', str(self.root), cwd=Path(self.tmp.name))
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', str(self.remote))
        (self.root / 'memory').mkdir()
        (self.root / 'memory/records.jsonl').write_text('{}\n')

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', '-C', str(cwd or self.root), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def backup(self):
        return worker.backup_once(self.root, 'origin', 'main', True, '', '', 'Fixture', 'fixture@example.invalid')

    def test_http_504_after_success_is_confirmed_without_repeated_push(self):
        real = worker._git
        pushes = []
        def uncertain(root, *args, **kwargs):
            value = real(root, *args, **kwargs)
            if 'push' in args:
                pushes.append(args)
                raise worker.BackupError('HTTP 504 remote hung up')
            return value
        with patch.object(worker, '_git', side_effect=uncertain), patch.object(worker, '_retry_sleep') as sleep:
            self.assertIn('enviado ao remoto', self.backup())
        self.assertEqual(len(pushes), 1)
        sleep.assert_not_called()
        state = read_backup_state(self.root)
        self.assertEqual(state['state'], 'synced')
        self.assertEqual(state['remote_head'], self.git('rev-parse', 'HEAD'))

    def test_transient_failure_retries_and_does_not_hold_data_lock(self):
        real = worker._git
        count = 0
        def flaky(root, *args, **kwargs):
            nonlocal count
            if 'push' in args:
                count += 1
                if count == 1:
                    # A separate process must still be able to write canonical memory.
                    script = "from pathlib import Path;from omm.locking import data_lock;import sys\nr=Path(sys.argv[1])\nwith data_lock(r): (r/'writer-ran').touch()"
                    subprocess.run([sys.executable, '-c', script, str(root)], check=True, timeout=3)
                    raise worker.BackupError('HTTP 504')
            return real(root, *args, **kwargs)
        with patch.object(worker, '_git', side_effect=flaky), patch.object(worker, '_retry_sleep') as sleep:
            self.backup()
        self.assertTrue((self.root / 'writer-ran').exists())
        self.assertEqual(count, 2)
        sleep.assert_called_once_with(5)
        self.assertEqual(read_backup_state(self.root)['state'], 'synced')

    def test_permanent_failure_keeps_commit_and_sanitized_receipt(self):
        real = worker._git
        def rejected(root, *args, **kwargs):
            if 'push' in args:
                raise worker.BackupError('authentication failed token=private-value')
            return real(root, *args, **kwargs)
        with patch.object(worker, '_git', side_effect=rejected), patch.object(worker, '_retry_sleep') as sleep:
            with self.assertRaises(worker.BackupError):
                self.backup()
        sleep.assert_not_called()
        state = read_backup_state(self.root)
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['error_category'], 'authentication')
        self.assertEqual(state['local_head'], self.git('rev-parse', 'HEAD'))
        self.assertNotIn('private-value', json.dumps(state))
