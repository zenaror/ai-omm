"""Read-only timing snapshot for an already-running OMM installation."""
from __future__ import annotations

import statistics
import sqlite3
from time import perf_counter

from .service import OMM
from .semantic import SemanticSearchError
from .web_server import dashboard_data


def _measure(action, repetitions: int, warmup: bool = True) -> dict[str, float]:
    if warmup:
        action()  # warm local caches before recording steady-state timings
    samples = []
    for _ in range(repetitions):
        started = perf_counter()
        action()
        samples.append(perf_counter() - started)
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {"median_ms": round(statistics.median(ordered) * 1000, 2),
            "p95_ms": round(p95 * 1000, 2)}


def measure_performance(omm: OMM, repetitions: int = 5) -> dict:
    """Measure common local operations without returning memory text or changing canonical files."""
    repetitions = max(2, min(int(repetitions), 10))
    with omm.operation_lock():
        records = [record for record in omm.records() if record.status == "active"]
        query = " ".join(records[0].title.split()[:8]) if records else "omm memory search"
        scopes = [records[0].scope] if records else None
        try:
            index_current = (omm.retriever.has_expected_schema() and
                             omm.retriever.indexed_fingerprint() == omm._canonical_fingerprint())
        except (OSError, RuntimeError):
            index_current = False
        index_path = omm.root / ".omm" / "index.sqlite3"
        index_bytes = index_path.stat().st_size if index_path.is_file() else 0
        semantic_enabled = omm.semantic is not None
        source_chunks = omm.source_chunk_count()
    measurements = {"dashboard": _measure(lambda: dashboard_data(omm), repetitions)}
    notes = [
        "Medições locais; conteúdo das memórias não é incluído no relatório.",
    ]
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
        "records": len(records),
        "source_chunks": source_chunks,
        "index_bytes": index_bytes,
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
        try:
            measurements["semantic_search"] = _measure(
                lambda: omm.semantic_search(semantic_query, "all", 5, scopes),
                semantic_repetitions, warmup=False)
            result["semantic_repetitions"] = semantic_repetitions
        except (SemanticSearchError, OSError, RuntimeError, sqlite3.Error) as exc:
            measurements["semantic_search"] = {"error": type(exc).__name__}
            notes.append("A busca semântica está indexada, mas o teste de tempo falhou ao acessar o serviço local.")
    return result
