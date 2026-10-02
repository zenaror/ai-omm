"""Small, offline retrieval check using generic synthetic OMM data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omm.models import MemoryRecord
from omm.service import OMM


CASES = [
    ("palavras", "memory", "token MCP remoto", "api-access"),
    ("palavras", "memory", "SQLite índice recriar arquivos", "git-source"),
    ("palavras", "memory", "limite caracteres tokens contexto", "context-budget"),
    ("palavras", "memory", "backup agendado Git remoto", "git-backup"),
    ("palavras", "source", "embeddings Ollama significado", "search.md"),
    ("palavras", "source", "revisa sugestão memória", "review.md"),
    ("reformulada", "memory", "proteger conversa remotamente", "api-access"),
    ("reformulada", "source", "achar ideias pelo sentido", "search.md"),
]


def reciprocal_rank(ids: list[str], expected: str) -> float:
    try:
        return 1.0 / (ids.index(expected) + 1)
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emitir o resultado em JSON")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="omm-retrieval-eval-") as temporary:
        root = Path(temporary)
        omm = OMM(root)
        omm.init()
        for scope, title, content, identity in [
            ("demo", "Proteção do acesso MCP", "Um token bearer protege chamadas remotas à interface MCP.", "api-access"),
            ("demo", "Git é a memória principal", "SQLite é um índice derivado e deve poder ser recriado dos arquivos Git.", "git-source"),
            ("demo", "Contexto com limite", "Um limite de caracteres ajuda a reduzir tokens enviados ao assistente.", "context-budget"),
            ("demo", "Backup agendado", "Uma rotina pode salvar versões no repositório Git local e opcionalmente enviar ao remoto.", "git-backup"),
        ]:
            omm.remember(MemoryRecord(id=identity, kind="fact", scope=scope, title=title,
                                      content=content, source="dataset sintético"))
        source_dir = root / "sources" / "demo"
        source_dir.mkdir(parents=True)
        (source_dir / "search.md").write_text(
            "# Busca semântica opcional\nEmbeddings do Ollama ajudam a localizar ideias descritas com palavras diferentes e significado próximo.\n",
            encoding="utf-8")
        (source_dir / "review.md").write_text(
            "# Aprovação humana\nA pessoa revisa uma sugestão de memória antes de ela ser aceita.\n",
            encoding="utf-8")
        omm.rebuild()
        hits, ranks, per_case = [], [], []
        buckets: dict[str, list[float]] = {"palavras": [], "reformulada": []}
        for category, kind, query, expected in CASES:
            if kind == "memory":
                ids = [item.id for item in omm.search(query, 5, scopes=["demo"])]
            else:
                ids = [item.path.rsplit("/", 1)[-1] for item in omm.search_sources(query, 5, scopes=["demo"])]
            rank = reciprocal_rank(ids, expected)
            hits.append(1 if rank else 0)
            ranks.append(rank)
            buckets[category].append(rank)
            per_case.append({"kind": kind, "query": query, "expected_id": expected,
                             "query_type": category,
                             "found": bool(rank), "reciprocal_rank": round(rank, 4),
                             "top_ids": ids[:3]})
        context = omm.context("MCP token", limit=3, scopes=["demo"], include_sources=False)
        result = {
            "dataset": "synthetic generic examples; no network or user data",
            "cases": len(CASES),
            "recall_at_5": round(sum(hits) / len(hits), 4),
            "mean_reciprocal_rank_at_5": round(sum(ranks) / len(ranks), 4),
            "recall_at_5_by_query_type": {name: round(sum(values) / len(values), 4)
                                           for name, values in buckets.items()},
            "context_chars": len(context),
            "context_tokens_approx": (len(context) + 3) // 4,
            "results": per_case,
        }
        print(json.dumps(result if args.json else result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
