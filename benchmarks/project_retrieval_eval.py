"""Evaluate private project questions against expected IDs, without printing text."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omm.service import OMM


def validate_cases(cases):
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Forneça de 1 a 200 perguntas no arquivo JSON.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Cada pergunta precisa ser um objeto JSON.")
        identity = case.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or identity in seen:
            raise ValueError("Use IDs únicos, simples e com até 80 caracteres.")
        seen.add(identity)
        if case.get("kind") not in {"memory", "source"}:
            raise ValueError("kind deve ser memory ou source.")
        if not isinstance(case.get("query"), str) or not 1 <= len(case["query"].strip()) <= 1200:
            raise ValueError("A pergunta precisa ter de 1 a 1200 caracteres.")
        if not isinstance(case.get("scope"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", case["scope"]):
            raise ValueError("Informe um escopo simples em cada pergunta.")
        if not isinstance(case.get("expected_empty", False), bool):
            raise ValueError("expected_empty deve ser booleano.")
        for key in ("expected_ids", "forbidden_ids"):
            values = case.get(key, [])
            if not isinstance(values, list) or len(values) > 20 or any(not isinstance(v, str) or not v or len(v) > 500 for v in values):
                raise ValueError("Use listas de até 20 IDs ou caminhos esperados/proibidos.")
        if bool(case.get("expected_ids")) == case.get("expected_empty", False):
            raise ValueError("Informe expected_ids ou expected_empty=true, nunca ambos.")


def evaluate(omm, cases, top_k=5, min_recall=1.0):
    validate_cases(cases)
    if not 1 <= top_k <= 20 or not 0 <= min_recall <= 1:
        raise ValueError("Use top-k entre 1 e 20 e min-recall entre 0 e 1.")
    results = []
    recalls = []
    for case in cases:
        started = perf_counter()
        if case["kind"] == "memory":
            ids = [r.id for r in omm.search(case["query"], top_k, scopes=[case["scope"]])]
        else:
            ids = list(dict.fromkeys(r.path for r in omm.search_sources(case["query"], top_k, scopes=[case["scope"]])))
        elapsed = (perf_counter() - started) * 1000
        expected = set(case.get("expected_ids", []))
        recall = len(expected.intersection(ids)) / len(expected) if expected else None
        if recall is not None:
            recalls.append(recall)
        ranks = [ids.index(i) + 1 for i in expected if i in ids]
        forbidden = bool(set(case.get("forbidden_ids", [])).intersection(ids))
        passed = (not ids if case.get("expected_empty") else recall >= min_recall) and not forbidden
        results.append({"id": case["id"], "recall_at_k": recall,
                        "reciprocal_rank": 1 / min(ranks) if ranks else 0,
                        "forbidden_hit": forbidden, "returned_count": len(ids),
                        "elapsed_ms": round(elapsed, 2), "passed": passed})
    return {"measurement": "Busca lexical; não avalia resposta do modelo, tokens ou economia. IDs de casos e métricas, sem perguntas/documentos.",
            "top_k": top_k, "min_recall": min_recall, "cases": len(results),
            "mean_recall_at_k": sum(recalls) / len(recalls) if recalls else None,
            "passed": all(r["passed"] for r in results), "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=1.0)
    args = parser.parse_args()
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        validate_cases(cases)
        if not (args.root / "memory/records.jsonl").is_file():
            raise ValueError("A pasta indicada não contém uma OMM existente.")
        result = evaluate(OMM(args.root), cases, args.top_k, args.min_recall)
    except (ValueError, OSError):
        parser.exit(2, "Não foi possível avaliar. Confira a pasta OMM, o JSON e os limites documentados.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
