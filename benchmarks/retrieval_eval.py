"""Small offline retrieval check using generic synthetic OMM data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omm.models import MemoryRecord
from omm.service import OMM


TOP_K = 5
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

MEMORIES = [
    ("Proteção do acesso MCP", "Use um token bearer para proteger o acesso remoto à interface MCP.", "api-access"),
    ("Git é a memória principal", "SQLite é um índice derivado e pode ser recriado a partir dos arquivos Git.", "git-source"),
    ("Contexto com limite", "Um limite de caracteres reduz o texto enviado ao assistente e ajuda a economizar tokens.", "context-budget"),
    ("Backup agendado", "Uma rotina pode salvar versões no repositório Git local e opcionalmente enviar ao remoto.", "git-backup"),
    ("Tempo das consultas", "O relatório mostra a duração das operações locais sem incluir o texto das memórias.", "mcp-latency"),
    ("Papéis de agentes", "A topologia descreve coordenadores e subagentes; quem os inicia é o assistente hospedeiro.", "agent-topology"),
    ("Importação de conversas", "Uma transcrição pode ser guardada como fonte pesquisável antes de revisar suas anotações.", "chat-import"),
    ("Revisão de sugestões", "A pessoa aprova ou recusa uma proposta antes de ela virar memória ativa.", "memory-review"),
    ("Instalação com Podman", "O container OCI pode ser iniciado com Podman Compose e uma pasta de dados persistente.", "podman-install"),
    ("Passagem de trabalho", "Um handoff registra o estado atual, bloqueios e próximos passos de uma tarefa.", "handoff"),
    ("Cuidados com segredos", "Senhas e chaves privadas não devem ser copiadas para as memórias ou para o Git.", "secret-handling"),
    ("Modelo de embeddings", "O Ollama transforma o texto em vetores numéricos para comparar assuntos parecidos.", "embedding-model"),
]

SOURCES = {
    "search.md": (
        "# Busca semântica opcional\n"
        "Embeddings do Ollama ajudam a localizar ideias descritas com palavras diferentes, mas com sentido próximo.\n"
    ),
    "review.md": (
        "# Aprovação humana\n"
        "A pessoa revisa uma sugestão de memória antes de ela ser aceita e usada como conhecimento ativo.\n"
    ),
    "backup.md": (
        "# Backup dos arquivos\n"
        "A sincronização Git guarda arquivos canônicos e pode enviar a branch ao servidor remoto.\n"
    ),
    "agents.md": (
        "# Agentes e papéis\n"
        "O host consulta a topologia e seleciona os papéis adequados para a tarefa atual.\n"
    ),
    "privacy.md": (
        "# Dados privados\n"
        "Evite registrar credenciais; revise os documentos antes de compartilhá-los com serviços externos.\n"
    ),
    "install.md": (
        "# Instalação local\n"
        "Docker e Podman executam a aplicação com arquivos persistentes em um volume de dados.\n"
    ),
}


def reciprocal_rank(ids: list[str], expected: str) -> float:
    try:
        return 1.0 / (ids.index(expected) + 1)
    except ValueError:
        return 0.0


def unit_interval(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use um número entre 0 e 1") from exc
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("use um número entre 0 e 1")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emitir o resultado em JSON")
    parser.add_argument("--min-hit-rate-at-3", type=unit_interval,
                        help="limite de 0 a 1 para alvos no top 3; 0.8 significa 80%%")
    parser.add_argument("--min-mrr-at-5", type=unit_interval,
                        help="limite de 0 a 1 para a posição média dos alvos no top 5")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="omm-retrieval-eval-") as temporary:
        root = Path(temporary)
        omm = OMM(root)
        omm.init()
        for title, content, identity in MEMORIES:
            omm.remember(MemoryRecord(id=identity, kind="fact", scope="demo", title=title,
                                      content=content, source="dataset sintético"))
        source_dir = root / "sources" / "demo"
        source_dir.mkdir(parents=True)
        for name, content in SOURCES.items():
            (source_dir / name).write_text(content, encoding="utf-8")
        omm.rebuild()
        hits_at_1, hits_at_3, hits_at_5, ranks, per_case = [], [], [], [], []
        buckets: dict[str, list[float]] = {"palavras": [], "reformulada": []}
        for category, kind, query, expected in CASES:
            if kind == "memory":
                ids = [item.id for item in omm.search(query, TOP_K, scopes=["demo"])]
            else:
                ids = [item.path.rsplit("/", 1)[-1] for item in omm.search_sources(query, TOP_K, scopes=["demo"])]
            rank = reciprocal_rank(ids, expected)
            hits_at_1.append(int(bool(ids) and ids[0] == expected))
            hits_at_3.append(int(expected in ids[:3]))
            hits_at_5.append(int(expected in ids[:5]))
            ranks.append(rank)
            buckets[category].append(rank)
            per_case.append({"kind": kind, "query": query, "expected_id": expected,
                             "query_type": category,
                             "rank": ids.index(expected) + 1 if expected in ids else None,
                             "found_at_3": expected in ids[:3],
                             "reciprocal_rank_at_5": round(rank, 4),
                             "top_ids": ids[:3]})
        context = omm.context("MCP token", limit=3, scopes=["demo"], include_sources=False)
        hit_rate_at_3 = sum(hits_at_3) / len(hits_at_3)
        mrr_at_5 = sum(ranks) / len(ranks)
        minimums = {
            "hit_rate_at_3": args.min_hit_rate_at_3,
            "mrr_at_5": args.min_mrr_at_5,
        }
        checks = []
        if args.min_hit_rate_at_3 is not None:
            checks.append(hit_rate_at_3 >= args.min_hit_rate_at_3)
        if args.min_mrr_at_5 is not None:
            checks.append(mrr_at_5 >= args.min_mrr_at_5)
        passed = all(checks) if checks else None
        result = {
            "dataset": "synthetic generic examples with distractors; no network or user data",
            "retrieval_type": "lexical; this evaluation does not call Ollama",
            "cases": len(CASES),
            "candidate_counts": {"memories": len(MEMORIES), "source_documents": len(SOURCES)},
            "hit_rate_at_1": round(sum(hits_at_1) / len(hits_at_1), 4),
            "hit_rate_at_3": round(hit_rate_at_3, 4),
            "hit_rate_at_5": round(sum(hits_at_5) / len(hits_at_5), 4),
            "mean_reciprocal_rank_at_5": round(mrr_at_5, 4),
            "mrr_at_5_by_query_type": {name: round(sum(values) / len(values), 4)
                                        for name, values in buckets.items()},
            "minimums": minimums,
            "passed_minimums": passed,
            "context_chars": len(context),
            "context_tokens_approx": (len(context) + 3) // 4,
            "results": per_case,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Avaliação sintética da busca lexical (sem Ollama e sem dados pessoais).")
            print(f"Alvos entre os 3 primeiros: {sum(hits_at_3)}/{len(hits_at_3)} consultas ({hit_rate_at_3:.0%}).")
            print(f"Posição média dos alvos (MRR@5): {mrr_at_5:.2f}.")
            if passed is not None:
                print(f"Limites configurados: {'aprovados' if passed else 'não atingidos'}.")
    return 1 if passed is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
