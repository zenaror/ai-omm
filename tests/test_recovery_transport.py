"""Recovery must round-trip exact bytes and remain compatible with Git backup."""
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from omm import artifacts
from omm.backup_worker import _validate_sources, backup_once, BackupError
from omm.errors import OMMError
from omm.restore import restore_from_git
from omm.service import OMM
from omm.source_documents import read_source_batch, replace_source_markdown, read_source_chunks
from omm.usage import UsageRecorder


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "data"
        self.env = patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "false", "OMM_ARTIFACT_SCOPE_QUOTA_MIB": "512"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.omm = OMM(self.root)
        self.omm.init()

    def upload(self, raw=b"OpaquePayloadNeedle\0\xff", **changes):
        args = dict(scope="demo", content_base64=base64.b64encode(raw).decode(),
                    sha256=hashlib.sha256(raw).hexdigest(), title="RecoveryNeedle",
                    description="Synthetic recovery bytes, not a gameplay test.", origin="fixture")
        args.update(changes)
        return self.omm.import_artifact(**args)

    def test_artifact_pagination_dedup_and_search_exclusion(self):
        raw = b"OpaquePayloadNeedle" + bytes(range(256)) * 1100
        receipt = self.upload(raw)
        self.assertEqual(self.upload(raw)["status"], "already_present")
        other = self.upload(raw, title="Other provenance")
        self.assertNotEqual(other["path"], receipt["path"])
        self.assertEqual(len(list((self.root / "sources/demo/artifacts").glob("*.blob"))), 1)
        restored, offset = b"", 0
        while True:
            page = artifacts.read_artifact(self.root, receipt["path"], receipt["sha256"], offset)
            chunk = base64.b64decode(page["content_base64"], validate=True)
            self.assertEqual(hashlib.sha256(chunk).hexdigest(), page["chunk_sha256"])
            restored += chunk
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual(restored, raw)
        self.assertTrue(self.omm.search_sources("RecoveryNeedle", scopes=["demo"]))
        self.assertFalse(self.omm.search_sources("OpaquePayloadNeedle", scopes=["demo"]))
        self.assertNotIn("recorded_at", "\n".join(c.content for c in read_source_chunks(self.root)))
        _validate_sources(self.root)

    def test_limits_hash_and_metadata_validation(self):
        for changes in ({"sha256": "0" * 64}, {"content_base64": "!"}, {"scope": "../escape"},
                        {"title": "two\nlines"}, {"origin": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.upload(**changes)
        with self.assertRaises(ValueError):
            self.upload(b"x" * (artifacts.MAX_BYTES + 1))
        with patch.dict(os.environ, {"OMM_ARTIFACT_SCOPE_QUOTA_MIB": "1"}):
            with self.assertRaises(ValueError):
                self.upload(b"x" * (1024 * 1024 + 1))
        receipt = self.upload()
        with self.assertRaises(OMMError):
            artifacts.read_artifact(self.root, receipt["path"], "0" * 64)
        for offset, size in ((-1, 1), (True, 1), (0, 262145), (10000, 10)):
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                artifacts.read_artifact(self.root, receipt["path"], receipt["sha256"], offset, size)
        with self.assertRaises(ValueError):
            replace_source_markdown(self.root, receipt["path"], "replace", "0" * 64)

    def test_symlinks_corruption_and_orphans_block_backup(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "sources").mkdir()
        (self.root / "sources/demo").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.upload()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / "sources/demo").unlink()
        receipt = self.upload()
        blob = next((self.root / "sources/demo/artifacts").glob("*.blob"))
        original = blob.read_bytes()
        blob.unlink()
        external = outside / "payload"
        external.write_bytes(original)
        blob.symlink_to(external)
        with self.assertRaises(ValueError):
            artifacts.read_artifact(self.root, receipt["path"], receipt["sha256"])
        with self.assertRaises(BackupError):
            _validate_sources(self.root)
        blob.unlink()
        blob.write_bytes(original)
        blob.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            artifacts.read_artifact(self.root, receipt["path"], receipt["sha256"])
        with self.assertRaises(BackupError):
            _validate_sources(self.root)
        (self.root / receipt["path"]).unlink()
        with self.assertRaises(BackupError):
            _validate_sources(self.root)

    def test_git_backup_and_fresh_restore_preserve_payload(self):
        raw = bytes(range(256))
        receipt = self.upload(raw)
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        for args in (("init", "-b", "main"), ("remote", "add", "origin", str(remote))):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        backup_once(self.root, "origin", "main", True, "", "", "Fixture", "test@example.invalid")
        destination = self.base / "restored"
        restore_from_git(destination, str(remote), "main")
        result = artifacts.read_artifact(destination, receipt["path"], receipt["sha256"])
        self.assertEqual(base64.b64decode(result["content_base64"]), raw)
        restored = OMM(destination)
        plan = restored.merge_scope("demo", "renamed", True)
        self.assertEqual(plan["status"], "ready")
        restored.merge_scope("demo", "renamed", False, plan["plan_sha256"])
        moved = receipt["path"].replace("sources/demo/", "sources/renamed/legacy-demo/")
        self.assertEqual(base64.b64decode(artifacts.read_artifact(destination, moved, receipt["sha256"])["content_base64"]), raw)
        _validate_sources(destination)

    def test_batch_exact_unicode_crlf_empty_and_long_lines(self):
        raw = ("# 日本語\r\n" + "é" * 25000 + "\r\n").encode()
        receipt = self.omm.import_source("demo", "long.md", raw.decode())
        requests = [{"path": receipt["source"], "start_char": start, "max_chars": 14000,
                     "expected_sha256": receipt["sha256"]} for start in (0, 14000)]
        result = read_source_batch(self.root, requests)
        self.assertEqual("".join(p["content"] for p in result["items"]).encode(), raw)
        (self.root / "sources/demo/empty.md").write_bytes(b"")
        self.assertEqual(read_source_batch(self.root, [{"path": "sources/demo/empty.md"}])["items"][0]["content"], "")
        with self.assertRaises(OMMError):
            read_source_batch(self.root, [{"path": receipt["source"], "expected_sha256": "0" * 64}])
        for requests in ([], [{"path": "../outside.md"}], [{"path": receipt["source"], "max_chars": True}],
                         [{"path": receipt["source"], "max_chars": 32768}] * 3,
                         [{"path": receipt["source"]}] * 9):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                read_source_batch(self.root, requests)

    def test_failure_correlation_is_bounded_private_and_not_retrospective(self):
        usage = UsageRecorder(self.root)
        def read_source(path):
            raise FileNotFoundError("private exception text")
        measured = usage.wrap(read_source)
        for i in range(205):
            with self.assertRaises(FileNotFoundError):
                measured(f"sources/demo/private-file-{i}.md")
        with self.assertRaises(FileNotFoundError):
            measured("sources/demo/private-file-0.md")
        report = usage.report(scope="demo")
        self.assertEqual(len(report["source_error_rows"]), 20)
        self.assertEqual(report["source_errors_overflow"], 5)
        key = hashlib.sha256(b'["sources/demo/private-file-0.md"]').hexdigest()
        self.assertEqual(report["source_error_rows"][0]["path_key"], key)
        self.assertEqual(report["source_error_rows"][0]["failures"], 2)
        with sqlite3.connect(usage.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage_source_failures_daily").fetchone()[0], 201)
            self.assertTrue(db.execute("SELECT 1 FROM usage_source_failures_daily WHERE path_key=?", (key,)).fetchone())
            dump = "\n".join(db.iterdump())
            self.assertNotIn("private-file", dump)
            self.assertNotIn("private exception", dump)
            db.execute("UPDATE usage_source_failures_daily SET day='2000-01-01'")
        usage._record("fixture", "demo", 1, 1, 1, False)
        self.assertEqual(usage.report()["source_error_rows"], [])

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK required")
    def test_real_mcp_schema_transport_and_failure_diagnostic(self):
        from omm.mcp_server import build_server
        from mcp.server.mcpserver.exceptions import ToolError
        async def run():
            server, _ = build_server(self.root)
            schemas = {t.name: t.input_schema for t in await server.list_tools()}
            self.assertIn("expected_sha256", schemas["read_artifact"]["required"])
            async def call(name, args):
                result = await server.call_tool(name, args)
                return json.loads(result.content[0].text)
            receipt = await call("upload_artifact", dict(scope="demo", content_base64="YWJj", sha256=hashlib.sha256(b"abc").hexdigest(),
                                                       title="fixture", description="synthetic", origin="fixture"))
            page = await call("read_artifact", dict(path=receipt["path"], expected_sha256=receipt["sha256"]))
            self.assertEqual(base64.b64decode(page["content_base64"]), b"abc")
            source = self.omm.import_source("demo", "text.md", "# Texto\r\n日本語\r\n")
            batch = await call("read_source_batch", {"requests": [{"path": source["source"], "expected_sha256": source["sha256"]}]})
            self.assertEqual(batch["items"][0]["content"], "# Texto\r\n日本語\r\n")
            with self.assertRaises(ToolError):
                await server.call_tool("read_source_batch", {"requests": [{"path": "sources/demo/missing.md"}]})
            report = await call("usage_report", {"scope": "demo"})
            self.assertEqual(report["source_error_rows"][0]["code"], "NOT_FOUND")
        asyncio.run(run())
