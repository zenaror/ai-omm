"""Durable restart, atomic publication and cross-process progress for embeddings."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from omm.cli import main
from omm.models import MemoryRecord
from omm.semantic import OllamaSemanticIndex, SemanticSearchError
from omm.service import OMM


class SemanticResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "true",
                                     "OMM_EMBEDDING_URL": "http://fixture.invalid/api/embed",
                                     "OMM_EMBEDDING_MODEL": "fixture-model"})
        env.start()
        self.addCleanup(env.stop)
        self.omm = OMM(self.root)
        self.omm.init()
        self.records = [MemoryRecord(kind="fact", title=f"Needle{i:03}", content=f"Evidence {i}",
                                     source="fixture", scope="demo") for i in range(70)]
        self.omm.store.append_many(self.records[:1])
        with patch("omm.semantic.urlopen", side_effect=self.embed):
            self.omm.rebuild_semantic()
        self.original_fingerprint = self.omm.semantic.indexed_fingerprint()
        self.omm.store.append_many(self.records[1:])

    @staticmethod
    def embed(request, timeout):
        data = json.loads(request.data)
        return io.BytesIO(json.dumps({"embeddings": [[1.0, 0.0] for _ in data["input"]]}).encode())

    def vector_ids(self):
        with sqlite3.connect(self.omm.semantic.path) as db:
            return {row[0] for row in db.execute("SELECT id FROM semantic_vectors")}

    def fail_after_batch(self):
        calls = []
        def failing(request, timeout):
            calls.append(json.loads(request.data)["input"])
            if len(calls) == 2:
                raise OSError("private endpoint detail must not enter progress")
            return self.embed(request, timeout)
        with patch("omm.semantic.urlopen", side_effect=failing):
            with self.assertRaises(SemanticSearchError):
                self.omm.rebuild_semantic(["demo"])
        self.assertEqual(self.vector_ids(), {self.records[0].id})
        self.assertEqual(self.omm.semantic.indexed_fingerprint(), self.original_fingerprint)
        self.assertEqual(self.omm.semantic.build_progress()["completed"], 32)
        self.assertNotIn("private endpoint", json.dumps(self.omm.semantic.build_progress()))

    def test_lexical_probe_under_exclusive_lock_preserves_batches_and_resume(self):
        self.fail_after_batch()
        index = self.omm.semantic.path
        inode = index.stat().st_ino
        holder = sqlite3.connect(index)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                self.omm._ensure_index_current(force=True)
            self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
            self.assertEqual(index.stat().st_ino, inode)
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(self.omm.semantic.build_progress()["completed"], 32)
        self.assertEqual(self.vector_ids(), {self.records[0].id})
        embedded = []
        def count(request, timeout):
            embedded.extend(json.loads(request.data)["input"])
            return self.embed(request, timeout)
        # A normal lexical query after the lock clears must still work.
        self.assertEqual(len(self.omm.search("Needle000")), 1)
        with patch("omm.semantic.urlopen", side_effect=count):
            self.assertEqual(self.omm.rebuild_semantic(), 70)
        self.assertEqual(len(embedded), 38)
        self.assertEqual(self.omm.semantic.build_progress()["reused"], 32)

    def test_non_corruption_errors_do_not_discard_index(self):
        inode = self.omm.semantic.path.stat().st_ino
        for method in ("has_expected_schema", "indexed_fingerprint"):
            for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR, None):
                with self.subTest(method=method, code=code):
                    error = sqlite3.OperationalError("fixture")
                    if code is not None:
                        error.sqlite_errorcode = code
                    with patch.object(self.omm.retriever, method, side_effect=error), \
                            patch.object(self.omm.retriever, "discard_index") as discard:
                        with self.assertRaises(sqlite3.OperationalError):
                            self.omm._ensure_index_current(force=True)
                        discard.assert_not_called()
                    self.assertEqual(self.omm.semantic.path.stat().st_ino, inode)

    def test_confirmed_corrupt_database_is_still_rebuilt_from_canonical_data(self):
        self.omm.semantic.path.write_bytes(b"not a SQLite database")
        self.omm._ensure_index_current(force=True)
        self.assertEqual(len(self.omm.search("Needle000")), 1)
        self.assertEqual(len(list(self.omm.store.records())), 70)

    def test_killed_cli_restarts_from_durable_batches_and_reports_to_other_processes(self):
        script = '''import io,json,sys,time
from pathlib import Path
from unittest.mock import patch
from omm.cli import main
root=Path(sys.argv[1]);calls=0
def embed(request,timeout):
 global calls
 calls+=1
 if calls==2:
  (root/'second-batch').touch()
  time.sleep(60)
 return io.BytesIO(json.dumps({'embeddings':[[1.0,0.0] for _ in json.loads(request.data)['input']]}).encode())
with patch('omm.semantic.urlopen',side_effect=embed):
 main(['--root',str(root),'semantic-rebuild'])
'''
        child = subprocess.Popen([sys.executable, "-c", script, str(self.root)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "second-batch").exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue((self.root / "second-batch").exists(), "CLI did not reach second batch")
            observer = OMM(self.root)
            status = observer.semantic_index_status()
            self.assertEqual(status["status"], "building")
            self.assertEqual((status["completed"], status["total"]), (32, 70))
            self.assertTrue(status["checkpointed"])
            self.assertEqual(self.vector_ids(), {self.records[0].id})
        finally:
            child.kill()
            stdout, stderr = child.communicate(timeout=3)
        self.assertIn("Conferindo 70 itens", stderr)
        self.assertIn("não apaga o índice anterior", stderr)
        self.assertNotIn("recriado:", stdout)
        self.assertEqual(OMM(self.root).semantic_index_status()["status"], "interrupted")
        result = subprocess.run([sys.executable, "-m", "omm", "--root", str(self.root),
                                 "semantic-status"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["last_build"]["completed"], 32)
        embedded = []
        def count(request, timeout):
            embedded.extend(json.loads(request.data)["input"])
            return self.embed(request, timeout)
        resumed = OMM(self.root)
        with patch("omm.semantic.urlopen", side_effect=count):
            self.assertEqual(resumed.rebuild_semantic(), 70)
        self.assertEqual(len(embedded), 38)
        self.assertEqual(self.vector_ids(), {r.id for r in self.records})
        self.assertEqual(resumed.semantic_index_status()["status"], "ready")
        self.assertEqual(resumed.semantic.build_progress()["reused"], 32)

    def test_restart_rechecks_changed_and_removed_inputs_and_preserves_other_scope(self):
        other = MemoryRecord(kind="fact", title="OtherScope", content="Retain this vector",
                             source="fixture", scope="other")
        self.omm.store.append(other)
        with patch("omm.semantic.urlopen", side_effect=self.embed):
            self.omm.rebuild_semantic(["other"])
        original_ids = self.vector_ids()
        calls = 0
        def failing(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("interrupted fixture")
            return self.embed(request, timeout)
        with patch("omm.semantic.urlopen", side_effect=failing), self.assertRaises(SemanticSearchError):
            self.omm.rebuild_semantic(["demo"])
        self.assertEqual(self.vector_ids(), original_ids)
        self.records[0].content = "Changed checkpoint input"
        self.records[1].status = "retracted"
        # Simulate a canonical edit between two independent runs.
        self.omm.store.records_path.write_text("".join(r.to_json() + "\n" for r in [*self.records, other]))
        embedded = []
        def count(request, timeout):
            embedded.extend(json.loads(request.data)["input"])
            return self.embed(request, timeout)
        resumed = OMM(self.root)
        with patch("omm.semantic.urlopen", side_effect=count):
            resumed.rebuild_semantic(["demo"])
        self.assertEqual(len(embedded), 39)
        self.assertTrue(any("Changed checkpoint input" in text for text in embedded))
        self.assertNotIn(self.records[1].id, self.vector_ids())
        self.assertIn(other.id, self.vector_ids())
        self.assertEqual(resumed.semantic.indexed_fingerprint("demo"), resumed._canonical_fingerprint(["demo"]))
        self.assertNotEqual(resumed.semantic.indexed_fingerprint(), resumed._canonical_fingerprint())
        with sqlite3.connect(resumed.semantic.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM semantic_checkpoints "
                                        "WHERE scope='demo'").fetchone()[0], 0)

    def test_completing_another_scope_preserves_unfinished_scope_checkpoints(self):
        self.fail_after_batch()
        other = MemoryRecord(kind="fact", title="OtherScope", content="Separate work",
                             source="fixture", scope="other")
        self.omm.store.append(other)
        with patch("omm.semantic.urlopen", side_effect=self.embed):
            self.omm.rebuild_semantic(["other"])
        with sqlite3.connect(self.omm.semantic.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM semantic_checkpoints "
                                        "WHERE scope='demo'").fetchone()[0], 32)
        embedded = []
        def count(request, timeout):
            embedded.extend(json.loads(request.data)["input"])
            return self.embed(request, timeout)
        with patch("omm.semantic.urlopen", side_effect=count):
            self.omm.rebuild_semantic(["demo"])
        self.assertEqual(len(embedded), 38)

    def test_failed_publication_rolls_back_and_can_retry_without_new_embeddings(self):
        with sqlite3.connect(self.omm.semantic.path) as db:
            db.execute("CREATE TRIGGER reject_publish BEFORE INSERT ON semantic_vectors "
                       "BEGIN SELECT RAISE(ABORT, 'fixture rejects publication'); END")
        with patch("omm.semantic.urlopen", side_effect=self.embed), self.assertRaises(sqlite3.Error):
            self.omm.rebuild_semantic()
        self.assertEqual(self.vector_ids(), {self.records[0].id})
        self.assertEqual(self.omm.semantic.indexed_fingerprint(), self.original_fingerprint)
        self.assertEqual(self.omm.semantic.build_progress()["completed"], 70)
        self.assertEqual(self.omm.semantic.build_progress()["status"], "failed")
        with sqlite3.connect(self.omm.semantic.path) as db:
            db.execute("DROP TRIGGER reject_publish")
        with patch("omm.semantic.urlopen", side_effect=AssertionError("must reuse saved batches")):
            OMM(self.root).rebuild_semantic()
        self.assertEqual(len(self.vector_ids()), 70)

    def test_new_model_does_not_reuse_checkpoint_vectors(self):
        self.fail_after_batch()
        self.omm.semantic = OllamaSemanticIndex(self.omm.semantic.path,
                                               "http://fixture.invalid/api/embed", "new-model")
        embedded = []
        def count(request, timeout):
            embedded.extend(json.loads(request.data)["input"])
            return self.embed(request, timeout)
        with patch("omm.semantic.urlopen", side_effect=count):
            self.omm.rebuild_semantic(["demo"])
        self.assertEqual(len(embedded), 70)
        self.assertIsNone(self.omm.semantic.indexed_fingerprint())

    def test_cli_failure_is_not_success_and_valid_checkpoints_survive(self):
        calls = 0
        def failing(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture")
            return self.embed(request, timeout)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("omm.semantic.urlopen", side_effect=failing), redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--root", str(self.root), "semantic-rebuild"])
        self.assertEqual(result, 1)
        self.assertNotIn("recriado:", stdout.getvalue())
        self.assertIn("retomada", stderr.getvalue())
        self.assertEqual(self.omm.semantic.build_progress()["completed"], 32)

    def test_changes_in_other_projects_do_not_invalidate_ready_scope(self):
        with patch("omm.semantic.urlopen", side_effect=self.embed):
            self.omm.rebuild_semantic(["demo"])
        self.omm.store.append(MemoryRecord(kind="fact", title="Unrelated", content="Other content", source="fixture", scope="other"))
        self.assertEqual(self.omm.semantic_index_status(["demo"])["status"], "ready")
        self.assertEqual(self.omm.semantic_index_status(["other"])["status"], "stale")

    def test_cli_reports_reused_and_new_items_separately_on_resume(self):
        self.fail_after_batch()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("omm.semantic.urlopen", side_effect=self.embed), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--root", str(self.root), "semantic-rebuild"])
        self.assertIn(result, (None, 0))
        self.assertIn("Conferindo 70 itens", stderr.getvalue())
        self.assertNotIn("(0.0%)", stderr.getvalue())
        self.assertIn("70/70 itens preparados (100.0%)", stderr.getvalue())
        for output in (stdout.getvalue(), stderr.getvalue()):
            self.assertIn("Reaproveitados: 32 | Novos/recalculados: 38", output)
