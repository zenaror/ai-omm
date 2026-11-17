import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from omm.git_credentials import credential_entry
from omm import backup_worker, sync, restore


class GitCredentialTests(unittest.TestCase):
    username = 'fixture-user'
    token = 'fixture:@/% value'

    def lookup(self, url, path):
        parsed = urlsplit(url)
        host = parsed.hostname
        if ':' in host:
            host = '[' + host + ']'
        if parsed.port is not None:
            host += ':' + str(parsed.port)
        environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        result = subprocess.run(['git', '-c', 'credential.helper=', '-c',
                                 'credential.helper=store --file=' + str(path), 'credential', 'fill'],
                                input=f'protocol={parsed.scheme}\nhost={host}\n\n',
                                text=True, capture_output=True, env=environment)
        self.assertEqual(result.returncode, 0, 'Git did not find the credential for this protocol and port')
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        self.assertEqual(fields['username'], self.username)
        self.assertEqual(fields['password'], self.token)
        return fields

    def test_real_git_lookup_preserves_protocol_and_port(self):
        for url in ['http://fixture.invalid/repo', 'https://fixture.invalid/repo',
                    'http://fixture.invalid:3000/repo', 'https://fixture.invalid:8443/repo',
                    'http://fixture.invalid:80/repo', 'https://fixture.invalid:443/repo',
                    'http://[::1]:3000/repo', 'https://[::1]:8443/repo']:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'credentials'
                path.write_text(credential_entry(url, self.username, self.token))
                path.chmod(0o600)
                fields = self.lookup(url, path)
                self.assertEqual(fields['protocol'], urlsplit(url).scheme)
                if urlsplit(url).port:
                    self.assertTrue(fields['host'].endswith(':' + str(urlsplit(url).port)))

    def test_invalid_urls_do_not_echo_values(self):
        for url in ['file:///tmp/repo', 'ssh://fixture.invalid/repo', 'https:///missing',
                    'http://fixture.invalid:bad/repo']:
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                credential_entry(url, self.username, self.token)
            self.assertNotIn(self.token, str(error.exception))

    def check_prefix(self, args, url, seen):
        entry = next(a for a in args if a.startswith('credential.helper=store --file='))
        path = Path(entry.split('--file=', 1)[1])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(self.token, ' '.join(str(a) for a in args))
        self.lookup(url, path)
        seen.append(path)

    def test_manual_sync_and_restore_use_matching_credentials_and_clean_up(self):
        for url in ['http://fixture.invalid:3000/repo', 'https://fixture.invalid:8443/repo']:
            for operation in ['sync', 'restore-update', 'restore-clone']:
                with self.subTest(url=url, operation=operation), tempfile.TemporaryDirectory() as folder:
                    seen = []
                    def run(*args, **kwargs):
                        flat = args[0] if operation.startswith('restore') else args[1:]
                        self.check_prefix(flat, url, seen)
                        raise RuntimeError('fixture interruption')
                    with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                        if operation == 'sync':
                            with patch.object(sync, '_run', side_effect=run):
                                sync._credential_args(Path(folder), 'origin', url, self.username,
                                                      self.token, ['ls-remote', url])
                        elif operation == 'restore-update':
                            with patch.object(restore, '_run_git', side_effect=run):
                                restore._run_git_authenticated(['fetch', 'origin'], url,
                                                               self.username, self.token)
                        else:
                            with patch.object(restore, '_run_git', side_effect=run):
                                restore.restore_from_git(Path(folder)/'data', url,
                                                         username=self.username, token=self.token)
                    self.assertEqual(len(seen), 1)
                    self.assertFalse(seen[0].exists())

    def test_non_http_sync_does_not_attach_web_credentials(self):
        for url in ['ssh://git@fixture.invalid/repo', '/tmp/local-remote.git']:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                def run(root, *args, **kwargs):
                    self.assertFalse(any('credential.helper' in a for a in args))
                    return 'fixture result'
                with patch.object(sync, '_run', side_effect=run):
                    self.assertEqual(sync._credential_args(Path(folder), 'origin', url,
                                     self.username, self.token, ['ls-remote', url]), 'fixture result')

    def test_scheduled_delivery_matches_authentication_and_keeps_receipt_clean(self):
        for url in ['http://fixture.invalid:3000/repo', 'https://fixture.invalid:8443/repo']:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as folder:
                seen = []
                def run(root, *args, **kwargs):
                    self.check_prefix(args, url, seen)
                    return 'a'*40 + '\trefs/heads/main'
                with patch.object(backup_worker, '_git', side_effect=run):
                    backup_worker._send_backup(Path(folder), 'origin', 'main', url,
                                               self.username, self.token, 'a'*40)
                self.assertTrue(seen)
                self.assertTrue(all(not p.exists() for p in seen))
                self.assertNotIn(self.token, (Path(folder)/'.omm/omm-backup-status.json').read_text())
