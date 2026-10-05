"""Read-only timing snapshot for an already-running OMM installation."""
from __future__ import annotations

from collections import Counter
import math
import sqlite3
import statistics
from time import perf_counter

from .service import OMM
from .semantic import SemanticSearchError
from .web_server import dashboard_data


def _measure(action, repetitions: int, warmup: bool = True) -> dict[str, object]:
    if warmup:
        action()  # warm local caches before recording steady-state timings
    samples = []
    for _ in range(repetitions):
        started = perf_counter()
        action()
        samples.append(perf_counter() - started)
    ordered = sorted(samples)
    p95_rank = max(1, math.ceil(len(ordered) * 0.95))
    return {"sample_count": len(ordered), "warmup_runs": int(warmup),
            "p95_method": "nearest-rank",
            "min_ms": round(ordered[0] * 1000, 2),
            "median_ms": round(statistics.median(ordered) * 1000, 2),
            "p95_ms": round(ordered[p95_rank - 1] * 1000, 2),
            "max_ms": round(ordered[-1] * 1000, 2)}


def measure_performance(omm: OMM, repetitions: int = 30) -> dict:
    """Measure common local operations without returning memory text or changing canonical files."""
    repetitions = max(2, min(int(repetitions), 100))
    with omm.operation_lock():
        all_records = omm.records()
        records = [record for record in all_records if record.status == "active"]
        records_by_status = dict(sorted(Counter(record.status for record in all_records).items()))
        query = " ".join(records[0].title.split()[:8]) if records else "omm memory search"
        scopes = [records[0].scope] if records else None
        try:
            index_current = (omm.retriever.has_expected_schema() and
                             omm.retriever.indexed_fingerprint() == omm._canonical_fingerprint())
        except (OSError, RuntimeError):
            index_current = False
        index_path = omm.root / ".omm" / "index.sqlite3"
        index_bytes = index_path.stat().st_size if index_path.is_file() else 0
        index_sidecar_bytes = sum(
            candidate.stat().st_size for candidate in (
                index_path.with_name(index_path.name + "-wal"),
                index_path.with_name(index_path.name + "-shm"),
            ) if candidate.is_file()
        )
        index_pages = {"page_size_bytes": 0, "page_count": 0, "free_pages": 0,
                       "free_bytes_estimate": 0}
        if index_path.is_file():
            try:
                uri = index_path.resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=1) as db:
                    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
                    page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
                    free_pages = int(db.execute("PRAGMA freelist_count").fetchone()[0])
                index_pages = {"page_size_bytes": page_size, "page_count": page_count,
                               "free_pages": free_pages,
                               "free_bytes_estimate": page_size * free_pages}
            except (OSError, sqlite3.Error, TypeError, ValueError):
                pass
        semantic_enabled = omm.semantic is not None
        source_chunks = omm.source_chunk_count()
    measurements = {"dashboard": _measure(lambda: dashboard_data(omm), repetitions)}
    notes = [
        "Medições locais dentro do servidor; não incluem a ida e volta MCP/rede. O conteúdo das memórias não é incluído.",
    ]
    if repetitions < 20:
        notes.append("Com menos de 20 amostras, p95 é uma estimativa grosseira; aumente repetitions para comparar caudas.")
    semantic_current = False
    semantic_entries = 0
    if omm.semantic is not None:
        try:
            semantic_current = (index_current and bool(scopes) and
                                all(omm.semantic.indexed_fingerprint(scope) == omm._canonical_fingerprint()
                                    for scope in scopes))
            semantic_entries = omm.semantic.chunk_count()
        except (OSError, RuntimeError):
            semantic_current = False
        if not semantic_current:
            notes.append("Busca semântica não foi medida porque o índice semântico precisa ser reconstruído.")
    result = {
        "read_only": True,
        "canonical_data_modified_by_report": False,
        "total_records": len(all_records),
        "records": len(records),
        "records_by_status": records_by_status,
        "source_chunks": source_chunks,
        "index_bytes": index_bytes,
        "index_sidecar_bytes": index_sidecar_bytes,
        "index_pages": index_pages,
        "index_current": index_current,
        "semantic_search_enabled": semantic_enabled,
        "semantic_index_current": semantic_current,
        "semantic_index_entries": semantic_entries,
        "repetitions": repetitions,
        "measurements": measurements,
        "notes": notes,
    }
    if index_current:
        measurements["search"] = _measure(lambda: omm.search(query, 5, scopes=scopes), repetitions)
        source_query = "project protocol notes"
        measurements["source_search"] = _measure(
            lambda: omm.search_sources(source_query, 5, scopes=scopes), repetitions)
        context = omm.context(query, limit=5, scopes=scopes,
                              state_scope=scopes[0] if scopes else "global",
                              include_sources=False, budget_chars=2500)
        measurements["context"] = _measure(
            lambda: omm.context(query, limit=5, scopes=scopes,
                                state_scope=scopes[0] if scopes else "global",
                                include_sources=False, budget_chars=2500), repetitions)
        result["context"] = {"characters": len(context),
                             "estimated_tokens": (len(context) + 3) // 4}
    else:
        notes.append("Busca e contexto não foram medidos porque o índice precisa ser recriado.")
    if semantic_current and omm.semantic is not None:
        semantic_repetitions = min(3, repetitions)
        # A fixed generic query avoids sending a real memory title to the configured
        # embedding service. It measures the combined memory + source path.
        semantic_query = "shared project protocol example"
        notes.append("A busca semântica usa apenas uma pergunta genérica e chama o serviço de embeddings configurado.")
        notes.append("A medição semântica usa no máximo três amostras para limitar chamadas ao Ollama; seu p95 é apenas indicativo.")
        try:
            measurements["semantic_search"] = _measure(
                lambda: omm.semantic_search(semantic_query, "all", 5, scopes),
                semantic_repetitions, warmup=False)
            result["semantic_repetitions"] = semantic_repetitions
        except (SemanticSearchError, OSError, RuntimeError, sqlite3.Error) as exc:
            measurements["semantic_search"] = {"error": type(exc).__name__}
            notes.append("A busca semântica está indexada, mas o teste de tempo falhou ao acessar o serviço local.")
    return result
