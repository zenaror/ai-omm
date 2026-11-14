"""Scheduled Git backup for OMM's canonical project data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo
from .locking import data_lock
from .backup_state import write_backup_state, backup_lock_root
from .image_evidence import CARD_SUFFIX, IMAGE_SUFFIXES, MAX_IMAGE_BYTES, inspect_image, read_image
from . import artifacts

CANONICAL_PATHS = ("memory", "skills", "sources")
MAX_SOURCE_PDF_BYTES = 100 * 1024 * 1024
_retry_sleep = time.sleep


class BackupError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True, env: dict[str, str] | None = None, timeout: int = 1800) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], text=True, capture_output=True,
            check=False, env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError("tempo esgotado na operação Git") from exc
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
    blobs, referenced = set(), set()
    for path in sources.rglob("*"):
        if path.is_symlink():
            raise BackupError(f"documentos-fonte não podem apontar para fora da pasta de dados: {path.relative_to(root)}")
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".md":
                path.read_text(encoding="utf-8")
                if path.name.endswith(CARD_SUFFIX):
                    read_image(root, path.relative_to(root).as_posix(), "original")
                elif path.name.endswith(artifacts.CARD_SUFFIX):
                    card, _ = artifacts._read(root, path.relative_to(root).as_posix())
                    referenced.add(path.parent / card["artifact"]["filename"])
            elif suffix == ".blob":
                blobs.add(path)
            elif suffix == ".pdf":
                if path.stat().st_size > MAX_SOURCE_PDF_BYTES:
                    raise BackupError(f"PDF-fonte excede 100 MB: {path.relative_to(root)}")
                with path.open("rb") as source:
                    header = source.read(1024)
                if b"%PDF-" not in header:
                    raise BackupError(f"PDF-fonte inválido em {path.relative_to(root)}")
            elif suffix in IMAGE_SUFFIXES:
                with path.open("rb") as source:
                    inspect_image(source.read(MAX_IMAGE_BYTES + 1))
            else:
                raise BackupError(
                    f"formato ainda não aceito em sources/: {path.relative_to(root)} (use Markdown, PDF, PNG, JPEG ou WebP)"
                )
        except (OSError, UnicodeError, ValueError) as exc:
            raise BackupError(f"documento-fonte inválido em {path.relative_to(root)}: {exc}") from exc
    if blobs - referenced:
        raise BackupError("há arquivo de recuperação sem descrição válida; nenhum backup foi declarado concluído")


def _remote_uses_https(url: str) -> bool:
    if url.startswith("http://") or url.startswith("https://"):
        return True
    # SCP-style SSH remotes (user@host:path) do not have an HTTPS scheme.
    return urlsplit(url).scheme in {"http", "https"}


def _backup_once_locked(root: Path, remote: str, branch: str, push_enabled: bool, username: str, token: str,
                author_name: str, author_email: str, repository_url: str = "") -> tuple[str, str, str]:
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

    return outcome, _git(root, "rev-parse", "HEAD"), remote_url


def _error_category(error: Exception) -> str:
    text = str(error).lower()
    if any(x in text for x in ("504", "502", "503", "timeout", "tempo esgotado", "disconnect", "hung up", "connection reset", "could not resolve", "failed to connect")):
        return "transport"
    if any(x in text for x in ("authentication", "permission denied", "token", "403", "401")):
        return "authentication"
    if any(x in text for x in ("non-fast-forward", "rejected", "fetch first")):
        return "remote_conflict"
    return "validation_or_git"


def _send_backup(root, remote, branch, remote_url, username, token, target, attempts=3):
    credential_file = None
    prefix = []
    try:
        if _remote_uses_https(remote_url) and not token:
            raise BackupError("commit salvo localmente; informe OMM_GIT_BACKUP_TOKEN para envio HTTPS")
        if token and _remote_uses_https(remote_url):
            fd, credential_file = tempfile.mkstemp(prefix="omm-git-credentials-")
            os.fchmod(fd, 0o600)
            url = urlsplit(remote_url)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(f"{url.scheme}://{quote(username, safe='')}:{quote(token, safe='')}@{url.netloc}\n")
            prefix = ["-c", f"credential.helper=store --file={credential_file}"]
        last_error = BackupError("não foi possível confirmar o envio remoto")
        for attempt in range(1, attempts + 1):
            write_backup_state(root, state="sending", attempt=attempt, local_head=target, error_category=None)
            try:
                # An earlier timed-out push may actually have reached the server.
                listing = _git(root, *prefix, "ls-remote", "--heads", remote,
                               f"refs/heads/{branch}", timeout=30)
                remote_head = listing.split()[0] if listing else None
                if remote_head != target:
                    try:
                        _git(root, *prefix, "push", remote, f"{target}:refs/heads/{branch}")
                    except BackupError as exc:
                        last_error = exc
                    # Never infer delivery from the push's exit code or prose.
                    listing = _git(root, *prefix, "ls-remote", "--heads", remote,
                                   f"refs/heads/{branch}", timeout=30)
                    remote_head = listing.split()[0] if listing else None
                write_backup_state(root, remote_head=remote_head)
                if remote_head == target:
                    write_backup_state(root, state="synced", error_category=None,
                                       last_success_at=datetime.now(timezone.utc).isoformat())
                    return
                if _error_category(last_error) != "transport":
                    break
            except BackupError as exc:
                last_error = exc
                if _error_category(exc) != "transport":
                    break
            if attempt < attempts:
                write_backup_state(root, state="retrying", error_category=_error_category(last_error))
                _retry_sleep(min(60, 5 * 2 ** (attempt - 1)))
        raise last_error
    finally:
        if credential_file:
            Path(credential_file).unlink(missing_ok=True)


def backup_once(root: Path, remote: str, branch: str, push_enabled: bool, username: str, token: str,
                author_name: str, author_email: str, repository_url: str = "") -> str:
    """Snapshot under the data lock; network waits never block memory writers."""
    root = root.resolve()
    with data_lock(backup_lock_root(root)):
        write_backup_state(root, state="preparing", error_category=None,
                           last_attempt_at=datetime.now(timezone.utc).isoformat())
        try:
            with data_lock(root):
                outcome, target, remote_url = _backup_once_locked(
                    root, remote, branch, push_enabled, username, token,
                    author_name, author_email, repository_url)
            write_backup_state(root, local_head=target)
            if not push_enabled:
                write_backup_state(root, state="local_only")
                return outcome + "; salvo somente no Git local"
            _send_backup(root, remote, branch, remote_url, username, token, target)
            return outcome + "; enviado ao remoto"
        except (OSError, BackupError) as exc:
            write_backup_state(root, state="failed", error_category=_error_category(exc))
            raise


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
