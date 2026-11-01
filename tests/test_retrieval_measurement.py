import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.project_retrieval_eval import evaluate, validate_cases
from omm.models import MemoryRecord
from omm.service import OMM
from omm.timing import PhaseTimings


class MeasurementTests(unittest.TestCase):
    def test_project_recovery_scope_superseded_negative_and_missing(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false"}):
            omm = OMM(Path(temporary))
            omm.init()
            for identity, scope, status in [("current", "demo", "active"), ("old", "demo", "superseded"), ("other", "other", "active")]:
                omm.remember(MemoryRecord(id=identity, kind="fact", scope=scope, status=status,
                                         title="transporte serial", content="buffer serial", source="fixture"))
            omm.import_source("demo", "sample.md", "# Serial\nBuffer serial persistente\n")
            cases = [{"id": "active", "scope": "demo", "kind": "memory", "query": "serial", "expected_ids": ["current"], "forbidden_ids": ["old", "other"]},
                     {"id": "negative", "scope": "demo", "kind": "memory", "query": "inexistente", "expected_empty": True},
                     {"id": "document", "scope": "demo", "kind": "source", "query": "serial", "expected_ids": ["sources/demo/sample.md"]}]
            result = evaluate(omm, cases)
            self.assertTrue(result["passed"])
            self.assertEqual(result["mean_recall_at_k"], 1)
            self.assertNotIn("query", result["results"][0])
            self.assertNotIn("current", json.dumps(result))
            cases[0]["expected_ids"] = ["missing"]
            self.assertFalse(evaluate(omm, cases)["passed"])
            phases = {r["phase"]: r for r in omm.phase_report()["rows"]}
            self.assertIn("lock_wait", phases)
            self.assertIn("index_rebuild", phases)
            self.assertIn("lexical_memory_query", phases)
            self.assertIn("lexical_source_query", phases)

    def test_invalid_cases_fail_before_retrieval(self):
        for cases in ([], [{}], [{"id": "x", "kind": "memory", "query": "x", "scope": "demo", "expected_empty": True, "expected_ids": ["a"]}]):
            with self.assertRaises(ValueError):
                validate_cases(cases)

    def test_phase_accounting_includes_failures_and_nested_lock_is_not_double_counted(self):
        timings = PhaseTimings()
        with patch("omm.timing.perf_counter", side_effect=[1, 1.5]):
            with self.assertRaises(ValueError), timings.measure("test"):
                raise ValueError("private-text")
        row = timings.report()["rows"][0]
        self.assertEqual((row["calls"], row["failures"], row["total_ms"]), (1, 1, 500))
        self.assertNotIn("private-text", json.dumps(timings.report()))
        with tempfile.TemporaryDirectory() as temporary:
            omm = OMM(Path(temporary))
            with omm.operation_lock():
                with omm.operation_lock():
                    pass
            self.assertEqual({r["phase"]: r["calls"] for r in omm.phase_report()["rows"]}, {"lock_wait": 1, "operation": 1})

    def test_usage_disable_also_disables_phase_counters(self):
        with patch.dict(os.environ, {"OMM_USAGE_ENABLED": "false"}):
            timings = PhaseTimings()
        with timings.measure("test"):
            pass
        self.assertEqual(timings.report()["rows"], [])
        self.assertFalse(timings.report()["enabled"])

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Requires optional MCP SDK")
    def test_compact_instructions_preserve_guide_and_tool_validation(self):
        from omm.mcp_server import build_server, FULL_USAGE_GUIDE
        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                server, _ = build_server(Path(temporary))
                self.assertLess(len(server.instructions), len(FULL_USAGE_GUIDE) / 3)
                tools = {t.name: t for t in await server.list_tools()}
                self.assertIn("expected_sha256", tools["update_workstream"].input_schema["required"])
                guide = await server.call_tool("usage_guide", {})
                self.assertEqual(guide.content[0].text, FULL_USAGE_GUIDE)
                report = await server.call_tool("usage_report", {})
                self.assertIn("runtime_phases", json.loads(report.content[0].text))
        asyncio.run(run())
