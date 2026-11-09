"""Canonical note writes must not depend on rebuilding unrelated sources."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from omm.models import MemoryRecord
from omm.service import OMM


class NoteWriteTests(unittest.TestCase):
    def fixture(self, root):
        omm = OMM(Path(root))
        omm.init()
        record = MemoryRecord(kind="fact", title="NoteNeedle", content="Durable fact.",
                              source="fixture", scope="demo")
        omm.remember(record)
        source = omm.root / "sources/demo/evidence.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Evidence\nOldSourceNeedle\n")
        omm.rebuild()
        return omm, record, source

    def write(self, omm, record, action):
        if action == "status":
            return omm.set_record_status(record.id, "superseded")
        record = MemoryRecord(kind="fact", title="AddedNeedle", content="Another durable fact.",
                              source="fixture", scope="demo")
        omm.remember(record)
        return record

    def test_writes_preserve_staleness_and_repair_on_search(self):
        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false"}):
            for action in ("remember", "status"):
                for state in ("current", "stale", "missing"):
                    with self.subTest(action=action, state=state), tempfile.TemporaryDirectory() as root:
                        omm, record, source = self.fixture(root)
                        if state == "stale":
                            source.write_text("# Evidence\nNewSourceNeedle\n")
                        elif state == "missing":
                            omm.retriever.discard_index()
                        with patch.object(omm, "_rebuild_index", side_effect=AssertionError("write rebuilt sources")):
                            written = self.write(omm, record, action)
                            self.assertEqual(omm.get_record(written.id).status, written.status)
                            if state != "missing":
                                self.assertEqual(omm.retriever.indexed_fingerprint() == omm._canonical_fingerprint(),
                                                 state == "current")
                        hits = omm.search("NoteNeedle" if action == "status" else "AddedNeedle", scopes=["demo"])
                        self.assertEqual([r.id for r in hits], [] if action == "status" else [written.id])
                        needle = "NewSourceNeedle" if state == "stale" else "OldSourceNeedle"
                        self.assertTrue(omm.search_sources(needle))
                        self.assertEqual(omm.retriever.indexed_fingerprint(), omm._canonical_fingerprint())

    def test_index_failure_does_not_lose_canonical_write(self):
        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false"}):
            for action in ("remember", "status"):
                with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                    omm, record, _ = self.fixture(root)
                    with patch.object(omm.retriever, "add_many", side_effect=sqlite3.OperationalError("fixture")):
                        written = self.write(omm, record, action)
                    self.assertEqual(omm.get_record(written.id).to_json(), written.to_json())
                    hits = omm.search("NoteNeedle" if action == "status" else "AddedNeedle", scopes=["demo"])
                    self.assertEqual([r.id for r in hits], [] if action == "status" else [written.id])

    def test_invalid_writes_do_not_touch_canonical_data(self):
        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false"}), tempfile.TemporaryDirectory() as root:
            omm, record, _ = self.fixture(root)
            original = omm.store.records_path.read_bytes()
            record.workstream_id = "missing"
            with self.assertRaises(ValueError):
                omm.remember(record)
            with self.assertRaises(ValueError):
                omm.set_record_status(record.id, "invalid")
            self.assertEqual(omm.store.records_path.read_bytes(), original)
