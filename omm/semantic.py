"""Optional semantic index backed by a local Ollama embedding endpoint."""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from pathlib import Path
import re
import sqlite3
import struct
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import MemoryRecord
from .source_documents import SourceChunk, SourceHit


class SemanticSearchError(RuntimeError):
    pass


_STORAGE_VERSION = "2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_content_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _pack_vector(vector: list[float]) -> bytes:
    if not vector:
        raise SemanticSearchError("O serviço de embeddings devolveu um vetor vazio.")
    if any(not math.isfinite(value) for value in vector):
        raise SemanticSearchError("O serviço de embeddings devolveu valores inválidos.")
    try:
        return struct.pack("<" + "f" * len(vector), *vector)
    except (OverflowError, struct.error) as exc:
        raise SemanticSearchError("O serviço de embeddings devolveu valores inválidos.") from exc


def _unpack_vector(value: object) -> list[float]:
    """Read compact float32 vectors and the JSON vectors written by older OMM versions."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid legacy vector") from exc
        if not isinstance(decoded, list):
            raise ValueError("invalid legacy vector")
        vector = [float(component) for component in decoded]
        if any(not math.isfinite(component) for component in vector):
            raise ValueError("invalid legacy vector")
        return vector
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if not raw or len(raw) % 4:
            raise ValueError("invalid compact vector")
        vector = list(struct.unpack("<" + "f" * (len(raw) // 4), raw))
        if any(not math.isfinite(component) for component in vector):
            raise ValueError("invalid compact vector")
        return vector
    raise ValueError("invalid vector storage")


class OllamaSemanticIndex:
    """Optional vectors stored in the disposable SQLite search database."""

    def __init__(self, path: Path, endpoint: str, model: str, timeout: float = 10.0):
        self.path = path
        self.endpoint = endpoint
        self.model = model
        self.timeout = max(1.0, min(float(timeout), 120.0))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        request = Request(self.endpoint, data=json.dumps({
            "model": self.model, "input": texts, "truncate": True,
        }).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except (OSError, HTTPError, URLError, json.JSONDecodeError) as exc:
            raise SemanticSearchError(
                f"Não consegui acessar o serviço local de embeddings em {self.endpoint}. "
                "Confira se o Ollama está ligado e se o modelo escolhido foi baixado."
            ) from exc
        vectors = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise SemanticSearchError("O serviço de embeddings devolveu uma resposta inesperada.")
        normalized = []
        dimension = None
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise SemanticSearchError("O serviço de embeddings devolveu um vetor vazio.")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise SemanticSearchError("O serviço de embeddings devolveu valores inválidos.") from exc
            if any(not math.isfinite(value) for value in values):
                raise SemanticSearchError("O serviço de embeddings devolveu valores inválidos.")
            if dimension is not None and len(values) != dimension:
                raise SemanticSearchError("Os vetores devolvidos têm tamanhos diferentes.")
            dimension = len(values)
            norm = math.sqrt(sum(value * value for value in values))
            if norm == 0:
                raise SemanticSearchError("O serviço de embeddings devolveu um vetor sem direção.")
            normalized.append([value / norm for value in values])
        return normalized

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise SemanticSearchError("O índice de busca ainda não existe; inicie a OMM antes da busca semântica.")
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS semantic_vectors ("
                   "kind TEXT NOT NULL, id TEXT NOT NULL, scope TEXT NOT NULL, title TEXT NOT NULL, "
                   "content_hash TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', "
                   "source TEXT NOT NULL, start_line INTEGER, end_line INTEGER, "
                   "vector BLOB NOT NULL, PRIMARY KEY(kind,id))")
        columns = {row[1] for row in db.execute("PRAGMA table_info(semantic_vectors)")}
        if "content_hash" not in columns:
            # Older derived indexes stored the complete embedding input in content.
            db.execute("ALTER TABLE semantic_vectors ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE TABLE IF NOT EXISTS semantic_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return db

    def indexed_fingerprint(self, scope: str | None = None) -> str | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                key = "fingerprint" if scope is None else "scope:" + scope
                row = db.execute("SELECT value FROM semantic_metadata WHERE key=?", (key,)).fetchone()
                model = db.execute("SELECT value FROM semantic_metadata WHERE key='model'").fetchone()
            return row[0] if row and model and model[0] == self.model else None
        except sqlite3.Error:
            return None

    def rebuild(self, records: list[MemoryRecord], chunks: list[SourceChunk], fingerprint: str,
                scopes: list[str] | None = None,
                progress: Callable[[int, int], None] | None = None) -> int:
        selected_scopes = None if scopes is None else set(scopes)
        items: list[tuple[str, str, str, str, str, str, int | None, int | None]] = []
        items.extend(("memory", record.id, record.scope, record.title,
                      record.title + "\n" + record.content + "\n" + " ".join(record.tags),
                      record.source, None, None)
                     for record in records if record.status == "active" and
                     (selected_scopes is None or record.scope in selected_scopes))
        items.extend(("source", chunk.id, chunk.scope, chunk.heading,
                      chunk.heading + "\n" + chunk.content, chunk.path,
                      chunk.start_line, chunk.end_line) for chunk in chunks if
                     (selected_scopes is None or chunk.scope in selected_scopes))
        # Reuse unchanged vectors when only one memory or source has changed.
        # This keeps ordinary updates from sending the whole collection again.
        cached: dict[tuple[str, str], tuple[tuple, list[float]]] = {}
        if self.path.is_file():
            try:
                with self._connect() as db:
                    model_row = db.execute("SELECT value FROM semantic_metadata WHERE key='model'").fetchone()
                    if model_row and model_row[0] == self.model:
                        sql = ("SELECT kind,id,scope,title,content_hash,content,source,start_line,end_line,vector "
                               "FROM semantic_vectors")
                        params: tuple[str, ...] = ()
                        if selected_scopes is not None:
                            if not selected_scopes:
                                sql += " WHERE 0"
                            else:
                                sql += " WHERE scope IN (" + ",".join("?" for _ in selected_scopes) + ")"
                                params = tuple(sorted(selected_scopes))
                        for row in db.execute(sql, params):
                            try:
                                saved_content = row[4] or row[5]
                                cached_item = (*row[:4], saved_content, *row[6:9])
                                cached[(row[0], row[1])] = (cached_item, _unpack_vector(row[9]))
                            except (TypeError, ValueError, json.JSONDecodeError, struct.error):
                                continue
            except sqlite3.Error:
                pass
        vectors: list[list[float] | None] = [None] * len(items)
        pending: list[tuple[int, tuple]] = []
        for position, item in enumerate(items):
            previous = cached.get((item[0], item[1]))
            if previous and self._cached_item_matches(previous[0], item):
                vectors[position] = previous[1]
            else:
                pending.append((position, item))
        completed = len(items) - len(pending)
        if progress is not None:
            progress(completed, len(items))
        for offset in range(0, len(pending), 32):
            batch = pending[offset:offset + 32]
            embedded = self._embed([item[4] for _, item in batch])
            for (position, _), vector in zip(batch, embedded):
                vectors[position] = vector
            completed += len(batch)
            if progress is not None:
                progress(completed, len(items))
        if any(vector is None for vector in vectors):
            raise SemanticSearchError("O índice semântico ficou incompleto; tente reconstruí-lo novamente.")
        complete_vectors = [vector for vector in vectors if vector is not None]
        with self._connect() as db:
            if selected_scopes is None:
                db.execute("DELETE FROM semantic_vectors")
                db.execute("DELETE FROM semantic_metadata WHERE key='fingerprint' OR key LIKE 'scope:%'")
            elif selected_scopes:
                placeholders = ",".join("?" for _ in selected_scopes)
                db.execute(f"DELETE FROM semantic_vectors WHERE scope IN ({placeholders})",
                           tuple(sorted(selected_scopes)))
                db.executemany("DELETE FROM semantic_metadata WHERE key=?",
                               [("scope:" + scope,) for scope in selected_scopes])
            db.executemany(
                "INSERT INTO semantic_vectors(kind,id,scope,title,content_hash,content,source,start_line,end_line,vector) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(item[0], item[1], item[2], item[3], _content_hash(item[4]), "",
                  item[5], item[6], item[7], _pack_vector(vector))
                 for item, vector in zip(items, complete_vectors)],
            )
            db.execute("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES('model',?)",
                       (self.model,))
            if selected_scopes is None:
                db.execute("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES('fingerprint',?)",
                           (fingerprint,))
                db.executemany("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES(?,?)",
                               [("scope:" + scope, fingerprint)
                                for scope in sorted({item[2] for item in items})])
                db.execute("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES('storage_version',?)",
                           (_STORAGE_VERSION,))
            else:
                db.executemany("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES(?,?)",
                               [("scope:" + scope, fingerprint) for scope in sorted(selected_scopes)])
        return len(items)

    @staticmethod
    def _cached_item_matches(saved: tuple, current: tuple) -> bool:
        if saved[:4] + saved[5:] != current[:4] + current[5:]:
            return False
        return saved[4] == current[4] or saved[4] == _content_hash(current[4])

    def compact_storage(self) -> dict[str, int | str | bool]:
        """Replace duplicated text and JSON vectors in the disposable index, then reclaim pages."""
        if not self.path.is_file():
            return {"status": "no_index", "rows_compacted": 0,
                    "bytes_before": 0, "bytes_after": 0, "bytes_reclaimed": 0}
        bytes_before = self.path.stat().st_size
        rows_compacted = 0
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "semantic_vectors" not in tables:
                return {"status": "no_semantic_index", "rows_compacted": 0,
                        "bytes_before": bytes_before, "bytes_after": bytes_before,
                        "bytes_reclaimed": 0}
            columns = {row[1] for row in db.execute("PRAGMA table_info(semantic_vectors)")}
            if "content_hash" not in columns:
                db.execute("ALTER TABLE semantic_vectors ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE TABLE IF NOT EXISTS semantic_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            last_key: tuple[str, str] | None = None
            while True:
                if last_key is None:
                    rows = db.execute(
                        "SELECT kind,id,content_hash,content,vector FROM semantic_vectors "
                        "ORDER BY kind,id LIMIT 256").fetchall()
                else:
                    rows = db.execute(
                        "SELECT kind,id,content_hash,content,vector FROM semantic_vectors "
                        "WHERE kind>? OR (kind=? AND id>?) ORDER BY kind,id LIMIT 256",
                        (last_key[0], last_key[0], last_key[1])).fetchall()
                if not rows:
                    break
                updates = []
                for kind, record_id, saved_hash, content, vector in rows:
                    content_text = content.decode("utf-8") if isinstance(content, bytes) else str(content or "")
                    compact_hash = (saved_hash if _is_content_hash(saved_hash) else
                                    content_text if _is_content_hash(content_text) else
                                    _content_hash(content_text))
                    packed_vector = _pack_vector(_unpack_vector(vector))
                    if (saved_hash != compact_hash or content_text or
                            not isinstance(vector, (bytes, bytearray, memoryview)) or
                            bytes(vector) != packed_vector):
                        updates.append((compact_hash, packed_vector, kind, record_id))
                if updates:
                    db.executemany(
                        "UPDATE semantic_vectors SET content_hash=?,content='',vector=? WHERE kind=? AND id=?",
                        updates)
                    rows_compacted += len(updates)
                last_key = (rows[-1][0], rows[-1][1])
            db.execute("INSERT OR REPLACE INTO semantic_metadata(key,value) VALUES('storage_version',?)",
                       (_STORAGE_VERSION,))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        # VACUUM is deliberately explicit: it can briefly need extra disk space,
        # so only the maintenance command runs it, never a normal search.
        with sqlite3.connect(self.path, timeout=30) as vacuum_db:
            vacuum_db.execute("PRAGMA busy_timeout=30000")
            vacuum_db.execute("VACUUM")
        bytes_after = self.path.stat().st_size
        return {"status": "compacted", "rows_compacted": rows_compacted,
                "bytes_before": bytes_before, "bytes_after": bytes_after,
                "bytes_reclaimed": max(0, bytes_before - bytes_after),
                "bytes_delta": bytes_before - bytes_after}

    def _rank(self, query: str, kind: str, limit: int,
              scopes: list[str] | None,
              query_vector: list[float] | None = None) -> list[tuple[float, tuple]]:
        limit = max(0, min(int(limit), 100))
        if not query.strip() or limit == 0 or scopes == []:
            return []
        if query_vector is None:
            query_vector = self._embed([query[:1200]])[0]
        with self._connect() as db:
            # Ranking reads only metadata and vectors. Source text is loaded from
            # the lexical source_chunks table only after the top matches are known.
            sql = ("SELECT id,scope,title,source,start_line,end_line,vector "
                   "FROM semantic_vectors WHERE kind=?")
            params: list[object] = [kind]
            if scopes is not None:
                sql += f" AND scope IN ({','.join('?' for _ in scopes)})"
                params.extend(scopes)
            best: list[tuple[float, int, tuple]] = []
            for position, row in enumerate(db.execute(sql, params)):
                try:
                    vector = _unpack_vector(row[6])
                    if len(vector) != len(query_vector):
                        continue
                    score = sum(left * right for left, right in zip(query_vector, vector))
                except (TypeError, ValueError, json.JSONDecodeError, struct.error):
                    continue
                candidate = (score, -position, row[:6])
                if len(best) < limit:
                    heapq.heappush(best, candidate)
                elif candidate[:2] > best[0][:2]:
                    heapq.heapreplace(best, candidate)
        best.sort(key=lambda item: (-item[0], -item[1]))
        return [(score, row) for score, _, row in best]

    def embed_query(self, query: str) -> list[float]:
        """Create one query vector for reuse across memory and source searches."""
        if not query.strip():
            return []
        return self._embed([query[:1200]])[0]

    def search_memories(self, query: str, limit: int = 5,
                        scopes: list[str] | None = None,
                        query_vector: list[float] | None = None) -> list[tuple[float, str]]:
        return [(score, row[0]) for score, row in
                self._rank(query, "memory", limit, scopes, query_vector)]

    def search_sources(self, query: str, limit: int = 5,
                       scopes: list[str] | None = None,
                       query_vector: list[float] | None = None) -> list[tuple[float, SourceHit]]:
        ranked = self._rank(query, "source", limit, scopes, query_vector)
        if not ranked:
            return []
        ids = [row[0] for _, row in ranked]
        with self._connect() as db:
            contents: dict[str, str] = {}
            try:
                contents.update(db.execute(
                    f"SELECT id,content FROM source_chunks WHERE id IN ({','.join('?' for _ in ids)})",
                    ids,
                ).fetchall())
            except sqlite3.OperationalError:
                # Keep old standalone indexes readable until their lexical table is rebuilt.
                pass
            missing_ids = [item for item in ids if item not in contents]
            if missing_ids:
                rows = db.execute(
                    f"SELECT id,content_hash,content FROM semantic_vectors "
                    f"WHERE kind='source' AND id IN ({','.join('?' for _ in missing_ids)})",
                    missing_ids,
                ).fetchall()
                contents.update((item_id, content) for item_id, content_hash, content in rows
                                if content and not _is_content_hash(content))
        return [(score, SourceHit(id=row[0], scope=row[1], heading=row[2],
                                  content=contents.get(row[0], ""), path=row[3],
                                  start_line=row[4] or 1, end_line=row[5] or 1))
                for score, row in ranked]

    def chunk_count(self) -> int:
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                return int(db.execute("SELECT count(*) FROM semantic_vectors").fetchone()[0])
        except sqlite3.Error:
            return 0
