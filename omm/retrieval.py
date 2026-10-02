from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Protocol

from .models import MemoryRecord
from .source_documents import SourceChunk, SourceHit

MAX_QUERY_CHARS = 1200
MAX_QUERY_TERMS = 64


def _search_terms(query: str) -> list[str]:
    """Keep FTS queries small and interpret them as plain words."""
    return list(dict.fromkeys(re.findall(r"[\w]+", query[:MAX_QUERY_CHARS], flags=re.UNICODE)))[:MAX_QUERY_TERMS]


class Retriever(Protocol):
    """Contract for replaceable search indexes; implementations never own canonical data."""
    def file_signature(self) -> tuple[int, int, int] | None: ...
    def has_expected_schema(self) -> bool: ...
    def indexed_fingerprint(self) -> str | None: ...
    def set_indexed_fingerprint(self, fingerprint: str) -> None: ...
    def discard_index(self) -> None: ...
    def rebuild(self, records: list[MemoryRecord], source_chunks: list[SourceChunk] | None = None,
                fingerprint: str | None = None) -> None: ...
    def add(self, record: MemoryRecord) -> None: ...
    def search(self, query: str, limit: int = 10,
               scopes: list[str] | None = None) -> list[MemoryRecord]: ...
    def search_sources(self, query: str, limit: int = 10,
                       scopes: list[str] | None = None) -> list[SourceHit]: ...
    def source_chunk_count(self) -> int: ...


class SQLiteFTSRetriever:
    """Disposable FTS5 index. Canonical data always comes from JSONL."""

    def __init__(self, path: Path):
        self.path = path

    def file_signature(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
            return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except FileNotFoundError:
            return None

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            # The index is disposable. Recreate it when a previous OMM version
            # used a schema without scopes instead of asking the user to migrate it.
            existing = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
            if existing and "scope" not in {row[1] for row in db.execute("PRAGMA table_info(records)")}:
                db.execute("DROP TABLE records")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS records USING fts5(id UNINDEXED, kind UNINDEXED, scope UNINDEXED, title, content, source, tags)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks USING fts5(id UNINDEXED, scope UNINDEXED, path, heading, content, start_line UNINDEXED, end_line UNINDEXED)")
            db.execute("CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        except sqlite3.OperationalError as exc:
            db.close()
            raise RuntimeError("SQLite FTS5 is required for lexical retrieval") from exc
        return db

    def indexed_fingerprint(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM index_metadata WHERE key='canonical_fingerprint'").fetchone()
        return row[0] if row else None

    def has_expected_schema(self) -> bool:
        """Check the disposable index without creating or repairing its tables."""
        if not self.path.is_file():
            return False
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1) as db:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
                if not {"records", "source_chunks", "index_metadata"} <= tables:
                    return False
                record_columns = {row[1] for row in db.execute("PRAGMA table_info(records)")}
                source_columns = {row[1] for row in db.execute("PRAGMA table_info(source_chunks)")}
                metadata_columns = {row[1] for row in db.execute("PRAGMA table_info(index_metadata)")}
                return (
                    {"id", "kind", "scope", "title", "content", "source", "tags"} <= record_columns
                    and {"id", "scope", "path", "heading", "content", "start_line", "end_line"} <= source_columns
                    and {"key", "value"} <= metadata_columns
                )
        except sqlite3.Error:
            return False

    def discard_index(self) -> None:
        """Remove only the derived search database, including SQLite sidecars."""
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def set_indexed_fingerprint(self, fingerprint: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO index_metadata(key,value) VALUES('canonical_fingerprint',?)",
                       (fingerprint,))

    def rebuild(self, records: list[MemoryRecord], source_chunks: list[SourceChunk] | None = None,
                fingerprint: str | None = None) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM records")
            db.execute("DELETE FROM source_chunks")
            db.executemany(
                "INSERT INTO records(id,kind,scope,title,content,source,tags) VALUES(?,?,?,?,?,?,?)",
                [(r.id, r.kind, r.scope, r.title, r.content, r.source, " ".join(r.tags)) for r in records if r.status == "active"],
            )
            db.executemany(
                "INSERT INTO source_chunks(id,scope,path,heading,content,start_line,end_line) VALUES(?,?,?,?,?,?,?)",
                [(r.id, r.scope, r.path, r.heading, r.content, r.start_line, r.end_line)
                 for r in (source_chunks or [])],
            )
            if fingerprint is not None:
                db.execute("INSERT OR REPLACE INTO index_metadata(key,value) VALUES('canonical_fingerprint',?)",
                           (fingerprint,))

    def add(self, record: MemoryRecord) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM records WHERE id=?", (record.id,))
            if record.status == "active":
                db.execute("INSERT INTO records(id,kind,scope,title,content,source,tags) VALUES(?,?,?,?,?,?,?)",
                           (record.id, record.kind, record.scope, record.title, record.content, record.source, " ".join(record.tags)))

    def search(self, query: str, limit: int = 10, scopes: list[str] | None = None) -> list[MemoryRecord]:
        if not query.strip():
            return []
        # Treat user input as plain words, not SQLite FTS operators. This keeps
        # common identifiers such as "ESP-01" and "do-not-assume" searchable.
        terms = _search_terms(query)
        if not terms:
            return []
        # OR finds useful partial matches; BM25 ranks records containing more
        # of the query terms and gives title/content more weight than metadata.
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        with self._connect() as db:
            sql = "SELECT id,kind,scope,title,content,source,tags FROM records WHERE records MATCH ?"
            params: list[object] = [fts_query]
            if scopes is not None:
                if not scopes:
                    return []
                sql += f" AND scope IN ({','.join('?' for _ in scopes)})"
                params.extend(scopes)
            sql += " ORDER BY bm25(records,0,0,0,8,5,2,1) LIMIT ?"
            params.append(limit)
            rows = db.execute(sql, params).fetchall()
        return [MemoryRecord(kind=r[1], scope=r[2], title=r[3], content=r[4], source=r[5], id=r[0], tags=r[6].split()) for r in rows]

    def search_sources(self, query: str, limit: int = 10,
                       scopes: list[str] | None = None) -> list[SourceHit]:
        if not query.strip():
            return []
        terms = _search_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        with self._connect() as db:
            sql = ("SELECT id,scope,path,heading,start_line,end_line,content FROM source_chunks "
                   "WHERE source_chunks MATCH ?")
            params: list[object] = [fts_query]
            if scopes is not None:
                if not scopes:
                    return []
                sql += f" AND scope IN ({','.join('?' for _ in scopes)})"
                params.extend(scopes)
            sql += " ORDER BY bm25(source_chunks,0,0,5,8,1,0,0) LIMIT ?"
            params.append(limit)
            rows = db.execute(sql, params).fetchall()
        return [SourceHit(id=r[0], scope=r[1], path=r[2], heading=r[3],
                          start_line=r[4], end_line=r[5], content=r[6])
                for r in rows]

    def source_chunk_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT count(*) FROM source_chunks").fetchone()[0])
