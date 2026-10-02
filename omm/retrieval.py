from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Protocol

from .models import MemoryRecord
from .source_documents import SourceChunk, SourceHit


class Retriever(Protocol):
    def rebuild(self, records: list[MemoryRecord], source_chunks: list[SourceChunk] | None = None) -> None: ...
    def add(self, record: MemoryRecord) -> None: ...
    def search(self, query: str, limit: int = 10) -> list[MemoryRecord]: ...


class SQLiteFTSRetriever:
    """Disposable FTS5 index. Canonical data always comes from JSONL."""

    def __init__(self, path: Path):
        self.path = path

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
        except sqlite3.OperationalError as exc:
            db.close()
            raise RuntimeError("SQLite FTS5 is required for lexical retrieval") from exc
        return db

    def rebuild(self, records: list[MemoryRecord], source_chunks: list[SourceChunk] | None = None) -> None:
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
        terms = list(dict.fromkeys(re.findall(r"[\w]+", query, flags=re.UNICODE)))
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
        terms = list(dict.fromkeys(re.findall(r"[\w]+", query, flags=re.UNICODE)))
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
