from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .adapters import GenericMarkdownAdapter
from .models import MemoryRecord
from .retrieval import SQLiteFTSRetriever
from .source_documents import SourceHit, read_source_chunks
from .store import CanonicalStore
from .locking import data_lock


class OMM:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.store = CanonicalStore(self.root)
        self.retriever = SQLiteFTSRetriever(self.root / ".omm" / "index.sqlite3")
        self.adapter = GenericMarkdownAdapter()
        self._lock = RLock()

    @contextmanager
    def operation_lock(self):
        with self._lock, data_lock(self.root):
            yield

    def init(self) -> None:
        with self.operation_lock():
            self.store.initialize()
            self.rebuild()

    def remember(self, record: MemoryRecord) -> None:
        with self.operation_lock():
            if record.workstream_id and not any(item["id"] == record.workstream_id for item in self.store.workstreams()):
                raise ValueError(f"unknown workstream: {record.workstream_id}; create it first")
            self.store.append(record)
            self.retriever.add(record)

    def set_record_status(self, record_id: str, status: str) -> MemoryRecord:
        with self.operation_lock():
            record = self.store.update_record_status(record_id, status)
            self.retriever.add(record)
            return record

    def delete_retracted_record(self, record_id: str) -> MemoryRecord:
        with self.operation_lock():
            record = self.store.delete_retracted_record(record_id)
            self.retriever.rebuild(list(self.store.records()))
            return record

    def search(self, query: str, limit: int = 10, workstream_id: str | None = None,
               scopes: list[str] | None = None) -> list[MemoryRecord]:
        with self.operation_lock():
            return self._search(query, limit, workstream_id, scopes)

    def _search(self, query: str, limit: int, workstream_id: str | None = None,
                scopes: list[str] | None = None) -> list[MemoryRecord]:
        if workstream_id and not any(item["id"] == workstream_id for item in self.store.workstreams()):
            raise ValueError(f"unknown workstream: {workstream_id}; create it first")
        if not (self.root / ".omm" / "index.sqlite3").exists():
            self.rebuild()
        canonical = {record.id: record for record in self.store.records()}
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

    def context(self, query: str, limit: int = 10, workstream_id: str | None = None,
                scopes: list[str] | None = None, state_scope: str | None = None) -> str:
        with self.operation_lock():
            rendered = self.adapter.render_context(self.search(query, limit, workstream_id, scopes),
                                                   self.store.read_state(workstream_id, state_scope))
            source_hits = self.search_sources(query, min(limit, 5), scopes)
            if source_hits:
                lines = [rendered, "", "## Trechos de documentos-fonte", "",
                         "Estes trechos ajudam a localizar a fonte original; confirme o contexto antes de tratá-los como evidência.", ""]
                for hit in source_hits:
                    locator = f"{hit.source}:{hit.start_line}-{hit.end_line}"
                    lines.extend([f"### [{hit.scope}] {hit.heading}", hit.content,
                                  f"Fonte: {locator} | Localizador, não autoridade", ""])
                return "\n".join(lines).rstrip() + "\n"
            return rendered

    def search_sources(self, query: str, limit: int = 10,
                       scopes: list[str] | None = None) -> list[SourceHit]:
        with self.operation_lock():
            if not (self.root / ".omm" / "index.sqlite3").exists():
                self.rebuild()
            return self.retriever.search_sources(query, limit, scopes)

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
            records = list(self.store.records())
            self.retriever.rebuild(records, read_source_chunks(self.root))
            return len(records)
