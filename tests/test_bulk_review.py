"""Approval must preserve canonical decisions without rebuilding unrelated sources."""
import http.client
import json
import os
from pathlib import Path
import tempfile
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch
import uuid

from omm.models import MemoryRecord
from omm.service import OMM
from omm.web_server import dashboard_data, make_handler


class BulkReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false", "OMM_WEB_PASSWORD": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.omm = OMM(Path(self.temp.name))
        self.omm.init()
        self.source = self.omm.root / "sources/demo/evidence.md"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# Evidence\n\nOldSourceNeedle.\n")
        self.omm.rebuild()

    def proposal(self, **changes):
        record = MemoryRecord(kind="fact", scope="demo", title="ApprovedNeedle",
                              content="Synthetic durable decision.", source="fixture", **changes)
        proposal = {"id": str(uuid.uuid4()), "status": "pending",
                    "record": json.loads(record.to_json()), "possible_matches": []}
        self.omm.store.append_proposal(proposal)
        return proposal

    def test_stale_sources_do_not_block_approval_or_dashboard_and_are_not_marked_current(self):
        proposals = [self.proposal() for _ in range(25)]
        self.source.write_text("# Evidence\n\nNewSourceNeedle added after indexing.\n")
        with patch.object(self.omm, "_rebuild_index", side_effect=AssertionError("must not rebuild sources")):
            result = self.omm.review_proposals([p["id"] for p in proposals], True)
            self.assertEqual(result["reviewed"], 25)
            self.assertTrue(result["index_pending"])
            data = dashboard_data(self.omm)
            self.assertEqual(data["proposal_count"], 0)
            self.assertEqual(data["summary"]["active_count"], 25)
            self.assertFalse(data["summary"]["index_current"])
            self.assertEqual(len(self.omm.retriever.search("ApprovedNeedle", 100)), 25)
        self.assertEqual(self.omm.search_sources("NewSourceNeedle")[0].path, "sources/demo/evidence.md")
        self.assertTrue(dashboard_data(self.omm)["summary"]["index_current"])

    def test_current_index_updates_in_one_batch_and_retries_do_not_duplicate(self):
        proposals = [self.proposal() for _ in range(25)]
        ids = [p["id"] for p in proposals]
        with patch.object(self.omm.retriever, "add_many", wraps=self.omm.retriever.add_many) as add:
            with patch.object(self.omm, "_rebuild_index", side_effect=AssertionError("unexpected rebuild")):
                result = self.omm.review_proposals(ids, True)
                self.assertFalse(result["index_pending"])
                self.assertTrue(dashboard_data(self.omm)["summary"]["index_current"])
                retry = self.omm.review_proposals(ids, True)
                self.assertEqual(retry["reviewed"], 0)
                self.assertEqual(retry["skipped"], 25)
            self.assertEqual(add.call_count, 1)
        self.assertEqual(len(self.omm.records()), 25)

    def test_index_failure_keeps_accepted_records_and_can_recover(self):
        proposal = self.proposal()
        with patch.object(self.omm.retriever, "add_many", side_effect=OSError("synthetic disk error")):
            result = self.omm.review_proposals([proposal["id"]], True)
        self.assertTrue(result["index_pending"])
        self.assertEqual(self.omm.store.proposals()[0]["status"], "accepted")
        self.assertEqual(len(self.omm.records()), 1)
        self.assertFalse(dashboard_data(self.omm)["summary"]["index_current"])
        self.assertEqual(self.omm.search("ApprovedNeedle")[0].id, proposal["record"]["id"])
        self.assertTrue(dashboard_data(self.omm)["summary"]["index_current"])

    def test_missing_index_does_not_prevent_canonical_approval_or_display(self):
        proposal = self.proposal()
        self.omm.retriever.discard_index()
        with patch.object(self.omm, "_rebuild_index", side_effect=AssertionError("unexpected rebuild")):
            result = self.omm.review_proposals([proposal["id"]], True)
            self.assertTrue(result["index_pending"])
            data = dashboard_data(self.omm)
            self.assertEqual(data["summary"]["active_count"], 1)
            self.assertFalse(data["summary"]["index_current"])
        self.assertEqual(len(self.omm.search("ApprovedNeedle")), 1)

    def test_validation_precedes_all_canonical_writes(self):
        valid, invalid = self.proposal(), self.proposal(workstream_id="missing")
        before = self.omm.store.records_path.read_bytes()
        with self.assertRaises(ValueError):
            self.omm.review_proposals([valid["id"], invalid["id"]], True)
        self.assertEqual(self.omm.store.records_path.read_bytes(), before)
        self.assertEqual(len(self.omm.proposals()), 2)

    def test_retry_after_proposal_status_write_failure_preserves_unique_record(self):
        proposal = self.proposal()
        with patch.object(self.omm.store, "update_proposals", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.omm.review_proposals([proposal["id"]], True)
        self.assertEqual(len(self.omm.records()), 1)
        self.assertEqual(len(self.omm.proposals()), 1)
        self.omm.review_proposals([proposal["id"]], True)
        self.assertEqual(len(self.omm.records()), 1)
        self.assertEqual(self.omm.proposals(), [])
        self.assertEqual(len(self.omm.search("ApprovedNeedle")), 1)

    def test_rejection_never_needs_the_index_and_single_approval_uses_fast_path(self):
        reject, approve = self.proposal(), self.proposal()
        with patch.object(self.omm.retriever, "has_expected_schema", side_effect=AssertionError("index read on rejection")):
            result = self.omm.review_proposals([reject["id"]], False)
            self.assertFalse(result["index_pending"])
        self.source.write_text("# Evidence\n\nNewSourceNeedle.\n")
        with patch.object(self.omm, "_rebuild_index", side_effect=AssertionError("single approval rebuilt sources")):
            result = self.omm.review_proposal(approve["id"], True)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["index_pending"])

    def test_batch_index_transaction_rolls_back_on_invalid_input(self):
        record = MemoryRecord(kind="fact", title="Original", content="Original content.", source="fixture")
        self.omm.retriever.add(record)
        bad = MemoryRecord(kind="fact", title="Bad", content="Invalid tags.", source="fixture", tags=[object()])
        with self.assertRaises(TypeError):
            self.omm.retriever.add_many([record, bad], "must-not-be-committed")
        self.assertEqual(self.omm.retriever.search("Original")[0].id, record.id)
        self.assertNotEqual(self.omm.retriever.indexed_fingerprint(), "must-not-be-committed")

    def test_http_reports_saved_decisions_when_index_update_fails(self):
        proposal = self.proposal()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.omm))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(self.omm.retriever, "add_many", side_effect=OSError("synthetic failure")):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                conn.request("POST", "/api/proposals/review-batch",
                             json.dumps({"ids": [proposal["id"]], "action": "approve"}),
                             {"Content-Type": "application/json"})
                response = conn.getresponse()
                data = json.loads(response.read())
                conn.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(data["index_pending"])
            self.assertIn("decisões estão salvas", data["message"])
            self.assertEqual(self.omm.proposals(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
