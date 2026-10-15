"""Checkpoint provenance and explicit revision comparison, without guessing age."""
from __future__ import annotations

import re

from .errors import OMMError


def validate_revision(value: str | None) -> None:
    if value is not None and not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", value):
        raise OMMError("INVALID_ARGUMENT", "A revisão Git precisa ser um hash completo de 40 ou 64 caracteres.",
                       "Leia o HEAD com git rev-parse HEAD; não use um hash abreviado.")


def validate_repository(value: str | None) -> None:
    if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value):
        raise OMMError("INVALID_ARGUMENT", "Informe um identificador simples do repositório, sem URL ou credenciais.",
                       "Use, por exemplo, equipe/projeto; preserve o mesmo identificador na comparação.")


def checkpoint_metadata(state: dict, observed_revision: str | None = None,
                        observed_repository: str | None = None) -> dict:
    validate_revision(observed_revision)
    validate_repository(observed_repository)
    repository = state.get("repository")
    revision = state.get("git_revision")
    status = "not_verified"
    reason = "A revisão do checkout não foi comparada; a idade do registro não comprova obsolescência."
    if not state.get("updated_at"):
        status, reason = "no_checkpoint", "Não há checkpoint registrado para esta seleção."
    elif observed_revision is not None:
        if not repository or not revision:
            reason = "O checkpoint antigo não informa repositório e revisão em campos próprios."
        elif observed_repository != repository:
            reason = "O repositório observado não foi identificado como o mesmo do checkpoint."
        else:
            status = "matches" if str(revision).lower() == observed_revision.lower() else "diverged"
            reason = ("A revisão informada coincide com a registrada; isso não verifica a árvore local nem os testes."
                      if status == "matches" else
                      "A revisão informada difere da registrada; confira o trabalho antes de mesclar, sem sobrescrever automaticamente.")
    return {
        "provenance": {"origin": "canonical", "source": "memory/",
                       "updated_at": state.get("updated_at"), "updated_by": state.get("updated_by"),
                       "repository": repository, "branch": state.get("branch"), "git_revision": revision},
        "freshness": {"status": status, "message": reason,
                      "comparison_basis": "caller_supplied" if observed_revision is not None else "not_observed",
                      "remote_backup": "not_verified"},
    }
