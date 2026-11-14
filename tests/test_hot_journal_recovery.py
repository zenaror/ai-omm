"""Interrupted SQLite transactions are recoverable, not reasons to discard vectors."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from omm.service import OMM
from omm.models import MemoryRecord

class HotJournalRecoveryTests(unittest.TestCase):
    def test_recovers_actual_crash_journal_and_preserves_committed_batches(self):
        with tempfile.TemporaryDirectory() as root:
            o = OMM(Path(root)); o.init()
            o.remember(MemoryRecord(kind='fact',title='RecoveryNeedle',content='Committed fact',source='fixture'))
            path = Path(root)/'.omm/index.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE semantic_checkpoint_fixture(id INTEGER PRIMARY KEY, evidence TEXT)')
                db.executemany('INSERT INTO semantic_checkpoint_fixture VALUES(?,?)',[(i,'committed-'+'x'*4000) for i in range(500)])
            script = '''import sqlite3,sys,os
c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA cache_size=10');c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE semantic_checkpoint_fixture SET evidence='interrupted-' || evidence")
os._exit(0)
'''
            subprocess.run([sys.executable,'-c',script,str(path)],check=True,timeout=5)
            self.assertTrue(Path(str(path)+'-journal').exists())
            with self.assertRaises(sqlite3.OperationalError) as raised:
                with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                    db.execute('SELECT name FROM sqlite_master').fetchall()
            self.assertEqual(raised.exception.sqlite_errorcode,sqlite3.SQLITE_READONLY_ROLLBACK)
            self.assertTrue(o.retriever.has_expected_schema())
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_checkpoint_fixture WHERE evidence LIKE 'committed-%'").fetchone()[0],500)
            self.assertEqual(len(o.search('RecoveryNeedle')),1)

    def test_readonly_permissions_error_is_not_treated_as_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            o=OMM(Path(root));o.init()
            error=sqlite3.OperationalError('fixture readonly permissions')
            error.sqlite_errorcode=sqlite3.SQLITE_READONLY
            with patch('omm.retrieval.sqlite3.connect',side_effect=error) as connect:
                with self.assertRaises(sqlite3.OperationalError):
                    o.retriever.has_expected_schema()
            self.assertEqual(connect.call_count,1)
            self.assertTrue(o.retriever.path.exists())
