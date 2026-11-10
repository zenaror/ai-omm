"""Slow GPU work must serialize with builders, not canonical memory writers."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from omm.models import MemoryRecord
from omm.service import OMM


class SemanticConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "true",
                                     "OMM_EMBEDDING_URL": "http://fixture.invalid/api/embed"})
        env.start()
        self.addCleanup(env.stop)
        self.omm = OMM(self.root)
        self.omm.init()
        self.omm.remember(MemoryRecord(kind="fact", title="BeforeNeedle", content="Original evidence.",
                                       source="fixture", scope="demo"))

    def writer(self):
        script = '''from pathlib import Path
import sys
from omm.service import OMM
from omm.models import MemoryRecord
o=OMM(Path(sys.argv[1]))
h=o.get_handoff('demo')
o.handoff('active','Concurrent checkpoint',[],[],[],'fixture',scope='demo',expected_sha256=h['sha256'])
o.remember(MemoryRecord(kind='fact', title='AfterNeedle',content='Concurrent evidence.',source='fixture',scope='demo'))
print('written')
'''
        return subprocess.run([sys.executable, "-c", script, str(self.root)],
                              capture_output=True, text=True, timeout=3)

    @staticmethod
    def response(request):
        payload = json.loads(request.data)
        return io.BytesIO(json.dumps({"embeddings": [[1.0, 0.0] for _ in payload["input"]]}).encode())

    def test_cli_and_sync_search_allow_cross_process_writes_and_leave_snapshot_stale(self):
        for method in ("rebuild_semantic", "semantic_search"):
            with self.subTest(method=method):
                # Start each case with a genuinely cold vector index.
                with self.omm.semantic._connect() as db:
                    db.execute("DELETE FROM semantic_vectors")
                    db.execute("DELETE FROM semantic_metadata")
                started, finish, errors = Event(), Event(), []
                def slow(request, timeout):
                    started.set()
                    if not finish.wait(6):
                        raise TimeoutError("fixture embedding timeout")
                    return self.response(request)
                def build():
                    try:
                        if method == "rebuild_semantic":
                            self.omm.rebuild_semantic()
                        else:
                            self.omm.semantic_search("BeforeNeedle", limit=0)
                    except Exception as exc:
                        errors.append(exc)
                with patch("omm.semantic.urlopen", side_effect=slow):
                    worker = Thread(target=build)
                    worker.start()
                    try:
                        self.assertTrue(started.wait(2))
                        result = self.writer()
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("written", result.stdout)
                    finally:
                        finish.set()
                        worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])
                self.assertEqual(self.omm.get_handoff("demo")["state"]["summary"], "Concurrent checkpoint")
                self.assertTrue(self.omm.search("AfterNeedle", scopes=["demo"]))
                self.assertNotEqual(self.omm.semantic.indexed_fingerprint(), self.omm._canonical_fingerprint())

    def test_waiting_builder_does_not_take_data_lock_and_catches_new_sources(self):
        started, finish, errors = Event(), Event(), []
        def slow(request, timeout):
            started.set()
            if not finish.wait(7):
                raise TimeoutError("fixture embedding timeout")
            return self.response(request)
        def build():
            try:
                self.omm.rebuild_semantic()
            except Exception as exc:
                errors.append(exc)
        script = '''from pathlib import Path
import io,json,sys
from unittest.mock import patch
from omm.service import OMM
root=Path(sys.argv[1]);o=OMM(root)
o.import_source('demo','new.md','# New source\\nNewSourceNeedle')
def embed(request,timeout):
 (root/'second-builder-entered').write_text('embedded')
 return io.BytesIO(json.dumps({'embeddings':[[1.0,0.0] for _ in json.loads(request.data)['input']]}).encode())
print('attempting',flush=True)
with patch('omm.semantic.urlopen',side_effect=embed):o.rebuild_semantic()
'''
        with patch("omm.semantic.urlopen", side_effect=slow):
            first = Thread(target=build)
            first.start()
            child = None
            try:
                self.assertTrue(started.wait(2))
                child = subprocess.Popen([sys.executable, "-c", script, str(self.root)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                ready, lines = Event(), []
                def read_ready():
                    lines.append(child.stdout.readline().strip())
                    ready.set()
                reader = Thread(target=read_ready, daemon=True)
                reader.start()
                self.assertTrue(ready.wait(3), "second process could not start its rebuild")
                reader.join(1)
                self.assertEqual(lines, ["attempting"])
                result = self.writer()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.root / "second-builder-entered").exists(),
                                 "second builder must wait before GPU work")
            finally:
                finish.set()
                first.join(3)
                if child is not None:
                    try:
                        stdout, stderr = child.communicate(timeout=4)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        stdout, stderr = child.communicate()
            self.assertFalse(first.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(child.returncode, 0, stderr)
        self.assertTrue((self.root / "second-builder-entered").exists())
        self.assertTrue(self.omm.search_sources("NewSourceNeedle", scopes=["demo"]))
