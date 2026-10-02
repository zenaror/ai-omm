"""Read-only timing snapshot for an already-running OMM installation."""
from __future__ import annotations

import statistics
from time import perf_counter

from .service import OMM
from .web_server import dashboard_data


def _measure(action, repetitions: int) -> dict[str, float]:
    action()  # warm caches before recording steady-state timings
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
        "Busca semântica não é chamada; Ollama e embeddings não são medidos.",
    ]
    result = {
        "read_only": True,
        "canonical_data_modified_by_report": False,
        "records": len(records),
        "source_chunks": source_chunks,
        "index_bytes": index_bytes,
        "index_current": index_current,
        "semantic_search_enabled": semantic_enabled,
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
    return result
