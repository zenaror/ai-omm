"""Optional semantic index backed by a local Ollama embedding endpoint."""
from __future__ import annotations

import json
import heapq
import math
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import MemoryRecord
from .source_documents import SourceChunk, SourceHit


class SemanticSearchError(RuntimeError):
    pass


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
                   "content TEXT NOT NULL, source TEXT NOT NULL, start_line INTEGER, end_line INTEGER, "
                   "vector TEXT NOT NULL, PRIMARY KEY(kind,id))")
        db.execute("CREATE TABLE IF NOT EXISTS semantic_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return db

    def indexed_fingerprint(self) -> str | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                row = db.execute("SELECT value FROM semantic_metadata WHERE key='fingerprint'").fetchone()
                model = db.execute("SELECT value FROM semantic_metadata WHERE key='model'").fetchone()
            return row[0] if row and model and model[0] == self.model else None
        except sqlite3.Error:
            return None

    def rebuild(self, records: list[MemoryRecord], chunks: list[SourceChunk], fingerprint: str) -> int:
        items: list[tuple[str, str, str, str, str, str, int | None, int | None]] = []
        items.extend(("memory", record.id, record.scope, record.title,
                      record.title + "\n" + record.content + "\n" + " ".join(record.tags),
                      record.source, None, None)
                     for record in records if record.status == "active")
        items.extend(("source", chunk.id, chunk.scope, chunk.heading,
                      chunk.heading + "\n" + chunk.content, chunk.path,
                      chunk.start_line, chunk.end_line) for chunk in chunks)
        # Reuse unchanged vectors when only one memory or source has changed.
        # This keeps ordinary updates from sending the whole collection again.
        cached: dict[tuple[str, str], tuple[tuple, list[float]]] = {}
        if self.path.is_file():
            try:
                with sqlite3.connect(self.path, timeout=5) as db:
                    model_row = db.execute("SELECT value FROM semantic_metadata WHERE key='model'").fetchone()
                    if model_row and model_row[0] == self.model:
                        for row in db.execute(
                                "SELECT kind,id,scope,title,content,source,start_line,end_line,vector FROM semantic_vectors"):
                            try:
                                cached[(row[0], row[1])] = (tuple(row[:8]), json.loads(row[8]))
                            except (TypeError, ValueError, json.JSONDecodeError):
                                continue
            except sqlite3.Error:
                pass
        vectors: list[list[float] | None] = [None] * len(items)
        pending: list[tuple[int, tuple]] = []
        for position, item in enumerate(items):
            previous = cached.get((item[0], item[1]))
            if previous and previous[0] == item:
                vectors[position] = previous[1]
            else:
                pending.append((position, item))
        for offset in range(0, len(pending), 32):
            batch = pending[offset:offset + 32]
            embedded = self._embed([item[4] for _, item in batch])
            for (position, _), vector in zip(batch, embedded):
                vectors[position] = vector
        if any(vector is None for vector in vectors):
            raise SemanticSearchError("O índice semântico ficou incompleto; tente reconstruí-lo novamente.")
        complete_vectors = [vector for vector in vectors if vector is not None]
        with self._connect() as db:
            db.execute("DELETE FROM semantic_vectors")
            db.execute("DELETE FROM semantic_metadata")
            db.executemany(
                "INSERT INTO semantic_vectors(kind,id,scope,title,content,source,start_line,end_line,vector) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [(*item, json.dumps(vector, separators=(",", ":")))
                 for item, vector in zip(items, complete_vectors)],
            )
            db.executemany("INSERT INTO semantic_metadata(key,value) VALUES(?,?)",
                           [("model", self.model), ("fingerprint", fingerprint)])
        return len(items)

    def _rank(self, query: str, kind: str, limit: int,
              scopes: list[str] | None,
              query_vector: list[float] | None = None) -> list[tuple[float, tuple]]:
        limit = max(0, min(int(limit), 100))
        if not query.strip() or limit == 0 or scopes == []:
            return []
        if query_vector is None:
            query_vector = self._embed([query[:1200]])[0]
        with self._connect() as db:
            # Content is deliberately fetched only for final source hits. Loading
            # every chunk's text for ranking wastes memory on large collections.
            sql = ("SELECT id,scope,title,source,start_line,end_line,vector "
                   "FROM semantic_vectors WHERE kind=?")
            params: list[object] = [kind]
            if scopes is not None:
                sql += f" AND scope IN ({','.join('?' for _ in scopes)})"
                params.extend(scopes)
            best: list[tuple[float, int, tuple]] = []
            for position, row in enumerate(db.execute(sql, params)):
                try:
                    vector = json.loads(row[6])
                    if len(vector) != len(query_vector):
                        continue
                    score = sum(left * float(right) for left, right in zip(query_vector, vector))
                except (TypeError, ValueError, json.JSONDecodeError):
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
            contents = dict(db.execute(
                f"SELECT id,content FROM semantic_vectors WHERE kind='source' AND id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall())
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
