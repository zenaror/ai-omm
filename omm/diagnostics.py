"""Read-only checks that explain whether an OMM installation is ready to use."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

from .service import OMM


def diagnose(omm: OMM) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    def git_output(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", "-C", str(omm.root), *args],
                                    capture_output=True, text=True, check=False, timeout=2)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    with omm.operation_lock():
        try:
            records = list(omm.store.records())
            add("memories", "ok", f"{len(records)} anotações legíveis.")
        except (OSError, ValueError) as exc:
            records = []
            add("memories", "error", f"Não consegui ler as anotações: {exc}")

        jsonl_paths = (omm.store.policies_path, omm.store.handoffs_path,
                       omm.store.workstreams_path, omm.store.proposals_path)
        try:
            for path in jsonl_paths:
                if path.exists():
                    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if line.strip():
                            json.loads(line)
            add("support_files", "ok", "Regras, passagens e frentes de trabalho estão legíveis.")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            add("support_files", "error", f"Um arquivo auxiliar precisa ser revisado: {exc}")

        state_path = omm.store.state_path
        try:
            if state_path.exists():
                json.loads(state_path.read_text(encoding="utf-8"))
            add("state", "ok", "O estado atual do trabalho está legível.")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            add("state", "error", f"O arquivo de estado precisa ser revisado: {exc}")

        source_root = omm.root / "sources"
        source_count = sum(1 for path in source_root.rglob("*.md")
                           if path.is_file() and not path.is_symlink()) if source_root.is_dir() else 0
        add("sources", "ok", f"{source_count} documentos Markdown disponíveis para busca.")

        try:
            index_ready = (omm.retriever.has_expected_schema() and
                           omm.retriever.indexed_fingerprint() == omm._canonical_fingerprint())
            if index_ready:
                add("search_index", "ok", "A busca está atualizada.")
            else:
                add("search_index", "warning", "A busca precisa ser reconstruída; as anotações continuam guardadas.")
        except (OSError, RuntimeError, sqlite3.Error):
            add("search_index", "warning", "A busca não está disponível; ela pode ser reconstruída dos arquivos guardados.")

        if git_output("rev-parse", "--is-inside-work-tree") != "true":
            add("git_backup", "info", "Esta pasta não é um repositório Git. Backup Git não foi configurado aqui.")
        else:
            branch = git_output("branch", "--show-current") or ""
            dirty = git_output("status", "--porcelain", "--", "memory", "skills", "sources") or ""
            if dirty:
                add("git_backup", "warning", f"Há mudanças de dados ainda sem commit na branch '{branch or 'sem nome'}'.")
            else:
                add("git_backup", "ok", f"Backup Git local encontrado na branch '{branch or 'sem nome'}'.")

        bind = os.getenv("OMM_BIND_ADDRESS", "127.0.0.1").strip().lower()
        token = os.getenv("OMM_MCP_TOKEN", "")
        if bind in {"127.0.0.1", "localhost", "::1"}:
            add("mcp_access", "ok", "O MCP está limitado a este computador.")
        elif len(token) >= 32:
            add("mcp_access", "ok", "O MCP aceita conexões de rede e exige um token.")
        else:
            add("mcp_access", "warning", "O MCP pode ser acessado pela rede sem um token longo.")

    return checks
