"""Scheduled Git backup for OMM's canonical project data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo
from .locking import data_lock

CANONICAL_PATHS = ("memory", "skills", "sources")
MAX_SOURCE_PDF_BYTES = 100 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=False, env=env,
    )
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "falha no Git"
        raise BackupError(message)
    return result.stdout.strip()


def _validate_memory(root: Path) -> None:
    memory = root / "memory"
    if not memory.exists():
        return
    for path in memory.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".jsonl":
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip():
                        json.loads(line)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"memória inválida em {path.relative_to(root)}: {exc}") from exc


def _validate_sources(root: Path) -> None:
    sources = root / "sources"
    if not sources.exists():
        return
    for path in sources.rglob("*"):
        if path.is_symlink():
            raise BackupError(f"documentos-fonte não podem apontar para fora da pasta de dados: {path.relative_to(root)}")
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".md":
                path.read_text(encoding="utf-8")
            elif suffix == ".pdf":
                if path.stat().st_size > MAX_SOURCE_PDF_BYTES:
                    raise BackupError(f"PDF-fonte excede 100 MB: {path.relative_to(root)}")
                with path.open("rb") as source:
                    header = source.read(1024)
                if b"%PDF-" not in header:
                    raise BackupError(f"PDF-fonte inválido em {path.relative_to(root)}")
            else:
                raise BackupError(
                    f"formato ainda não aceito em sources/: {path.relative_to(root)} (use Markdown ou PDF)"
                )
        except (OSError, UnicodeError) as exc:
            raise BackupError(f"documento-fonte inválido em {path.relative_to(root)}: {exc}") from exc


def _remote_uses_https(url: str) -> bool:
    if url.startswith("http://") or url.startswith("https://"):
        return True
    # SCP-style SSH remotes (user@host:path) do not have an HTTPS scheme.
    return urlsplit(url).scheme in {"http", "https"}


def _backup_once_locked(root: Path, remote: str, branch: str, push_enabled: bool, username: str, token: str,
                author_name: str, author_email: str, repository_url: str = "") -> str:
    """Commit canonical files when changed, then push the configured branch."""
    root = root.resolve()
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise BackupError("a pasta configurada não é um repositório Git")
    current_branch = _git(root, "branch", "--show-current")
    if current_branch != branch:
        raise BackupError(f"a branch atual é '{current_branch or '(sem branch)'}', mas o backup espera '{branch}'")
    remote_url = ""
    if push_enabled:
        remote_url = _git(root, "remote", "get-url", remote, check=False)
        if repository_url:
            if remote_url:
                _git(root, "remote", "set-url", remote, repository_url)
            else:
                _git(root, "remote", "add", remote, repository_url)
            remote_url = repository_url
        elif not remote_url:
            raise BackupError(f"o remoto Git '{remote}' não está configurado")

    staged_before = _git(root, "diff", "--cached", "--name-only")
    if staged_before:
        raise BackupError("há mudanças já preparadas no Git; finalize-as antes do backup automático")
    _validate_memory(root)
    _validate_sources(root)
    existing_paths = [name for name in CANONICAL_PATHS if (root / name).exists()]
    if existing_paths:
        _git(root, "add", "--", *existing_paths)
    staged = _git(root, "diff", "--cached", "--name-only")
    outside = [name for name in staged.splitlines()
               if not any(name == base or name.startswith(base + "/") for base in CANONICAL_PATHS)]
    if outside:
        _git(root, "reset", "--quiet")
        raise BackupError("o conjunto preparado contém arquivos fora de memory/, skills/ e sources/")

    if staged:
        _git(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
             "commit", "-m", "chore(omm): backup project data")
        outcome = "backup salvo em commit local"
    else:
        outcome = "memória sem alterações"

    if not push_enabled:
        return outcome + "; salvo somente no Git local"
    if _remote_uses_https(remote_url) and not token:
        raise BackupError("commit salvo localmente; informe OMM_GIT_BACKUP_TOKEN para enviá-lo ao remoto HTTPS")

    push_args = ["push", remote, f"HEAD:refs/heads/{branch}"]
    credential_file: str | None = None
    try:
        if token:
            fd, credential_file = tempfile.mkstemp(prefix="omm-git-credentials-")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as credentials:
                host = urlsplit(remote_url).hostname
                credentials.write(f"https://{quote(username, safe='')}:{quote(token, safe='')}@{host}\n")
            push_args = ["-c", f"credential.helper=store --file={credential_file}", *push_args]
        _git(root, *push_args)
    except (OSError, BackupError):
        if staged:
            outcome += "; o envio falhou e o commit continua local"
        raise
    finally:
        if credential_file:
            try:
                os.unlink(credential_file)
            except FileNotFoundError:
                pass
    return outcome + "; enviado ao remoto"


def backup_once(root: Path, remote: str, branch: str, push_enabled: bool, username: str, token: str,
                author_name: str, author_email: str, repository_url: str = "") -> str:
    """Commit/push canonical data while holding the shared OMM data lock."""
    with data_lock(root):
        return _backup_once_locked(root, remote, branch, push_enabled, username, token,
                                   author_name, author_email, repository_url)


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def main() -> None:
    from croniter import croniter

    parser = argparse.ArgumentParser(description="Backup agendado da memória canônica da OMM")
    parser.add_argument("--root", default="/data")
    args = parser.parse_args()
    if not _enabled(os.getenv("OMM_GIT_BACKUP_ENABLED", "false")):
        print("Backup automático desativado (OMM_GIT_BACKUP_ENABLED=false).", flush=True)
        return

    schedule = os.getenv("OMM_GIT_BACKUP_SCHEDULE", "0 3 * * *")
    timezone_name = os.getenv("OMM_GIT_BACKUP_TIMEZONE", "America/Sao_Paulo")
    try:
        timezone = ZoneInfo(timezone_name)
        if not croniter.is_valid(schedule):
            raise ValueError("agenda cron inválida")
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"Configuração do backup inválida: {exc}") from exc
    root = Path(args.root)
    remote = os.getenv("OMM_GIT_BACKUP_REMOTE", "origin")
    repository_url = os.getenv("OMM_GIT_BACKUP_REPOSITORY_URL", "").strip()
    branch = os.getenv("OMM_GIT_BACKUP_BRANCH", "main")
    push_enabled = _enabled(os.getenv("OMM_GIT_BACKUP_PUSH", "false"))
    username = os.getenv("OMM_GIT_BACKUP_USERNAME", "x-access-token")
    token = os.getenv("OMM_GIT_BACKUP_TOKEN", "")
    author_name = os.getenv("OMM_GIT_BACKUP_AUTHOR_NAME", "OMM Backup")
    author_email = os.getenv("OMM_GIT_BACKUP_AUTHOR_EMAIL", "omm@localhost")
    print(f"Backup OMM ativo: agenda '{schedule}' ({timezone_name}), remoto '{remote}'.", flush=True)

    while True:
        now = datetime.now(timezone)
        next_run = croniter(schedule, now).get_next(datetime)
        delay = max(1.0, (next_run - now).total_seconds())
        print(f"Próximo backup: {next_run.isoformat()}", flush=True)
        time.sleep(delay)
        try:
            result = backup_once(root, remote, branch, push_enabled, username, token, author_name, author_email,
                                 repository_url)
            print(f"Backup concluído: {result}.", flush=True)
        except BackupError as exc:
            print(f"Backup não concluído: {exc}", flush=True)


if __name__ == "__main__":
    main()
