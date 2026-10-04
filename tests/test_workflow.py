import json
import io
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from threading import Event
from time import monotonic
import unittest
from unittest.mock import patch

from omm.models import MemoryRecord
from omm.service import OMM
from omm.semantic import SemanticSearchError
from omm.topology import add_historical_source, load_topology
from omm.source_documents import read_source_markdown
from omm.history import HistoryImportError, accept_history, show_history_import, stage_history
from omm.copilot_archive import import_copilot_chat
from omm.claude_archive import import_claude_session
from omm.backup_worker import BackupError, backup_once
from omm.restore import RestoreError, resolve_restore_source, restore_from_git, restore_on_start
from omm.web_server import dashboard_data
from omm.performance import measure_performance
from omm.sync import sync_with_backup


class OMMWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.omm = OMM(self.root)
        self.omm.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_source_replace_and_delete_require_current_sha_and_rebuild_search(self):
        source = self.root / "sources" / "demo" / "notes.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Old note\n\nOldNeedle private details.\n", encoding="utf-8")
        self.omm.rebuild()
        path = "sources/demo/notes.md"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "mudou"):
            self.omm.replace_source(path, "# Safe note\n\nNewNeedle sanitized.\n", "0" * 64)
        with self.assertRaisesRegex(ValueError, "parece conter senha"):
            self.omm.replace_source(path, "# Safe note\n\npassword=synthetic-secret-value\n",
                                   hashlib.sha256(source.read_bytes()).hexdigest())
        updated = self.omm.replace_source(path, "# Safe note\n\nNewNeedle sanitized.\n", digest)
        self.assertEqual(updated["status"], "updated")
        self.omm.rebuild()
        self.assertEqual(self.omm.search_sources("OldNeedle", scopes=["demo"]), [])
        self.assertEqual(self.omm.search_sources("NewNeedle", scopes=["demo"])[0].path, path)
        new_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "mudou"):
            self.omm.delete_source(path, digest)
        deleted = self.omm.delete_source(path, new_digest)
        self.assertEqual(deleted["status"], "deleted")
        self.omm.rebuild()
        self.assertEqual(self.omm.search_sources("NewNeedle", scopes=["demo"]), [])
        self.assertFalse(source.exists())

    def test_read_source_returns_full_file_sha_for_guarded_edits(self):
        source = self.root / "sources" / "demo" / "notes.md"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"# Safe note\n\nA short line.\n")

        result = read_source_markdown(self.root, "sources/demo/notes.md", 1, 2)

        self.assertEqual(result["content"], "# Safe note\n")
        self.assertEqual(result["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(result["end_line"], 2)

    def test_topology_can_add_existing_historical_source_with_sha_guard(self):
        root = self.root / "topology-write"
        (root / "memory" / "roles").mkdir(parents=True)
        (root / "memory" / "roles" / "planner.md").write_text("role\n", encoding="utf-8")
        (root / "sources" / "demo" / "historical-chats").mkdir(parents=True)
        historical = root / "sources" / "demo" / "historical-chats" / "session.md"
        historical.write_text("# Historical chat\n", encoding="utf-8")
        config = {
            "schema_version": 1,
            "project_session_model": "multiple_workflow_sessions_shared_omm",
            "workflow_session_model": "single_parent_with_subagents",
            "coordinator_role": "coordinator", "shared_memory": "omm_canonical",
            "subagents": [{"name": "planner", "role_file": "memory/roles/planner.md",
                           "reports_to": "coordinator"}],
            "project_profiles": {"demo": {"shared_scope": "demo", "agents": [
                {"name": "demo-agent", "role_file": "memory/roles/planner.md"}]}}
        }
        topology_file = root / "memory" / "agent-topology.json"
        topology_file.write_text(json.dumps(config), encoding="utf-8")
        digest = hashlib.sha256(topology_file.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "mudou"):
            add_historical_source(root, "demo", "Session", "Claude Code", "session-1",
                                  "sources/demo/historical-chats/session.md", "Historical reference", "0" * 64)
        result = add_historical_source(root, "demo", "Session", "Claude Code", "session-1",
                                      "sources/demo/historical-chats/session.md", "Historical reference", digest)
        self.assertEqual(result["status"], "added")
        profile = load_topology(root)["project_profiles"]["demo"]
        self.assertEqual(profile["historical_sources"][0]["session_id"], "session-1")
        with self.assertRaisesRegex(ValueError, "registrada na topologia"):
            add_historical_source(root, "demo", "Session", "Claude Code", "session-1",
                                  "sources/demo/historical-chats/session.md", "Historical reference",
                                  result["sha256"])

    def test_remember_search_context_handoff_and_rebuild(self):
        record = MemoryRecord(kind="observation", title="RAG migration",
                              content="Keep retrieval replaceable and preserve evidence provenance.",
                              source="docs/research.md", evidence=["EV-12"], tags=["retrieval"])
        self.omm.remember(record)
        result = self.omm.search("evidence provenance")[0]
        self.assertEqual(result.id, record.id)
        self.assertEqual(result.evidence, ["EV-12"])
        context = self.omm.context("RAG migration")
        self.assertIn("Origem: docs/research.md", context)
        self.omm.create_workstream("retrieval", "Retrieval implementation")
        self.omm.handoff("in_progress", "Migrating retrieval", ["semantic adapter"],
                         ["Which embedding backend?"], ["Define adapter API"], "codex",
                         session_id="chat-12", workstream_id="retrieval")
        state = self.omm.store.read_state("retrieval")
        self.assertEqual(state["next_actions"], ["Define adapter API"])
        handoff = json.loads((self.root / "memory/handoffs.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(handoff["session_id"], "chat-12")
        self.assertEqual(handoff["workstream_id"], "retrieval")

        (self.root / ".omm/index.sqlite3").unlink()
        self.assertEqual(self.omm.search("replaceable")[0].id, record.id)
        count = self.omm.rebuild()
        self.assertEqual(count, 1)

    def test_invalid_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.omm.remember(MemoryRecord(kind="guess", title="x", content="y", source="z"))

    def test_semantic_embedding_timeout_defaults_to_two_minutes(self):
        with patch.dict(os.environ, {
            "OMM_SEMANTIC_ENABLED": "true",
            "OMM_EMBEDDING_URL": "http://ollama:11434/api/embed",
        }, clear=True):
            configured = OMM(self.root)
        self.assertEqual(configured.semantic.timeout, 120.0)

    def test_memory_proposal_can_be_reviewed_and_approved_once(self):
        original = MemoryRecord(kind="fact", title="Shared example protocol",
                                content="A generic protocol is shared.", source="guide.md", scope="demo")
        self.omm.remember(original)
        suggestion = MemoryRecord(kind="decision", title="Shared example protocol",
                                  content="Use the generic protocol in examples.", source="notes.md", scope="demo")
        proposal = self.omm.propose_memory(suggestion)
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["possible_matches"][0]["id"], original.id)
        accepted = self.omm.review_proposal(proposal["id"], True)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.omm.get_record(suggestion.id).content, suggestion.content)
        with self.assertRaisesRegex(ValueError, "já foi revisada"):
            self.omm.review_proposal(proposal["id"], False)

    def test_semantic_search_is_opt_in_and_rebuilds_disposable_vectors(self):
        with self.assertRaisesRegex(SemanticSearchError, "desligada"):
            self.omm.semantic_search("protocol")
        (self.root / "sources" / "demo").mkdir(parents=True)
        (self.root / "sources" / "demo" / "guide.md").write_text(
            "# Generic protocol\nA shared protocol helps projects coordinate.\n", encoding="utf-8")
        (self.root / "sources" / "other").mkdir(parents=True)
        (self.root / "sources" / "other" / "private.md").write_text(
            "# Unrelated project notes\nThis belongs to another project scope.\n", encoding="utf-8")
        self.omm.remember(MemoryRecord(kind="fact", title="Shared protocol",
                                      content="Projects use one shared protocol.", source="guide.md", scope="demo"))
        self.omm.remember(MemoryRecord(kind="fact", title="Unrelated protocol",
                                      content="This is outside the requested scope.", source="private.md", scope="other"))

        embedded_batches = []
        embedded_texts = []
        def fake_embed(request, timeout):
            payload = json.loads(request.data)
            embedded_batches.append(len(payload["input"]))
            embedded_texts.extend(payload["input"])
            return io.BytesIO(json.dumps({"embeddings": [[1.0, 0.0] for _ in payload["input"]]}).encode())

        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "true",
                                     "OMM_EMBEDDING_URL": "http://local.test/api/embed"}):
            semantic = OMM(self.root)
            with patch("omm.semantic.urlopen", side_effect=fake_embed):
                before_report = len(embedded_batches)
                stale_report = measure_performance(semantic, repetitions=2)
                self.assertFalse(stale_report["semantic_index_current"])
                self.assertNotIn("semantic_search", stale_report["measurements"])
                self.assertEqual(len(embedded_batches), before_report,
                                 "the report must not create a large semantic index")
                result = semantic.semantic_search("coordinating shared projects", scopes=["demo"])
                self.assertEqual(result["memories"][0]["title"], "Shared protocol")
                self.assertEqual(result["sources"][0]["source"],
                                 "sources/demo/guide.md:2-2")
                self.assertTrue(all("Unrelated" not in text and "another project scope" not in text
                                    for text in embedded_texts),
                                "a project search must not embed material from other scopes")
                self.assertEqual(embedded_batches[-1], 1,
                                 "one combined semantic search should embed its query only once")
                self.assertEqual(semantic.semantic.chunk_count(), 2,
                                 "only the requested scope should be added to the semantic index")
                self.assertEqual(semantic.semantic.indexed_fingerprint("demo"),
                                 semantic._canonical_fingerprint())
                self.assertIsNone(semantic.semantic.indexed_fingerprint(),
                                  "a scoped index must not claim the full corpus is indexed")
                other_result = semantic.semantic_search("another project scope", scopes=["other"])
                self.assertEqual(other_result["memories"][0]["title"], "Unrelated protocol")
                self.assertEqual(other_result["sources"][0]["scope"], "other")
                self.assertEqual(semantic.semantic.chunk_count(), 4,
                                 "adding a scope must preserve vectors already indexed for other scopes")
                before_report = len(embedded_batches)
                report = measure_performance(semantic, repetitions=2)
                self.assertTrue(report["semantic_index_current"])
                self.assertIn("semantic_search", report["measurements"])
                self.assertEqual(embedded_batches[before_report:], [1, 1],
                                 "the opt-in report should run only bounded generic queries")
                calls_before_full_rebuild = len(embedded_batches)
                self.assertGreater(semantic.rebuild_semantic(), 0)
                self.assertEqual(len(embedded_batches), calls_before_full_rebuild,
                                 "an explicit full rebuild should reuse all unchanged scoped vectors")
                self.assertEqual(semantic.semantic.indexed_fingerprint(),
                                 semantic._canonical_fingerprint())
                calls_after_full_rebuild = len(embedded_batches)
                semantic.rebuild_semantic()
                self.assertEqual(len(embedded_batches), calls_after_full_rebuild,
                                 "unchanged records and documents should reuse cached vectors")
                result = semantic.semantic_search("does not need a query vector", limit=0)
                self.assertEqual(result, {"memories": [], "sources": []})
                self.assertEqual(len(embedded_batches), calls_after_full_rebuild,
                                 "a zero result limit should not call the embedding service")

    def test_mcp_semantic_search_builds_in_background_without_blocking_omm(self):
        (self.root / "sources" / "demo").mkdir(parents=True)
        (self.root / "sources" / "demo" / "guide.md").write_text(
            "# Generic protocol\nA shared protocol helps projects coordinate.\n", encoding="utf-8")
        started, finish = Event(), Event()

        def slow_embed(request, timeout):
            started.set()
            if not finish.wait(2):
                raise TimeoutError("test embedding gate timed out")
            payload = json.loads(request.data)
            return io.BytesIO(json.dumps({"embeddings": [[1.0, 0.0] for _ in payload["input"]]}).encode())

        with patch.dict(os.environ, {"OMM_SEMANTIC_ENABLED": "true",
                                     "OMM_EMBEDDING_URL": "http://local.test/api/embed"}):
            semantic = OMM(self.root)
            semantic.remember(MemoryRecord(kind="fact", title="Shared protocol",
                content="Projects use one shared protocol.", source="guide.md", scope="demo"))
            with patch("omm.semantic.urlopen", side_effect=slow_embed):
                result = semantic.semantic_search_nonblocking("shared protocol", scopes=["demo"])
                self.assertEqual(result["status"], "building")
                self.assertTrue(started.wait(1))
                self.assertEqual(semantic.semantic_index_status(["demo"])["status"], "building")
                search_started = monotonic()
                self.assertEqual(semantic.search("shared protocol", scopes=["demo"])[0].title,
                                 "Shared protocol")
                self.assertLess(monotonic() - search_started, 0.5,
                                "semantic index generation must not hold the OMM data lock")
                finish.set()
                thread = semantic._semantic_job_thread
                self.assertIsNotNone(thread)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(semantic.semantic_index_status(["demo"])["status"], "ready")
                result = semantic.semantic_search_nonblocking("shared protocol", scopes=["demo"])
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["memories"][0]["title"], "Shared protocol")

    def test_web_dashboard_shows_usage_sources_and_can_archive_and_restore(self):
        record = MemoryRecord(kind="unknown", title="Cadência ainda desconhecida",
                              content="Medir a animação antes de propor uma causa.",
                              source="docs/HANDOFF.md", scope="project-beta")
        self.omm.remember(record)
        payload = dashboard_data(self.omm)
        self.assertEqual(payload["summary"]["active_count"], 1)
        self.assertEqual(payload["summary"]["unknown_count"], 1)
        self.assertGreater(payload["summary"]["memory_bytes"], 0)
        self.assertEqual(payload["records"][0]["source"], "docs/HANDOFF.md")

        source = self.root / "sources" / "project-beta" / "notes.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Server notes\n\nThe violet relay uses a fixed handshake.\n", encoding="utf-8")
        self.omm.rebuild()
        source_payload = dashboard_data(self.omm, "violet relay", "project-beta")
        self.assertGreater(source_payload["summary"]["source_bytes"], 0)
        self.assertEqual(source_payload["source_hits"][0]["source"],
                         "sources/project-beta/notes.md:3-3")
        (self.root / "skills" / "relay-expert").mkdir(parents=True)
        (self.root / "skills" / "relay-expert" / "SKILL.md").write_text(
            "---\nname: relay-expert\ndescription: Ajuda com o relay.\nmetadata:\n  scope: project:project-beta\n---\n",
            encoding="utf-8")
        (self.root / "memory" / "roles").mkdir(parents=True, exist_ok=True)
        (self.root / "memory" / "roles" / "beta.md").write_text("papel\n", encoding="utf-8")
        (self.root / "memory" / "agent-topology.json").write_text(json.dumps({
            "schema_version": 1, "project_session_model": "multiple_workflow_sessions_shared_omm",
            "workflow_session_model": "single_parent_with_subagents", "coordinator_role": "coordinator",
            "shared_memory": "omm_canonical",
            "subagents": [{"name": "beta-helper", "role_file": "skills/relay-expert/SKILL.md",
                           "activation": "when_project_beta", "reports_to": "coordinator"}],
            "project_profiles": {"project-beta": {
                "display_name": "Projeto Beta", "shared_scope": "project-beta",
                "agents": [{"name": "beta-expert", "role_file": "memory/roles/beta.md"}],
                "shared_project_knowledge": [{"name": "Protocolo comum", "used_by": ["beta-expert"]}],
            }}
        }), encoding="utf-8")
        organization = dashboard_data(self.omm, "", "project-beta")["organization"]
        self.assertEqual(organization["display_name"], "Projeto Beta")
        self.assertEqual(organization["agents"][0]["name"], "beta-expert")
        self.assertEqual(organization["skills"][0]["name"], "relay-expert")
        self.assertEqual(organization["shared_project_knowledge"][0]["name"], "Protocolo comum")
        self.assertEqual(dashboard_data(self.omm)["scope_labels"]["project-beta"], "Projeto Beta")
        self.assertEqual(organization["sources"][0]["title"], "Server notes")
        self.assertEqual(organization["sources"][0]["sha256"],
                         hashlib.sha256(source.read_bytes()).hexdigest())

        removed = self.omm.set_record_status(record.id, "retracted")
        self.assertEqual(removed.status, "retracted")
        self.assertEqual(self.omm.search("Cadência desconhecida"), [])
        self.assertEqual(dashboard_data(self.omm)["summary"]["archived_count"], 1)

        restored = self.omm.set_record_status(record.id, "active")
        self.assertEqual(restored.status, "active")
        self.assertEqual(self.omm.search("Cadência desconhecida")[0].id, record.id)
        with self.assertRaisesRegex(ValueError, "archive"):
            self.omm.delete_retracted_record(record.id)
        self.omm.set_record_status(record.id, "retracted")
        deleted = self.omm.delete_retracted_record(record.id)
        self.assertEqual(deleted.id, record.id)
        self.assertEqual(list(self.omm.store.records()), [])
        self.assertEqual(self.omm.search("Cadência desconhecida"), [])

    def test_performance_report_is_read_only_and_omits_memory_content(self):
        record = MemoryRecord(kind="fact", title="Private synthetic phrase",
                              content="This content must not appear in a performance report.",
                              source="fixture.md", scope="benchmark")
        self.omm.remember(record)
        before = self.omm.store.records_path.read_bytes()
        report = measure_performance(self.omm, repetitions=2)
        after = self.omm.store.records_path.read_bytes()
        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])
        self.assertTrue(report["index_current"])
        self.assertIn("search", report["measurements"])
        self.assertIn("context", report["measurements"])
        self.assertNotIn(record.content, json.dumps(report))

    def test_shared_scope_is_searchable_alongside_project_scope(self):
        self.omm.remember(MemoryRecord(kind="fact", title="example-domain protocol", content="Shared example-domain protocol detail.",
                                       source="docs/domain.md", scope="global"))
        self.omm.remember(MemoryRecord(kind="observation", title="Project result", content="Project-specific example-domain test result.",
                                       source="tests/result.txt", scope="project-alpha"))
        results = self.omm.search("example-domain", scopes=["global", "project-alpha"])
        self.assertEqual({record.scope for record in results}, {"global", "project-alpha"})
        only_global = self.omm.search("protocol", scopes=["global"])
        self.assertEqual([record.title for record in only_global], ["example-domain protocol"])

    def test_shared_service_handoffs_are_isolated_by_project_scope(self):
        self.omm.handoff("in_progress", "device-demo work", [], [], ["Check UART"],
                         "codex", scope="device-demo")
        self.omm.handoff("blocked", "Project Beta needs hardware evidence", ["Need console test"], [], [],
                         "chatgpt", scope="project-beta")
        self.assertEqual(self.omm.store.read_state(scope="device-demo")["summary"], "device-demo work")
        self.assertEqual(self.omm.store.read_state(scope="project-beta")["summary"],
                         "Project Beta needs hardware evidence")
        self.assertEqual(self.omm.store.read_state(scope="project-gamma")["status"], "not_started")

    def test_old_disposable_index_is_recreated_for_new_scope_column(self):
        db = sqlite3.connect(self.root / ".omm" / "index.sqlite3")
        db.execute("DROP TABLE records")
        db.execute("CREATE VIRTUAL TABLE records USING fts5(id UNINDEXED, kind UNINDEXED, title, content, source, tags)")
        db.commit()
        db.close()
        record = MemoryRecord(kind="fact", title="Index upgrade", content="Scopes migrate by rebuilding the index.",
                              source="docs/index.md", scope="global")
        self.omm.remember(record)
        found = self.omm.search("Scopes migrate", scopes=["global"])
        self.assertEqual(found[0].id, record.id)

    def test_search_treats_hyphenated_identifiers_as_plain_words(self):
        record = MemoryRecord(kind="observation", title="radio-module UART", content="radio-module uses GPIO1 and GPIO3.",
                              source="ChatGPT conversation", scope="device-demo")
        self.omm.remember(record)
        found = self.omm.search("radio-module", scopes=["device-demo"])
        self.assertEqual(found[0].id, record.id)

    def test_rebuild_indexes_source_documents_separately_and_context_cites_lines(self):
        source = self.root / "sources" / "project-alpha" / "research.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Frozen contract\n\nThe violet cartridge handshake stays stable.\n",
                          encoding="utf-8")
        self.omm.rebuild()
        hit = self.omm.search_sources("violet cartridge handshake", scopes=["project-alpha"])[0]
        self.assertEqual(hit.scope, "project-alpha")
        self.assertEqual(hit.source, "sources/project-alpha/research.md:3-3")
        self.assertEqual(list(self.omm.store.records()), [])
        context = self.omm.context("violet cartridge handshake", scopes=["project-alpha"])
        self.assertIn("## Fontes relacionadas", context)
        self.assertIn("sources/project-alpha/research.md:3-3", context)

    def test_large_historical_markdown_is_still_searchable(self):
        source = self.root / "sources" / "project-alpha" / "long-history.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Large history\n\nNeedleForLongHistory " + ("filler " * 760_000),
                          encoding="utf-8")
        self.omm.rebuild()
        hits = self.omm.search_sources("NeedleForLongHistory", scopes=["project-alpha"])
        self.assertTrue(hits)
        self.assertGreater(self.omm.source_chunk_count(), 1)

    def test_copilot_export_import_preserves_rendered_chat_and_redacts_secrets(self):
        export = self.root / "chat.json"
        export.write_text(json.dumps({
            "responderUsername": "GitHub Copilot",
            "requests": [{
                "timestamp": 1786546263049,
                "message": {"text": "Conserte isto; token ghp_" + "a" * 32},
                "response": [
                    {"kind": "thinking", "value": "hidden reasoning"},
                    {"value": "A correção está em src/main.c"},
                    {"kind": "toolInvocationSerialized", "value": "tool payload"},
                ],
            }],
        }), encoding="utf-8")
        result = import_copilot_chat(self.root, export, "project-alpha", "Chat histórico")
        self.assertEqual(result["interactions"], 1)
        self.assertEqual(result["redactions"]["tokens"], 1)
        archived = (self.root / result["path"]).read_text(encoding="utf-8")
        self.assertIn("A correção está em src/main.c", archived)
        self.assertIn("[TOKEN REMOVIDO]", archived)
        self.assertNotIn("hidden reasoning", archived)
        self.assertNotIn("tool payload", archived)

    def test_claude_export_import_keeps_visible_text_and_omits_tools_and_thinking(self):
        export = self.root / "session.jsonl"
        export.write_text("\n".join([
            json.dumps({"type": "user", "sessionId": "session-demo", "timestamp": "2026-09-01T12:00:00Z",
                       "message": {"content": [{"type": "text", "text": "Confirme o contrato documentado."},
                                                   {"type": "tool_result", "content": "hidden tool output"}]}}),
            json.dumps({"type": "assistant", "sessionId": "session-demo", "timestamp": "2026-09-01T12:00:10Z",
                       "message": {"content": [{"type": "thinking", "thinking": "hidden reasoning"},
                                                   {"type": "text", "text": "O contrato está no README."},
                                                   {"type": "tool_use", "name": "Read", "input": {"path": "private"}}]}}),
        ]) + "\n", encoding="utf-8")
        result = import_claude_session(self.root, export, "project-alpha", "Sessão antiga")
        self.assertEqual(result["messages"], 2)
        self.assertEqual(result["session_id"], "session-demo")
        archived = (self.root / result["path"]).read_text(encoding="utf-8")
        self.assertIn("O contrato está no README", archived)
        self.assertNotIn("hidden reasoning", archived)
        self.assertNotIn("hidden tool output", archived)

    def test_planner_and_executor_are_children_of_one_coordinator(self):
        topology_root = self.root / "topology-fixture"
        role_dir = topology_root / "memory/roles"
        role_dir.mkdir(parents=True)
        for name in ("planner", "executor", "specialist"):
            (role_dir / f"{name}.md").write_text(f"Role: {name}\n", encoding="utf-8")
        topology_file = {
            "schema_version": 1,
            "project_session_model": "multiple_workflow_sessions_shared_omm",
            "workflow_session_model": "single_parent_with_subagents",
            "default_mode": "coordinator_alone",
            "coordinator_role": "coordinator",
            "shared_memory": "omm_canonical",
            "subagents": [
                {"name": name, "role_file": f"memory/roles/{name}.md", "reports_to": "coordinator"}
                for name in ("planner", "executor")
            ],
            "project_profiles": {"demo": {
                "shared_scope": "demo", "shared_memory": "omm_canonical",
                "coordinator_role": "coordinator",
                "agents": [{"name": "demo-specialist", "role_file": "memory/roles/specialist.md",
                            "reports_to": "coordinator"}],
            }},
        }
        (topology_root / "memory").mkdir(exist_ok=True)
        (topology_root / "memory/agent-topology.json").write_text(json.dumps(topology_file), encoding="utf-8")
        topology = load_topology(topology_root)
        self.assertEqual(topology["project_session_model"], "multiple_workflow_sessions_shared_omm")
        self.assertEqual(topology["workflow_session_model"], "single_parent_with_subagents")
        self.assertEqual(topology["coordinator_role"], "coordinator")
        self.assertTrue({"planner", "executor"}.issubset({a["name"] for a in topology["subagents"]}))
        self.assertTrue(all(a["reports_to"] == "coordinator" for a in topology["subagents"]))
        self.assertEqual(topology["shared_memory"], "omm_canonical")
        self.assertEqual(topology["project_profiles"]["demo"]["shared_scope"], "demo")
        self.assertEqual(topology["project_profiles"]["demo"]["agents"][0]["name"], "demo-specialist")

    def test_multiple_workstreams_share_global_memory_and_keep_handoffs_scoped(self):
        self.omm.create_workstream("main-board", "main-board implementation")
        self.omm.create_workstream("board-radio", "board-plus-radio implementation")
        self.omm.remember(MemoryRecord(kind="constraint", title="Shared protocol invariant",
                                       content="Frame checksum covers header and payload.",
                                       source="docs/protocol.md"))
        self.omm.remember(MemoryRecord(kind="observation", title="main-board result",
                                       content="main-board handshake passes on the test fixture.",
                                       source="tests/run-17.txt", workstream_id="main-board", session_id="session-w"))
        self.omm.remember(MemoryRecord(kind="observation", title="ESP result",
                                       content="ESP bridge timing still needs measurement.",
                                       source="notes/esp.md", workstream_id="board-radio", session_id="session-esp"))
        scoped = self.omm.search("handshake", workstream_id="main-board")
        self.assertEqual([record.title for record in scoped], ["main-board result"])
        shared = self.omm.search("checksum", workstream_id="main-board")
        self.assertEqual([record.title for record in shared], ["Shared protocol invariant"])

        self.omm.handoff("in_progress", "main-board in progress", [], [], ["Run hardware test"],
                         "codex", session_id="session-w", workstream_id="main-board")
        self.omm.handoff("blocked", "ESP timing blocked", ["No measurement fixture"], [], [],
                         "copilot", session_id="session-esp", workstream_id="board-radio")
        self.assertEqual(self.omm.store.read_state("main-board")["summary"], "main-board in progress")
        self.assertEqual(self.omm.store.read_state("board-radio")["summary"], "ESP timing blocked")
        self.assertEqual(self.omm.store.read_state()["status"], "not_started")

    def test_copilot_history_is_staged_reviewed_then_imported_with_provenance(self):
        self.omm.create_workstream("mapper-project", "mapper project")
        payload = json.dumps({
            "format": "omm-history-v1", "platform": "github-copilot",
            "session_id": "copilot-session-42", "source_ref": "copilot-history://session-42",
            "workstream_id": "mapper-project",
            "records": [{"kind": "observation", "title": "Static verification boundary",
                          "content": "A successful build does not prove runtime mapper correctness.",
                          "source": "README.md#Building", "scope": "mapper-project", "evidence": ["make-verify-log.txt"],
                          "tags": ["domain:mapper-project"]}]
        }).encode()
        staged = stage_history(self.root, payload)
        self.assertEqual(staged["status"], "pending_review")
        self.assertIn("Static verification boundary", show_history_import(self.root, staged["id"])["records"][0]["title"])
        self.assertEqual(list(self.omm.store.records()), [])

        imported = accept_history(self.root, staged["id"], "reviewer")
        self.omm.rebuild()
        found = self.omm.search("runtime mapper correctness", workstream_id="mapper-project")[0]
        self.assertEqual(found.id, imported[0].id)
        self.assertEqual(found.session_id, "copilot-session-42")
        self.assertEqual(found.workstream_id, "mapper-project")
        self.assertEqual(found.scope, "mapper-project")
        self.assertIn("copilot-history://session-42", found.evidence)
        with self.assertRaises(HistoryImportError):
            accept_history(self.root, staged["id"], "reviewer")

    def test_scheduled_backup_pushes_memory_skills_and_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repo"
            remote = base / "remote.git"
            repository.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            (repository / "README.md").write_text("app code\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repository), "push", "-u", "origin", "main"], check=True, capture_output=True)

            (repository / "memory").mkdir()
            (repository / "memory" / "records.jsonl").write_text('{"title":"nova memória"}\n', encoding="utf-8")
            (repository / "skills").mkdir()
            (repository / "skills" / "expert.md").write_text("especialista\n", encoding="utf-8")
            (repository / "sources" / "project").mkdir(parents=True)
            (repository / "sources" / "project" / "notes.md").write_text("fonte\n", encoding="utf-8")
            (repository / ".omm").mkdir()
            (repository / ".omm" / "index.sqlite3").write_text("índice temporário", encoding="utf-8")

            result = backup_once(repository, "origin", "main", True, "", "", "OMM Backup", "omm@localhost")
            self.assertIn("enviado ao remoto", result)
            files = subprocess.run(["git", "--git-dir", str(remote), "show", "--pretty=", "--name-only", "refs/heads/main"],
                                   check=True, text=True, capture_output=True).stdout.splitlines()
            self.assertEqual(set(files), {"memory/records.jsonl", "skills/expert.md",
                                          "sources/project/notes.md"})

    def test_scheduled_backup_rejects_invalid_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            (repository / "memory").mkdir()
            (repository / "memory" / "records.jsonl").write_text("não é JSON\n", encoding="utf-8")
            with self.assertRaises(BackupError):
                backup_once(repository, "origin", "main", False, "", "", "OMM Backup", "omm@localhost")

    def test_scheduled_backup_preserves_pdf_sources_and_rejects_invalid_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            pdf = repository / "sources" / "sample-project" / "references" / "manual.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4\n% fixture\n")

            backup_once(repository, "origin", "main", False, "", "", "OMM Backup", "omm@localhost")
            self.assertIn("sources/sample-project/references/manual.pdf",
                          subprocess.run(["git", "-C", str(repository), "show", "--pretty=", "--name-only", "HEAD"],
                                         check=True, text=True, capture_output=True).stdout.splitlines())

            (pdf.parent / "invalid.pdf").write_bytes(b"not a PDF")
            with self.assertRaisesRegex(BackupError, "PDF-fonte inválido"):
                backup_once(repository, "origin", "main", False, "", "", "OMM Backup", "omm@localhost")

            (pdf.parent / "invalid.pdf").unlink()
            (pdf.parent / "unsupported.bin").write_bytes(b"binary")
            with self.assertRaisesRegex(BackupError, "use Markdown ou PDF"):
                backup_once(repository, "origin", "main", False, "", "", "OMM Backup", "omm@localhost")

    def test_sync_saves_local_mcp_changes_merges_backup_and_preserves_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "data"
            writer = base / "writer"
            remote = base / "backup.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(local)], check=True, capture_output=True)
            for repo in (local,):
                subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            (local / "memory").mkdir()
            initial = MemoryRecord(kind="fact", title="Base", content="Base data", source="test")
            (local / "memory" / "records.jsonl").write_text(initial.to_json() + "\n", encoding="utf-8")
            (local / "memory" / "policies.jsonl").write_text('{"id":"base-policy"}\n', encoding="utf-8")
            (local / "memory" / "handoffs.jsonl").write_text('{"id":"base-handoff"}\n', encoding="utf-8")
            (local / "memory" / "agent-topology.json").write_text('{"project_profiles":{}}\n', encoding="utf-8")
            (local / ".omm").mkdir()
            (local / ".omm" / "index.sqlite3").write_bytes(b"old derived index")
            subprocess.run(["git", "-C", str(local), "add", "memory", ".omm/index.sqlite3"], check=True)
            subprocess.run(["git", "-C", str(local), "commit", "-m", "base"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(local), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(local), "push", "-u", "origin", "main"], check=True, capture_output=True)
            subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
            subprocess.run(["git", "clone", str(remote), str(writer)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(writer), "config", "user.name", "Writer"], check=True)
            subprocess.run(["git", "-C", str(writer), "config", "user.email", "writer@example.invalid"], check=True)

            local_record = MemoryRecord(kind="observation", title="Local MCP", content="Created locally", source="mcp")
            with (local / "memory" / "records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(local_record.to_json() + "\n")
            (local / ".omm" / "index.sqlite3").write_bytes(b"rebuilt derived index")
            remote_record = MemoryRecord(kind="observation", title="Remote update", content="From backup", source="git")
            with (writer / "memory" / "records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(remote_record.to_json() + "\n")
            (local / "memory" / "agent-topology.json").write_text('{"project_profiles":{"local":{"name":"local"}}}\n', encoding="utf-8")
            (writer / "memory" / "agent-topology.json").write_text('{"project_profiles":{"remote":{"name":"remote"}}}\n', encoding="utf-8")
            local_role = local / "memory" / "roles" / "planner.md"
            remote_role = writer / "memory" / "roles" / "planner.md"
            local_role.parent.mkdir(parents=True)
            remote_role.parent.mkdir(parents=True)
            local_role.write_text("papel local do MCP\n", encoding="utf-8")
            remote_role.write_text("papel publicado no backup\n", encoding="utf-8")
            for filename in ("policies.jsonl", "handoffs.jsonl"):
                with (local / "memory" / filename).open("a", encoding="utf-8") as stream:
                    stream.write('{"id":"local-' + filename + '"}\n')
                with (writer / "memory" / filename).open("a", encoding="utf-8") as stream:
                    stream.write('{"id":"remote-' + filename + '"}\n')
            subprocess.run(["git", "-C", str(writer), "add", "memory"], check=True)
            subprocess.run(["git", "-C", str(writer), "commit", "-m", "backup changes"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(writer), "push", "origin", "main"], check=True, capture_output=True)

            # The production container may run as root without any global or
            # repository Git identity. Sync must still use the OMM author.
            subprocess.run(["git", "-C", str(local), "config", "--unset-all", "user.name"], check=True)
            subprocess.run(["git", "-C", str(local), "config", "--unset-all", "user.email"], check=True)
            result = sync_with_backup(local, author_name="OMM Test", author_email="omm@example.invalid")
            self.assertIn("conflito(s) preservado(s)", result)
            records = [json.loads(line)["id"] for line in (local / "memory" / "records.jsonl").read_text().splitlines()]
            self.assertEqual({initial.id, local_record.id, remote_record.id}, set(records))
            self.assertEqual(local_role.read_text(encoding="utf-8"), "papel publicado no backup\n")
            topology = json.loads((local / "memory" / "agent-topology.json").read_text(encoding="utf-8"))
            self.assertEqual(set(topology["project_profiles"]), {"local", "remote"})
            recovery = local / "memory" / "imports" / "sync-recovery"
            copies = list(recovery.glob("*/local/memory/roles/planner.md"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(encoding="utf-8"), "papel local do MCP\n")
            pushed = subprocess.run(["git", "--git-dir", str(remote), "show", "refs/heads/main:memory/records.jsonl"],
                                    check=True, text=True, capture_output=True).stdout
            self.assertIn(local_record.id, pushed)
            self.assertIn(".omm/index.sqlite3", subprocess.run(
                ["git", "-C", str(local), "status", "--short"], check=True,
                text=True, capture_output=True).stdout)

    def test_scheduled_backup_keeps_local_commit_when_remote_token_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            (repository / "memory").mkdir()
            (repository / "memory" / "records.jsonl").write_text('{"title":"local commit"}\n', encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "memory"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "remote", "add", "origin",
                            "https://git.example.invalid/user/repo"], check=True)
            (repository / "memory" / "records.jsonl").write_text('{"title":"new local commit"}\n', encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "commit salvo localmente"):
                backup_once(repository, "origin", "main", True, "user", "", "OMM Backup", "omm@localhost")
            latest = subprocess.run(["git", "-C", str(repository), "log", "-1", "--pretty=%s"],
                                    check=True, text=True, capture_output=True).stdout.strip()
            self.assertEqual(latest, "chore(omm): backup project data")
            self.assertEqual(subprocess.run(["git", "-C", str(repository), "status", "--porcelain"],
                                            check=True, text=True, capture_output=True).stdout, "")

    def test_restore_clones_memory_skills_sources_and_rebuilds_search_index(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            destination = base / "restored"
            remote = base / "remote.git"
            source.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            (source / "memory").mkdir()
            source_record = MemoryRecord(kind="fact", title="Restored fact", content="Git is canonical.",
                                         source="docs/git.md")
            (source / "memory" / "records.jsonl").write_text(source_record.to_json() + "\n", encoding="utf-8")
            (source / "skills").mkdir()
            (source / "skills" / "expert.md").write_text("domain skill\n", encoding="utf-8")
            (source / "sources" / "project-alpha").mkdir(parents=True)
            (source / "sources" / "project-alpha" / "rules.md").write_text(
                "# Frozen rule\n\nRestored source material is searchable.\n", encoding="utf-8")
            (source / "sources" / "project-alpha" / "manual.pdf").write_bytes(b"%PDF-1.4\n% test fixture\n")
            subprocess.run(["git", "-C", str(source), "add", "memory", "skills", "sources"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "backup"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True, capture_output=True)

            count = restore_from_git(destination, str(remote), "main")
            self.assertEqual(count, 1)
            self.assertTrue((destination / "skills" / "expert.md").is_file())
            self.assertTrue((destination / "sources" / "project-alpha" / "rules.md").is_file())
            self.assertEqual((destination / "sources" / "project-alpha" / "manual.pdf").read_bytes(),
                             b"%PDF-1.4\n% test fixture\n")
            self.assertTrue((destination / ".omm" / "index.sqlite3").is_file())
            restored = OMM(destination).search("canonical")
            self.assertEqual([record.title for record in restored], ["Restored fact"])
            source_hits = OMM(destination).search_sources("source material", scopes=["project-alpha"])
            self.assertEqual(source_hits[0].source, "sources/project-alpha/rules.md:3-3")
            self.assertEqual(subprocess.run(["git", "-C", str(destination), "remote", "get-url", "origin"],
                                            check=True, text=True, capture_output=True).stdout.strip(), str(remote))

    def test_restore_refuses_to_overwrite_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(RestoreError):
                restore_from_git(destination, "unused")
            self.assertTrue((destination / "keep.txt").is_file())

    def test_restore_source_uses_configured_url_or_data_repository_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                            "https://git.example.com/user/omm-data.git"], check=True)
            self.assertEqual(resolve_restore_source(root), "https://git.example.com/user/omm-data.git")
            self.assertEqual(resolve_restore_source(root, "https://backup.example/data.git"),
                             "https://backup.example/data.git")

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(resolve_restore_source(Path(directory)), "")

    def test_startup_restore_replaces_only_empty_omm_scaffold_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            destination = base / "restored"
            remote = base / "remote.git"
            source.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            (source / "memory").mkdir()
            record = MemoryRecord(kind="fact", title="Restored fact", content="Automatic restore works.",
                                  source="docs/restore.md")
            (source / "memory" / "records.jsonl").write_text(record.to_json() + "\n", encoding="utf-8")
            for name in ("policies.jsonl", "handoffs.jsonl", "workstreams.jsonl", "proposals.jsonl"):
                (source / "memory" / name).write_text("", encoding="utf-8")
            (source / "memory" / "state.json").write_text(json.dumps({
                "schema_version": 1, "status": "not_started", "summary": "", "blockers": [],
                "open_questions": [], "next_actions": [], "updated_at": None, "updated_by": None,
            }) + "\n", encoding="utf-8")
            (source / "skills").mkdir()
            (source / "skills" / "expert.md").write_text("skill\n", encoding="utf-8")
            (source / "sources" / "project-alpha").mkdir(parents=True)
            (source / "sources" / "project-alpha" / "history.md").write_text(
                "# Historical source\n\nAutomatic restore includes documents.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "memory", "skills", "sources"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "backup"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True, capture_output=True)

            # Simulate the empty files and the separate index volume created on first app start.
            OMM(destination).init()
            result = restore_on_start(destination, str(remote), "main")
            self.assertEqual(result, ("restaurada", 1))
            OMM(destination).init()
            self.assertEqual([item.title for item in OMM(destination).search("Automatic restore")], ["Restored fact"])
            self.assertTrue((destination / "skills" / "expert.md").is_file())
            self.assertTrue((destination / "sources" / "project-alpha" / "history.md").is_file())
            self.assertEqual(OMM(destination).search_sources("includes documents", scopes=["project-alpha"])[0].source,
                             "sources/project-alpha/history.md:3-3")
            self.assertEqual(restore_on_start(destination, str(remote), "main"), ("já atualizada", 0))

            newer = MemoryRecord(kind="decision", title="Remote update", content="Fetched on startup.",
                                 source="docs/restore.md", id="remote-update")
            with (source / "memory" / "records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(newer.to_json() + "\n")
            (source / "sources" / "project-alpha" / "new.md").write_text(
                "# New source\n\nSource updates are also indexed.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "memory/records.jsonl", "sources"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "add memory record"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True,
                           capture_output=True)
            self.assertEqual(restore_on_start(destination, str(remote), "main"), ("atualizada", 2))
            self.assertEqual([item.title for item in OMM(destination).search("Fetched on startup")],
                             ["Remote update"])
            self.assertEqual(OMM(destination).search_sources("also indexed", scopes=["project-alpha"])[0].source,
                             "sources/project-alpha/new.md:3-3")
            self.assertEqual(restore_on_start(destination, str(remote), "main"), ("já atualizada", 0))

    def test_startup_restore_skips_remote_update_when_local_data_is_uncommitted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            destination = base / "restored"
            remote = base / "remote.git"
            source.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            (source / "memory").mkdir()
            seed = MemoryRecord(kind="fact", title="Seed", content="Seed record.", source="test")
            (source / "memory/records.jsonl").write_text(seed.to_json() + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "memory"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "backup"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(source), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True,
                           capture_output=True)
            OMM(destination).init()
            self.assertEqual(restore_on_start(destination, str(remote), "main"), ("restaurada", 1))

            local = MemoryRecord(kind="observation", title="Local pending", content="Keep this unsynced edit.",
                                 source="test", id="local-pending")
            OMM(destination).remember(local)
            newer = MemoryRecord(kind="fact", title="Remote newer", content="Remote content.",
                                 source="test", id="remote-newer")
            with (source / "memory/records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(newer.to_json() + "\n")
            subprocess.run(["git", "-C", str(source), "add", "memory/records.jsonl"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-m", "remote addition"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True,
                           capture_output=True)

            self.assertEqual(restore_on_start(destination, str(remote), "main"),
                             ("alterações locais preservadas", 0))
            titles = {item.title for item in OMM(destination).store.records()}
            self.assertIn("Local pending", titles)
            self.assertNotIn("Remote newer", titles)

    def test_startup_restore_refuses_existing_memory_records(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing"
            omm = OMM(destination)
            omm.init()
            record = MemoryRecord(kind="fact", title="Keep me", content="Do not overwrite.", source="test")
            omm.remember(record)
            with self.assertRaisesRegex(RestoreError, "nada foi substituído"):
                restore_on_start(destination, "unused")
            self.assertEqual([item.title for item in omm.search("Do not overwrite")], ["Keep me"])

    def test_startup_restore_preserves_existing_source_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing"
            OMM(destination).init()
            document = destination / "sources" / "project-alpha" / "local.md"
            document.parent.mkdir(parents=True)
            document.write_text("local source\n", encoding="utf-8")
            with self.assertRaisesRegex(RestoreError, "nada foi substituído"):
                restore_on_start(destination, "unused")
            self.assertEqual(document.read_text(encoding="utf-8"), "local source\n")


if __name__ == "__main__":
    unittest.main()
