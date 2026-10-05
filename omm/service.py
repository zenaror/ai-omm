from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock, RLock, Thread
from time import monotonic
import uuid

from .adapters import GenericMarkdownAdapter
from .models import MemoryRecord
from .retrieval import Retriever, SQLiteFTSRetriever
from .redaction import find_credentials
from .registries import save_role as save_role_file, save_skill as save_skill_file
from .source_documents import (SourceHit, delete_source_markdown, import_source_markdown,
                               read_source_chunks, replace_source_markdown)
from .semantic import OllamaSemanticIndex, SemanticSearchError
from .store import CanonicalStore
from .locking import data_lock


MAX_SEARCH_RESULTS = 100
MAX_SOURCE_RESULTS = 20
DEFAULT_CONTEXT_CHARS = 5000
MAX_CONTEXT_CHARS = 12000
INDEX_CHECK_INTERVAL_SECONDS = 10.0
_REON_GID = re.compile(r"(?<![A-Za-z0-9])g[0-9]{9}(?![A-Za-z0-9])", re.IGNORECASE)
_REON_GID_MARKER = "[REON ACCOUNT ID REMOVED]"


class OMM:
    def __init__(self, root: Path, retriever: Retriever | None = None):
        self.root = root.resolve()
        self.store = CanonicalStore(self.root)
        self.retriever: Retriever = retriever or SQLiteFTSRetriever(self.root / ".omm" / "index.sqlite3")
        self.adapter = GenericMarkdownAdapter()
        self._lock = RLock()
        self._record_cache_signature: tuple[int, int, int] | None = None
        self._record_cache: dict[str, MemoryRecord] = {}
        self._last_index_check = 0.0
        self._index_file_signature: tuple[int, int, int] | None = None
        self._index_schema_valid = False
        self.semantic: OllamaSemanticIndex | None = None
        self._semantic_job_lock = Lock()
        self._semantic_job_state: dict[str, object] = {"status": "idle"}
        self._semantic_job_thread: Thread | None = None
        if os.getenv("OMM_SEMANTIC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "sim", "on"}:
            endpoint = os.getenv("OMM_EMBEDDING_URL", "").strip()
            model = os.getenv("OMM_EMBEDDING_MODEL", "embeddinggemma").strip()
            if not endpoint:
                raise ValueError("Busca semântica ligada, mas OMM_EMBEDDING_URL está vazio.")
            if not model:
                raise ValueError("OMM_EMBEDDING_MODEL não pode ficar vazio.")
            try:
                timeout = float(os.getenv("OMM_EMBEDDING_TIMEOUT", "120"))
            except ValueError as exc:
                raise ValueError("OMM_EMBEDDING_TIMEOUT precisa ser um número de segundos.") from exc
            self.semantic = OllamaSemanticIndex(self.root / ".omm" / "index.sqlite3", endpoint, model, timeout)

    def _current_index_file_signature(self) -> tuple[int, int, int] | None:
        return self.retriever.file_signature()

    def _records_by_id(self) -> dict[str, MemoryRecord]:
        try:
            stat = self.store.records_path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except FileNotFoundError:
            signature = (0, 0, 0)
        if signature != self._record_cache_signature:
            self._record_cache = {record.id: record for record in self.store.records()}
            self._record_cache_signature = signature
        return self._record_cache

    @contextmanager
    def operation_lock(self):
        with self._lock, data_lock(self.root):
            yield

    def init(self) -> None:
        with self.operation_lock():
            self.store.initialize()
            self._ensure_index_current()

    def _canonical_fingerprint(self) -> str:
        """Notice canonical edits without rereading unchanged document contents."""
        digest = hashlib.sha256(b"omm-index-v2\0")
        candidates = [self.store.records_path]
        source_root = self.root / "sources"
        if source_root.is_dir():
            candidates.extend(path for path in source_root.rglob("*.md")
                              if path.is_file() and not path.is_symlink())
        for path in sorted(candidates, key=lambda item: item.as_posix()):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            digest.update(path.relative_to(self.root).as_posix().encode("utf-8"))
            digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\0{stat.st_ctime_ns}\n".encode())
        return digest.hexdigest()

    def _rebuild_index(self, fingerprint: str | None = None) -> int:
        records = list(self.store.records())
        signature = fingerprint or self._canonical_fingerprint()
        self.retriever.rebuild(records, read_source_chunks(self.root), signature)
        self._last_index_check = monotonic()
        self._index_file_signature = self._current_index_file_signature()
        self._index_schema_valid = True
        return len(records)

    def _ensure_index_current(self, force: bool = False) -> None:
        now = monotonic()
        file_signature = self._current_index_file_signature()
        # Check cheaply on every use whether the index file changed. If not,
        # avoid even opening SQLite until the normal canonical-file check.
        if (not force and self._index_schema_valid and file_signature == self._index_file_signature
                and now - self._last_index_check < INDEX_CHECK_INTERVAL_SECONDS):
            return
        fingerprint = self._canonical_fingerprint()
        schema_valid = self.retriever.has_expected_schema()
        try:
            index_changed = not schema_valid or self.retriever.indexed_fingerprint() != fingerprint
        except (OSError, sqlite3.Error):
            schema_valid = False
            index_changed = True
        if not schema_valid:
            # The SQLite file is disposable. A broken file must not block
            # access to the Git-backed records and documents.
            self.retriever.discard_index()
        if index_changed:
            self._rebuild_index(fingerprint)
        else:
            self._index_schema_valid = True
            self._index_file_signature = self._current_index_file_signature()
        self._last_index_check = now

    @staticmethod
    def _validate_memory_input(record: MemoryRecord) -> None:
        record.validate()
        if len(record.title) > 300 or len(record.content) > 20000 or len(record.source) > 1200:
            raise ValueError("A anotação está muito longa. Divida o texto ou guarde documentos em sources/.")
        if len(record.evidence) > 30 or any(len(item) > 1200 for item in record.evidence):
            raise ValueError("A anotação tem evidências demais ou muito longas. Guarde só as referências principais.")
        if len(record.tags) > 30 or any(len(item) > 80 for item in record.tags):
            raise ValueError("A anotação tem marcadores demais ou muito longos.")
        sensitive_text = "\n".join([record.title, record.content, record.source, *record.evidence])
        if find_credentials(sensitive_text):
            raise ValueError("A anotação parece conter uma senha, token ou chave privada. "
                             "Remova a credencial e tente novamente; a OMM não a salvou.")

    def remember(self, record: MemoryRecord) -> None:
        self._validate_memory_input(record)
        with self.operation_lock():
            self._ensure_index_current(force=True)
            if record.workstream_id and not any(item["id"] == record.workstream_id for item in self.store.workstreams()):
                raise ValueError(f"unknown workstream: {record.workstream_id}; create it first")
            self.store.append(record)
            self.retriever.add(record)
            self.retriever.set_indexed_fingerprint(self._canonical_fingerprint())
            self._last_index_check = monotonic()
            self._index_file_signature = self._current_index_file_signature()
            self._index_schema_valid = True

    def propose_memory(self, record: MemoryRecord) -> dict:
        """Queue a proposed memory for human review with lexical near-match hints."""
        self._validate_memory_input(record)
        with self.operation_lock():
            matches = self._search(record.title, 3, scopes=[record.scope])
            proposal = {
                "id": str(uuid.uuid4()), "status": "pending", "proposed_at": record.created_at,
                "record": json.loads(record.to_json()),
                "possible_matches": [{"id": item.id, "kind": item.kind, "title": item.title,
                                      "content": item.content[:300], "source": item.source,
                                      "scope": item.scope}
                                     for item in matches if item.id != record.id],
            }
            self.store.append_proposal(proposal)
            return proposal

    def proposals(self, pending_only: bool = True) -> list[dict]:
        with self.operation_lock():
            return self.store.proposals(pending_only)

    def review_proposal(self, proposal_id: str, approve: bool) -> dict:
        with self.operation_lock():
            proposal = next((item for item in self.store.proposals()
                             if item.get("id") == proposal_id), None)
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.get("status") != "pending":
                raise ValueError("Esta sugestão já foi revisada.")
            record_id = None
            if approve:
                record = MemoryRecord.from_json(json.dumps(proposal["record"], ensure_ascii=False))
                if record.id not in self._records_by_id():
                    self.remember(record)
                record_id = record.id
            updated = self.store.update_proposal(proposal_id, "accepted" if approve else "rejected", record_id)
            return updated

    def set_record_status(self, record_id: str, status: str) -> MemoryRecord:
        with self.operation_lock():
            self._ensure_index_current(force=True)
            record = self.store.update_record_status(record_id, status)
            self.retriever.add(record)
            self.retriever.set_indexed_fingerprint(self._canonical_fingerprint())
            self._last_index_check = monotonic()
            self._index_file_signature = self._current_index_file_signature()
            self._index_schema_valid = True
            return record

    def delete_retracted_record(self, record_id: str) -> MemoryRecord:
        with self.operation_lock():
            record = self.store.delete_retracted_record(record_id)
            self._rebuild_index()
            return record

    def search(self, query: str, limit: int = 10, workstream_id: str | None = None,
               scopes: list[str] | None = None) -> list[MemoryRecord]:
        with self.operation_lock():
            return self._search(query, limit, workstream_id, scopes)

    def get_record(self, record_id: str) -> MemoryRecord:
        with self.operation_lock():
            record = self._records_by_id().get(record_id)
            if record is None:
                raise KeyError(record_id)
            return record

    def get_record_digest(self, record_id: str) -> dict[str, str]:
        """Return memory metadata and body digest without exposing the body."""
        with self.operation_lock():
            record = self._records_by_id().get(record_id)
            if record is None:
                raise KeyError(record_id)
            return {
                "id": record.id,
                "scope": record.scope,
                "status": record.status,
                "content_sha256": hashlib.sha256(record.content.encode("utf-8")).hexdigest(),
            }

    def redact_record_content(self, record_id: str, redaction_rule: str,
                              expected_sha256: str) -> dict[str, object]:
        """Redact a supported identifier in a non-active record without returning its value."""
        if redaction_rule != "reon_gid":
            raise ValueError("redaction_rule must be reon_gid")
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256.lower())):
            raise ValueError("expected_sha256 must be a 64-character SHA-256 digest")
        with self.operation_lock():
            record = self._records_by_id().get(record_id)
            if record is None:
                raise KeyError(record_id)
            if record.status == "active":
                raise ValueError("active memory records cannot be redacted")
            actual_sha256 = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
            if actual_sha256 != expected_sha256.lower():
                raise ValueError("The memory record changed since it was reviewed")
            redacted_content, count = _REON_GID.subn(_REON_GID_MARKER, record.content)
            if count == 0:
                raise ValueError("No REON account ID matching the selected rule was found")
            updated = MemoryRecord.from_json(record.to_json())
            updated.content = redacted_content
            self._validate_memory_input(updated)
            self.store.replace_record_content(record_id, redacted_content, expected_sha256)
            self._record_cache_signature = None
            self._rebuild_index()
            stored = self._records_by_id()[record_id]
            return {
                "id": stored.id,
                "scope": stored.scope,
                "status": stored.status,
                "redaction_rule": redaction_rule,
                "redacted_occurrences": count,
                "content_sha256": hashlib.sha256(stored.content.encode("utf-8")).hexdigest(),
            }

    def records(self) -> list[MemoryRecord]:
        """List canonical records, reusing the cache until the Git file changes."""
        with self.operation_lock():
            return list(self._records_by_id().values())

    def _search(self, query: str, limit: int, workstream_id: str | None = None,
                scopes: list[str] | None = None) -> list[MemoryRecord]:
        limit = max(0, min(int(limit), MAX_SEARCH_RESULTS))
        if limit == 0:
            return []
        if workstream_id and not any(item["id"] == workstream_id for item in self.store.workstreams()):
            raise ValueError(f"unknown workstream: {workstream_id}; create it first")
        self._ensure_index_current()
        canonical = self._records_by_id()
        search_limit = limit if workstream_id is None else max(limit * 10, 100)
        selected = []
        while True:
            hits = self.retriever.search(query, search_limit, scopes)
            selected = [canonical[hit.id] for hit in hits if hit.id in canonical and
                        (workstream_id is None or canonical[hit.id].workstream_id in (None, workstream_id))]
            if len(selected) >= limit or len(hits) < search_limit or search_limit >= 5000:
                break
            search_limit *= 2
        # The index returns ordering and IDs only. Render the actual canonical
        # records so provenance and status always come from Git-backed data.
        return selected[:limit]

    def context(self, query: str, limit: int = 5, workstream_id: str | None = None,
                scopes: list[str] | None = None, state_scope: str | None = None,
                include_sources: bool = True, budget_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
        with self.operation_lock():
            budget = max(800, min(int(budget_chars), MAX_CONTEXT_CHARS))
            selected = self.search(query, min(limit, 10), workstream_id, scopes)
            rendered = self.adapter.render_context(
                selected, self.store.read_state(workstream_id, state_scope), budget
            )
            if not include_sources:
                return rendered
            remaining = budget - len(rendered)
            if remaining < 180:
                return rendered
            source_hits = self.search_sources(query, min(max(0, limit), 2), scopes)
            additions = "\n## Fontes relacionadas\n"
            for hit in source_hits:
                remaining = budget - len(rendered) - len(additions)
                if remaining < 180:
                    break
                locator = f"{hit.path}:{hit.start_line}-{hit.end_line}"
                heading = f"\n### [{hit.scope}] {hit.heading}\nFonte: {locator}\n"
                content_budget = min(1200, max(0, remaining - len(heading) - 30))
                if content_budget < 80:
                    break
                content = hit.content[:content_budget]
                if len(content) < len(hit.content):
                    content = content.rstrip() + "…"
                additions += heading + content + "\n"
            if additions == "\n## Fontes relacionadas\n":
                return rendered
            return ((rendered + additions).rstrip() + "\n")[:budget]

    def search_sources(self, query: str, limit: int = 10,
                       scopes: list[str] | None = None) -> list[SourceHit]:
        with self.operation_lock():
            self._ensure_index_current()
            return self.retriever.search_sources(query, max(0, min(int(limit), MAX_SOURCE_RESULTS)), scopes)

    def import_source(self, scope: str, relative_path: str, content: str) -> dict[str, object]:
        """Import a Markdown source into canonical data and refresh retrieval lazily."""
        if not isinstance(content, str):
            raise ValueError("O conteúdo precisa ser texto Markdown.")
        if find_credentials(content):
            raise ValueError("A fonte parece conter senha, token ou chave privada; remova o segredo antes de importar.")
        with self.operation_lock():
            result = import_source_markdown(self.root, scope, relative_path, content)
            if result["status"] == "imported":
                self._index_schema_valid = False
        return result

    def replace_source(self, source: str, content: str,
                       expected_sha256: str) -> dict[str, object]:
        """Safely edit an existing Markdown source after checking its current hash."""
        if not isinstance(content, str):
            raise ValueError("O conteúdo precisa ser texto Markdown.")
        if find_credentials(content):
            raise ValueError("O novo conteúdo parece conter senha, token ou chave privada; remova o segredo antes de salvar.")
        with self.operation_lock():
            result = replace_source_markdown(self.root, source, content, expected_sha256)
            self._index_schema_valid = False
        return result

    def delete_source(self, source: str, expected_sha256: str) -> dict[str, object]:
        """Remove one source from current canonical files after a hash check."""
        with self.operation_lock():
            result = delete_source_markdown(self.root, source, expected_sha256)
            self._index_schema_valid = False
        return result

    def save_skill(self, name: str, content: str,
                   expected_sha256: str | None = None) -> dict[str, object]:
        """Create a skill, or update it only when the caller confirms its current hash."""
        with self.operation_lock():
            return save_skill_file(self.root, name, content, expected_sha256)

    def save_role(self, name: str, content: str,
                  expected_sha256: str | None = None) -> dict[str, object]:
        """Create a role, or update it only when the caller confirms its current hash."""
        with self.operation_lock():
            return save_role_file(self.root, name, content, expected_sha256)

    def rebuild_semantic(self) -> int:
        """Create the optional vector index from canonical records and source files."""
        if self.semantic is None:
            raise SemanticSearchError("Busca semântica desligada. Configure OMM_SEMANTIC_ENABLED=true e os dados do serviço de embeddings.")
        with self.operation_lock():
            self._ensure_index_current()
            fingerprint = self._canonical_fingerprint()
            return self.semantic.rebuild(list(self.store.records()), read_source_chunks(self.root), fingerprint)

    def _semantic_stale_scopes(self, fingerprint: str, scopes: list[str] | None) -> list[str] | None:
        assert self.semantic is not None
        if scopes is None:
            return None if self.semantic.indexed_fingerprint() != fingerprint else []
        return sorted({scope for scope in scopes
                       if self.semantic.indexed_fingerprint(scope) != fingerprint})

    def _semantic_query_current(self, query: str, mode: str, limit: int,
                                scopes: list[str] | None) -> dict[str, list[dict]]:
        """Run retrieval while the caller holds the OMM operation lock."""
        assert self.semantic is not None
        count = max(0, min(int(limit), 10))
        query_vector = None
        if mode == "all" and count and query.strip() and scopes != []:
            query_vector = self.semantic.embed_query(query)
        output: dict[str, list[dict]] = {"memories": [], "sources": []}
        if mode in {"all", "memory"}:
            canonical = self._records_by_id()
            for score, record_id in self.semantic.search_memories(query, count, scopes, query_vector):
                record = canonical.get(record_id)
                if record:
                    output["memories"].append({"id": record.id, "kind": record.kind,
                        "title": record.title, "content": record.content[:600],
                        "source": record.source, "scope": record.scope,
                        "confidence": record.confidence, "score": round(score, 4)})
        if mode in {"all", "sources"}:
            output["sources"] = [{"id": hit.id, "heading": hit.heading,
                "content": hit.content[:1000], "source": hit.source,
                "scope": hit.scope, "score": round(score, 4)}
                for score, hit in self.semantic.search_sources(query, count, scopes, query_vector)]
        return output

    def _start_semantic_rebuild(self, scopes: list[str] | None) -> dict[str, object]:
        assert self.semantic is not None
        with self._semantic_job_lock:
            if self._semantic_job_state.get("status") == "building":
                return dict(self._semantic_job_state)
            self._semantic_job_state = {
                "status": "building", "scopes": scopes, "completed": 0,
                "total": None, "started_at": datetime.now(timezone.utc).isoformat(),
                "error_type": None,
            }

            def update_progress(completed: int, total: int) -> None:
                with self._semantic_job_lock:
                    self._semantic_job_state["completed"] = completed
                    self._semantic_job_state["total"] = total

            def run() -> None:
                try:
                    # Snapshot canonical inputs briefly, then release the shared
                    # data lock before the slow network/GPU embedding batches.
                    with self.operation_lock():
                        self._ensure_index_current()
                        fingerprint = self._canonical_fingerprint()
                        current_scopes = self._semantic_stale_scopes(fingerprint, scopes)
                        if current_scopes == []:
                            with self._semantic_job_lock:
                                self._semantic_job_state.update(status="ready", completed=0, total=0)
                            return
                        records = list(self.store.records())
                        chunks = read_source_chunks(self.root)
                    self.semantic.rebuild(records, chunks, fingerprint, current_scopes, update_progress)
                except Exception as exc:  # status carries a safe class name, not endpoint details
                    with self._semantic_job_lock:
                        self._semantic_job_state.update(status="failed", error_type=type(exc).__name__)
                else:
                    with self._semantic_job_lock:
                        self._semantic_job_state.update(status="ready", error_type=None)

            thread = Thread(target=run, name="omm-semantic-index", daemon=True)
            self._semantic_job_thread = thread
            state = dict(self._semantic_job_state)
        thread.start()
        return state

    def semantic_index_status(self, scopes: list[str] | None = None) -> dict[str, object]:
        """Report semantic index state without waiting for an embedding job."""
        if self.semantic is None:
            return {"enabled": False, "status": "disabled", "indexed_entries": 0}
        with self._semantic_job_lock:
            job = dict(self._semantic_job_state)
        if job.get("status") == "building":
            return {"enabled": True, **job, "indexed_entries": self.semantic.chunk_count()}
        with self.operation_lock():
            fingerprint = self._canonical_fingerprint()
            current = (self.semantic.indexed_fingerprint() == fingerprint if scopes is None else
                       all(self.semantic.indexed_fingerprint(scope) == fingerprint for scope in scopes))
            entries = self.semantic.chunk_count()
        status = "ready" if current else ("failed" if job.get("status") == "failed" else "stale")
        return {"enabled": True, "status": status, "scopes": scopes,
                "indexed_entries": entries, "error_type": job.get("error_type")}

    def semantic_search_nonblocking(self, query: str, mode: str = "all", limit: int = 5,
                                    scopes: list[str] | None = None) -> dict[str, object]:
        """MCP-friendly search that starts a cold index build without holding a request open."""
        if self.semantic is None:
            raise SemanticSearchError("Busca semântica desligada. Configure OMM_SEMANTIC_ENABLED=true e reinicie a OMM.")
        if mode not in {"all", "memory", "sources"}:
            raise ValueError("mode precisa ser all, memory ou sources.")
        with self.operation_lock():
            self._ensure_index_current()
            fingerprint = self._canonical_fingerprint()
            stale_scopes = self._semantic_stale_scopes(fingerprint, scopes)
            if stale_scopes == []:
                return {"status": "ready", **self._semantic_query_current(query, mode, limit, scopes)}
        job = self._start_semantic_rebuild(stale_scopes)
        state = str(job.get("status", "building"))
        message = ("O índice semântico está sendo preparado. Use semantic_index_status para acompanhar; "
                   "a busca lexical continua disponível. Tente semantic_search novamente quando o estado for ready.")
        if state == "building" and job.get("scopes") != stale_scopes:
            message = ("Outra reconstrução semântica está em andamento. Aguarde o estado ready e tente "
                       "semantic_search novamente para este escopo.")
        return {"status": state, "index_status": job, "memories": [], "sources": [], "message": message}

    def semantic_search(self, query: str, mode: str = "all", limit: int = 5,
                        scopes: list[str] | None = None) -> dict[str, list[dict]]:
        """Search by meaning only when explicitly requested by the caller."""
        if self.semantic is None:
            raise SemanticSearchError("Busca semântica desligada. Configure OMM_SEMANTIC_ENABLED=true e reinicie a OMM.")
        if mode not in {"all", "memory", "sources"}:
            raise ValueError("mode precisa ser all, memory ou sources.")
        with self.operation_lock():
            self._ensure_index_current()
            fingerprint = self._canonical_fingerprint()
            stale_scopes = self._semantic_stale_scopes(fingerprint, scopes)
            if stale_scopes is None or stale_scopes:
                self.semantic.rebuild(list(self.store.records()), read_source_chunks(self.root),
                                      fingerprint, stale_scopes)
            return self._semantic_query_current(query, mode, limit, scopes)

    def source_chunk_count(self) -> int:
        with self.operation_lock():
            return self.retriever.source_chunk_count()

    def create_workstream(self, workstream_id: str, title: str, actor: str = "human", status: str = "active") -> dict:
        with self.operation_lock():
            if not title.strip():
                raise ValueError("workstream title cannot be empty")
            return self.store.create_workstream(workstream_id, title, actor, status)

    def workstreams(self) -> list[dict]:
        with self.operation_lock():
            return self.store.workstreams()

    def handoff(self, status: str, summary: str, blockers: list[str], questions: list[str], next_actions: list[str], actor: str, session_id: str | None = None, workstream_id: str | None = None, scope: str = "default") -> None:
        with self.operation_lock():
            if workstream_id and not any(item["id"] == workstream_id for item in self.store.workstreams()):
                raise ValueError(f"unknown workstream: {workstream_id}; create it first")
            state = {
                "schema_version": 1, "status": status, "summary": summary,
                "blockers": blockers, "open_questions": questions, "next_actions": next_actions, "scope": scope,
                "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "updated_by": actor, "session_id": session_id, "workstream_id": workstream_id,
            }
            self.store.append_handoff(state)
            if workstream_id is None and scope in {"default", "global"}:
                self.store.write_state(state)

    def rebuild(self) -> int:
        with self.operation_lock():
            return self._rebuild_index()
