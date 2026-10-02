"""Offline timing check for OMM using generated, generic memory and source data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omm.models import MemoryRecord
from omm.service import OMM
from omm.web_server import dashboard_data


def summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "median_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[p95_index] * 1000, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1000,
                        help="quantidade de anotações inventadas (padrão: 1000)")
    parser.add_argument("--sources", type=int, default=200,
                        help="quantidade de documentos inventados (padrão: 200)")
    parser.add_argument("--repetitions", type=int, default=30,
                        help="medições por operação, depois do aquecimento (padrão: 30)")
    args = parser.parse_args()
    if args.records < 1 or args.sources < 0 or args.repetitions < 2:
        parser.error("use ao menos 1 anotação, 0 documentos e 2 repetições")

    with tempfile.TemporaryDirectory(prefix="omm-performance-") as temporary:
        root = Path(temporary)
        omm = OMM(root)
        omm.init()

        records_path = omm.store.records_path
        with records_path.open("w", encoding="utf-8") as stream:
            for number in range(args.records):
                record = MemoryRecord(
                    id=f"synthetic-{number:08d}", kind="fact", scope="benchmark",
                    title=f"Protocolo de exemplo {number}",
                    content=("Dados inventados para medir busca local, proveniência e contexto. "
                             f"Grupo {number % 50}; assunto genérico; sem dados de usuário."),
                    source="dataset sintético",
                )
                stream.write(record.to_json() + "\n")

        source_root = root / "sources" / "benchmark"
        source_root.mkdir(parents=True)
        for number in range(args.sources):
            (source_root / f"document-{number:06d}.md").write_text(
                f"# Documento inventado {number}\n\n"
                f"Grupo {number % 50}; conteúdo genérico para medir indexação de fontes.\n",
                encoding="utf-8",
            )

        started = perf_counter()
        indexed_records = omm.rebuild()
        index_seconds = perf_counter() - started
        repetitions = args.repetitions
        query = "grupo protocolo exemplo"

        def measure(action) -> list[float]:
            action()  # aquece SQLite e caches
            results = []
            for _ in range(repetitions):
                started = perf_counter()
                action()
                results.append(perf_counter() - started)
            return results

        search_times = measure(lambda: omm.search(query, 10, scopes=["benchmark"]))
        context = omm.context(query, limit=10, scopes=["benchmark"], include_sources=False)
        context_times = measure(lambda: omm.context(query, limit=10, scopes=["benchmark"],
                                                    include_sources=False))
        dashboard_times = measure(lambda: dashboard_data(omm, scope="benchmark"))

        result = {
            "dataset": "inventado, temporário e removido ao terminar; sem rede ou dados de usuário",
            "search_mode": "textual SQLite/FTS; não mede embeddings nem Ollama",
            "records": indexed_records,
            "source_documents": args.sources,
            "repetitions": repetitions,
            "index_build_seconds": round(index_seconds, 3),
            "search": summary(search_times),
            "context": {**summary(context_times), "characters": len(context),
                        "estimated_tokens": (len(context) + 3) // 4},
            "dashboard": summary(dashboard_times),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
