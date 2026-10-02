"""Restore the canonical OMM files from a Git backup repository."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import quote, urlsplit

from .backup_worker import BackupError, _validate_memory, _validate_sources
from .service import OMM


class RestoreError(RuntimeError):
    pass


def resolve_restore_source(destination: Path, configured_source: str = "") -> str:
    """Use the configured backup URL, or the data checkout's origin when available."""
    if configured_source.strip():
        return configured_source.strip()
    result = subprocess.run(["git", "-C", str(destination), "remote", "get-url", "origin"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_pristine_root(destination: Path) -> bool:
    """Allow replacing only OMM's untouched startup skeleton, preserving its index volume."""
    if not destination.exists():
        return True
    if not destination.is_dir() or (set(p.name for p in destination.iterdir()) -
                                    {".omm", ".omm-write.lock", "memory", "skills", "sources"}):
        return False
    memory = destination / "memory"
    if memory.exists():
        expected = {"records.jsonl", "policies.jsonl", "handoffs.jsonl", "workstreams.jsonl",
                    "proposals.jsonl", "state.json"}
        files = {p.relative_to(memory).as_posix() for p in memory.rglob("*") if p.is_file()}
        if files != expected:
            return False
        for name in expected - {"state.json"}:
            if (memory / name).read_text(encoding="utf-8").strip():
                return False
        try:
            state = json.loads((memory / "state.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        default_state = {
            "schema_version": 1, "status": "not_started", "summary": "", "blockers": [],
            "open_questions": [], "next_actions": [], "updated_at": None, "updated_by": None,
        }
        if state != default_state:
            return False
    skills = destination / "skills"
    if skills.exists() and any(path.is_file() for path in skills.rglob("*")):
        return False
    sources = destination / "sources"
    if sources.exists() and any(sources.rglob("*")):
        return False
    return True


def restore_on_start(destination: Path, source: str, branch: str = "main",
                     username: str = "", token: str = "") -> tuple[str, int]:
    """Bootstrap a fresh or untouched OMM data folder from Git, without replacing real data."""
    destination = destination.resolve()
    if not source.strip():
        raise RestoreError("OMM_GIT_BACKUP_REPOSITORY_URL precisa conter a URL do backup")

    git_dir = destination / ".git"
    if git_dir.is_dir():
        result = subprocess.run(["git", "-C", str(destination), "remote", "get-url", "origin"],
                                capture_output=True, text=True, check=False)
        current_source = result.stdout.strip().rstrip("/") if result.returncode == 0 else ""
        if current_source == source.strip().rstrip("/"):
            return _update_existing_checkout(destination, branch, username, token)
        raise RestoreError("a pasta de dados já contém outro repositório Git; restore automático cancelado")

    if not _is_pristine_root(destination):
        raise RestoreError("a pasta de dados contém arquivos ou memórias; nada foi substituído. Use uma pasta vazia ou preserve os dados antes do restore")

    destination.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".omm-restore-stage-", dir=destination))
    staged_data = stage_root / "data"
    moved_old: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        count = restore_from_git(staged_data, source, branch, username, token)
        # The Compose index is mounted separately at /data/.omm; keep it and rebuild it
        # after installing the canonical files and Git history.
        for name in ("memory", "skills", "sources"):
            old_path = destination / name
            if old_path.exists():
                saved_path = stage_root / f"previous-{name}"
                os.replace(old_path, saved_path)
                moved_old.append((saved_path, old_path))
        for child in staged_data.iterdir():
            if child.name == ".omm":
                continue
            target = destination / child.name
            if target.exists():
                raise RestoreError(f"o restore não substituiu o item existente: {target.name}")
            os.replace(child, target)
            installed.append(target)
        return "restaurada", count
    except (OSError, ValueError, BackupError, RestoreError) as exc:
        for path in installed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for saved_path, old_path in moved_old:
            if saved_path.exists():
                os.replace(saved_path, old_path)
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(str(exc)) from exc
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _update_existing_checkout(destination: Path, branch: str, username: str, token: str) -> tuple[str, int]:
    """Fast-forward a clean data checkout; never replace uncommitted user data."""
    status = subprocess.run(["git", "-C", str(destination), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=False)
    if status.returncode:
        return "sincronização não verificada", 0
    dirty = []
    for line in status.stdout.splitlines():
        path = line[3:]
        if path in {".omm", ".omm-write.lock"} or path.startswith(".omm/"):
            continue
        # Older data backups predate the review inbox. Its newly-created empty
        # file is application scaffolding, so it must not block first upgrade.
        if path == "memory/proposals.jsonl":
            proposal_path = destination / path
            if proposal_path.is_file() and proposal_path.stat().st_size == 0:
                continue
        dirty.append(line)
    if dirty:
        return "alterações locais preservadas", 0

    current_branch = subprocess.run(["git", "-C", str(destination), "branch", "--show-current"],
                                    capture_output=True, text=True, check=False).stdout.strip()
    if current_branch != branch:
        return "branch local preservada", 0
    fetch = ["-C", str(destination), "fetch", "origin", branch]
    try:
        _run_git_authenticated(fetch, _git_output(destination, "remote", "get-url", "origin"), username, token)
    except RestoreError:
        return "backup remoto indisponível", 0

    remote_ref = f"refs/remotes/origin/{branch}"
    current = _git_output(destination, "rev-parse", "HEAD")
    remote_head = _git_output(destination, "rev-parse", remote_ref)
    if current == remote_head:
        return "já atualizada", 0
    if _git_check(destination, "merge-base", "--is-ancestor", remote_ref, "HEAD"):
        return "backup local mais recente", 0
    if not _git_check(destination, "merge-base", "--is-ancestor", "HEAD", remote_ref):
        return "históricos divergentes", 0

    try:
        _run_git(["-C", str(destination), "merge", "--ff-only", remote_ref])
        OMM(destination).init()
        return "atualizada", len(list(OMM(destination).store.records()))
    except (RestoreError, OSError, ValueError):
        return "atualização adiada", 0


def _git_output(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RestoreError(result.stderr.strip() or result.stdout.strip() or "falha ao consultar Git")
    return result.stdout.strip()


def _git_check(root: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=False).returncode == 0


def _run_git_authenticated(args: list[str], source: str, username: str, token: str) -> None:
    credential_file: str | None = None
    try:
        if token and urlsplit(source).scheme in {"http", "https"}:
            host = urlsplit(source).hostname
            if not host:
                raise RestoreError("o endereço HTTPS do repositório não é válido")
            fd, credential_file = tempfile.mkstemp(prefix="omm-restore-credentials-")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as credentials:
                credentials.write(f"https://{quote(username, safe='')}:{quote(token, safe='')}@{host}\n")
            args = ["-c", f"credential.helper=store --file={credential_file}", *args]
        _run_git(args)
    finally:
        if credential_file:
            try:
                os.unlink(credential_file)
            except FileNotFoundError:
                pass


def _run_git(args: list[str]) -> None:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False, env=environment)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "falha no Git"
        raise RestoreError(message)


def restore_from_git(destination: Path, source: str, branch: str = "main",
                     username: str = "", token: str = "") -> int:
    """Clone a backup into an empty destination and rebuild its local search index."""
    destination = destination.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise RestoreError(f"a pasta de destino precisa estar vazia: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}-restore-",
                                           dir=destination.parent))
    checkout = temporary_root / "repository"
    credential_file: str | None = None
    try:
        command = ["clone", "--branch", branch, "--single-branch", source, str(checkout)]
        if token and urlsplit(source).scheme in {"http", "https"}:
            host = urlsplit(source).hostname
            if not host:
                raise RestoreError("o endereço HTTPS do repositório não é válido")
            fd, credential_file = tempfile.mkstemp(prefix="omm-restore-credentials-")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as credentials:
                credentials.write(f"https://{quote(username, safe='')}:{quote(token, safe='')}@{host}\n")
            command = ["-c", f"credential.helper=store --file={credential_file}", *command]
        _run_git(command)
        if not (checkout / "memory").is_dir():
            raise RestoreError("o repositório não contém a pasta memory/")
        _validate_memory(checkout)
        _validate_sources(checkout)
        try:
            OMM(checkout).init()
        except (OSError, ValueError) as exc:
            raise RestoreError(f"a memória do repositório não pôde ser reconstruída: {exc}") from exc
        count = len(list(OMM(checkout).store.records()))
        shutil.rmtree(checkout / ".omm", ignore_errors=True)
        if destination.exists():
            destination.rmdir()
        os.replace(checkout, destination)
        OMM(destination).init()
        return count
    except (OSError, ValueError, BackupError) as exc:
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(str(exc)) from exc
    finally:
        if credential_file:
            try:
                os.unlink(credential_file)
            except FileNotFoundError:
                pass
        shutil.rmtree(temporary_root, ignore_errors=True)
