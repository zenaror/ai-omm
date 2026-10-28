import io
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from omm.cli import main, parser
from omm.errors import OMMError
from omm.models import MemoryRecord
from omm.service import OMM
from omm.topology import select_topology, TopologyError
from omm.usage import UsageRecorder


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false", "OMM_USAGE_ENABLED": "true"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.omm = OMM(self.root)
        self.omm.init()

    def test_topology_fallback_preserves_project_selection_and_rejects_corruption(self):
        role = self.root / "memory/roles/planner.md"
        role.write_text("Planner")
        config = {"schema_version": 1,
                  "project_session_model": "multiple_workflow_sessions_shared_omm",
                  "workflow_session_model": "single_parent_with_subagents",
                  "default_mode": "coordinator_alone", "coordinator_role": "coordinator",
                  "shared_memory": "omm_canonical", "subagents": [
                      {"name": "planner", "role_file": "memory/roles/planner.md", "reports_to": "coordinator"}],
                  "project_profiles": {"demo": {"agents": [
                      {"name": "planner", "role_file": "memory/roles/planner.md"}]}}}
        path = self.root / "memory/agent-topology.json"
        original = json.dumps(config)
        path.write_text(original)
        own = select_topology(self.root, "demo")
        fallback = select_topology(self.root, "another-project")
        global_rules = select_topology(self.root)
        self.assertEqual(own["profile"], config["project_profiles"]["demo"])
        self.assertTrue(own["profile_found"])
        self.assertTrue(fallback["fallback"])
        self.assertFalse(fallback["profile_found"])
        self.assertEqual(fallback["scope"], "another-project")
        self.assertNotIn("profile", fallback)
        for key, value in global_rules.items():
            self.assertEqual(fallback[key], value)
        self.assertEqual(path.read_text(), original)
        path.write_text("{")
        with self.assertRaises(TopologyError):
            select_topology(self.root, "another-project")

    def test_workstream_legacy_history_noop_pagination_and_handoff_independence(self):
        legacy = {"id": "demo-one", "title": "First", "status": "active", "created_by": "agent", "created_at": "2026-01-01"}
        self.omm.store.workstreams_path.write_text(json.dumps(legacy) + "\n")
        before = self.omm.get_workstream("demo-one")
        self.omm.handoff("partial", "Evidence retained", [], [], ["Continue"], "agent", workstream_id="demo-one", scope="demo")
        handoff = self.omm.get_handoff("demo", "demo-one")
        updated = self.omm.update_workstream("demo-one", before["sha256"], "agent", status="paused")
        self.assertNotEqual(before["sha256"], updated["sha256"])
        self.assertEqual(updated["created_at"], legacy["created_at"])
        history = self.omm.store.workstreams_path.read_bytes()
        self.assertEqual(len(history.splitlines()), 2)
        self.assertEqual(json.loads(history.splitlines()[0]), legacy)
        self.assertEqual(self.omm.get_handoff("demo", "demo-one"), handoff)
        self.assertEqual(self.omm.update_workstream("demo-one", updated["sha256"], status="paused"), updated)
        self.assertEqual(self.omm.store.workstreams_path.read_bytes(), history)
        self.omm.create_workstream("demo-two", "Second")
        self.assertEqual(len(self.omm.workstreams()), 2)
        page = self.omm.list_workstreams(limit=1)
        self.assertEqual(page["next_offset"], 1)
        self.assertEqual(self.omm.list_workstreams(offset=1)["items"][0]["id"], "demo-two")
        self.assertEqual(self.omm.list_workstreams(status="paused")["total"], 1)

    def test_workstream_concurrent_writers_have_one_winner(self):
        original = self.omm.create_workstream("demo-race", "Original")
        barrier = Barrier(2)

        def update(title):
            service = OMM(self.root)
            barrier.wait(timeout=5)
            try:
                return service.update_workstream("demo-race", original["sha256"], title=title)
            except OMMError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["First writer", "Second writer"]))
        self.assertEqual(results.count("VERSION_CONFLICT"), 1)
        winner = next(item for item in results if isinstance(item, dict))
        self.assertEqual(self.omm.get_workstream("demo-race"), winner)
        self.assertEqual(len(self.omm.store.workstreams_path.read_text().splitlines()), 2)

    def test_workstream_rejected_changes_do_not_write(self):
        item = self.omm.create_workstream("demo-safe", "Original")
        before = self.omm.store.workstreams_path.read_bytes()
        for kwargs in ({}, {"status": "invented"}, {"title": ""}, {"title": "x" * 301},
                       {"title": "line\nbreak"}, {"title": "ok", "actor": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.omm.update_workstream("demo-safe", item["sha256"], **kwargs)
        for digest in ("0" * 64, "short", None):
            with self.subTest(digest=digest), self.assertRaises(OMMError):
                self.omm.update_workstream("demo-safe", digest, status="completed")
        with self.assertRaises(OMMError) as duplicate:
            self.omm.create_workstream("demo-safe", "Duplicate")
        self.assertEqual(duplicate.exception.code, "ALREADY_EXISTS")
        self.assertEqual(before, self.omm.store.workstreams_path.read_bytes())

    def test_usage_migrates_old_counters_and_classifies_without_error_text(self):
        usage = UsageRecorder(self.root)
        usage._record("legacy", "demo", 10, 0, 2, True)
        # Simulate the exact previous schema, with no error-category table.
        with sqlite3.connect(usage.path) as db:
            db.execute("DROP TABLE usage_errors_daily")
        for code in ("VERSION_CONFLICT", "secret-user-value"):
            def failing(scope="demo"):
                raise OMMError(code, "private message", "private recovery")
            with self.assertRaises(OMMError):
                usage.wrap(failing)()
        report = usage.report(scope="demo")
        self.assertEqual(report["totals"]["failures"], 3)
        self.assertEqual(report["unclassified_failures"], 1)
        self.assertEqual(report["classified_failures"], 2)
        self.assertEqual({row["code"] for row in report["error_rows"]}, {"VERSION_CONFLICT", "INTERNAL_ERROR"})
        with sqlite3.connect(usage.path) as db:
            stored = "\n".join(db.iterdump())
        for private in ("secret-user-value", "private message", "private recovery"):
            self.assertNotIn(private, stored)
            self.assertNotIn(private, json.dumps(report))

    def test_usage_bounded_groups_retention_and_write_failure_isolation(self):
        usage = UsageRecorder(self.root)
        for index in range(130):
            usage._record("tool", f"project-{index}", 2, 0, 1, True, "INVALID_ARGUMENT")
        report = usage.report()
        self.assertEqual(report["classified_failures"], 130)
        self.assertTrue(report["errors_truncated"])
        self.assertEqual(len(report["error_rows"]), 50)
        self.assertEqual(usage.report(scope="project-0")["classified_failures"], 1)
        with sqlite3.connect(usage.path) as db:
            self.assertEqual(db.execute("SELECT SUM(failures) FROM usage_errors_daily WHERE scope='_other'").fetchone()[0], 2)
            db.execute("UPDATE usage_daily SET day='2000-01-01'")
            db.execute("UPDATE usage_errors_daily SET day='2000-01-01'")
        usage._record("tool", "demo", 1, 1, 1, False)
        self.assertEqual(usage.report()["classified_failures"], 0)
        def successful():
            return {"saved": True}
        with patch.object(usage, "_record", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(usage.wrap(successful)(), {"saved": True})
        self.assertEqual(usage.write_failures, 1)

    def test_scoped_semantic_rebuild_preserves_other_vectors_and_reuses_cache(self):
        for scope in ("demo", "other"):
            self.omm.remember(MemoryRecord(kind="fact", title=scope, content=f"Notes for {scope}", source="test", scope=scope))
        embedded = []
        def fake_embed(request, timeout):
            texts = json.loads(request.data)["input"]
            embedded.extend(texts)
            return io.BytesIO(json.dumps({"embeddings": [[1., 0.] for _ in texts]}).encode())
        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "true", "OMM_EMBEDDING_URL": "http://local.test/api/embed"}), patch("omm.semantic.urlopen", side_effect=fake_embed):
            service = OMM(self.root)
            service.rebuild_semantic(["other"])
            embedded.clear()
            service.rebuild_semantic(["demo"])
            self.assertTrue(embedded)
            self.assertTrue(all("other" not in text for text in embedded))
            self.assertEqual(service.semantic.chunk_count(), 2)
            self.assertIsNone(service.semantic.indexed_fingerprint())
            embedded.clear()
            service.rebuild_semantic(["demo"])
            self.assertEqual(embedded, [])
        args = parser().parse_args(["semantic-rebuild", "--scope", "demo", "--scope", "other"])
        self.assertEqual(args.scope, ["demo", "other"])

    def test_workstream_cli_conflict_is_structured_and_preserves_current_version(self):
        prefix = ["--root", str(self.root), "workstream"]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(prefix + ["create", "--id", "demo-cli", "--title", "Test"]), 0)
        item = json.loads(output.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(prefix + ["update", "--id", "demo-cli", "--expected-sha256", item["sha256"], "--status", "completed"]), 0)
        current = self.omm.get_workstream("demo-cli")
        with patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(main(prefix + ["update", "--id", "demo-cli", "--expected-sha256", item["sha256"], "--title", "Stale"]), 2)
        self.assertEqual(json.loads(error.getvalue())["error"]["code"], "VERSION_CONFLICT")
        self.assertEqual(self.omm.get_workstream("demo-cli"), current)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the optional MCP extra to test the real SDK")
    def test_mcp_schema_lifecycle_conflict_and_usage_with_real_sdk(self):
        from mcp.server.mcpserver.exceptions import ToolError
        from omm.mcp_server import build_server

        async def run():
            server, _ = build_server(self.root)
            schemas = {tool.name: tool.input_schema for tool in await server.list_tools()}
            for name in ("list_workstreams", "get_workstream", "create_workstream", "update_workstream"):
                self.assertIn(name, schemas)
            self.assertIn("expected_sha256", schemas["update_workstream"]["required"])

            async def call(name, arguments):
                result = await server.call_tool(name, arguments)
                self.assertFalse(result.is_error)
                return json.loads(result.content[0].text)

            created = await call("create_workstream", {"workstream_id": "demo-sdk", "title": "SDK test", "actor": "test"})
            self.assertEqual((await call("list_workstreams", {}))["items"][0], created)
            updated = await call("update_workstream", {"workstream_id": "demo-sdk", "expected_sha256": created["sha256"], "status": "paused", "actor": "test"})
            with self.assertRaises(ToolError) as failure:
                await server.call_tool("update_workstream", {"workstream_id": "demo-sdk", "expected_sha256": created["sha256"], "title": "stale", "actor": "test"})
            self.assertIn("VERSION_CONFLICT", str(failure.exception))
            self.assertEqual(await call("get_workstream", {"workstream_id": "demo-sdk"}), updated)
            report = await call("usage_report", {})
            self.assertEqual(report["classified_failures"], 1)
            self.assertEqual(report["error_rows"][0]["code"], "VERSION_CONFLICT")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
