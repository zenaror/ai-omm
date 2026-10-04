"""Bidirectional synchronization of OMM's canonical files with its Git backup."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from .backup_worker import BackupError, CANONICAL_PATHS, _validate_memory, _validate_sources
from .locking import data_lock


class SyncError(RuntimeError):
    pass


def _run(root: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True,
                          check=False, env=env)
    if check and proc.returncode:
        raise SyncError(proc.stderr.strip() or proc.stdout.strip() or "falha no Git")
    return proc.stdout.strip()


def _inside_canonical(path: str) -> bool:
    return any(path == base or path.startswith(base + "/") for base in CANONICAL_PATHS)


def _credential_args(root: Path, remote: str, remote_url: str, username: str, token: str,
                     operation: list[str], extra_env: dict[str, str] | None = None) -> str:
    credential_file = None
    command_env = os.environ.copy()
    command_env.update(extra_env or {})
    try:
        if token:
            host = urlsplit(remote_url).hostname
            if not host:
                raise SyncError("não consegui identificar o servidor Git para autenticação")
            fd, credential_file = tempfile.mkstemp(prefix="omm-git-credentials-")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(f"https://{quote(username, safe='')}:{quote(token, safe='')}@{host}\n")
            return _run(root, "-c", f"credential.helper=store --file={credential_file}",
                        *operation, env=command_env)
        return _run(root, *operation, env=command_env)
    finally:
        if credential_file:
            try:
                os.unlink(credential_file)
            except FileNotFoundError:
                pass


def _working_tree_paths(root: Path) -> list[str]:
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z",
                             "--untracked-files=all"], capture_output=True, check=True).stdout
    return [entry[3:].decode("utf-8", errors="replace")
            for entry in status.split(b"\0") if len(entry) > 3]


def _is_ancestor(root: Path, older: str, newer: str) -> bool | None:
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
                            capture_output=True, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def preview_sync(root: Path, remote: str = "origin", branch: str = "main",
                 repository_url: str = "", username: str = "", token: str = "") -> dict:
    """Read-only sync preflight; it never stages, commits, fetches, merges, or pushes."""
    root = root.resolve()
    if _run(root, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise SyncError("a pasta de dados ainda não é um repositório Git restaurado")
    current_branch = _run(root, "branch", "--show-current")
    if current_branch != branch:
        raise SyncError(f"a branch atual precisa ser '{branch}' para sincronizar")
    configured_url = _run(root, "remote", "get-url", remote, check=False)
    remote_url = repository_url or configured_url
    if not remote_url:
        raise SyncError(f"o remoto Git '{remote}' não está configurado")

    staged_paths = _run(root, "diff", "--cached", "--name-only").splitlines()
    changed_paths = _working_tree_paths(root)
    ignored_generated = {".omm/index.sqlite3", ".omm-write.lock"}
    outside_paths = sorted(path for path in changed_paths
                           if not _inside_canonical(path) and path not in ignored_generated)
    canonical_paths = sorted(path for path in changed_paths if _inside_canonical(path))
    blocking_reasons: list[str] = []
    if staged_paths:
        blocking_reasons.append("há arquivos preparados manualmente no Git")
    if outside_paths:
        blocking_reasons.append("há mudanças fora de memory/, skills/ e sources/")

    try:
        _validate_memory(root)
        _validate_sources(root)
    except (BackupError, OSError, ValueError) as exc:
        blocking_reasons.append(f"os dados locais precisam de correção: {exc}")

    extra_env: dict[str, str] = {}
    if repository_url and repository_url != configured_url:
        try:
            config_count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        except ValueError as exc:
            raise SyncError("a configuração temporária do Git está inválida") from exc
        extra_env = {
            "GIT_CONFIG_COUNT": str(config_count + 1),
            f"GIT_CONFIG_KEY_{config_count}": f"remote.{remote}.url",
            f"GIT_CONFIG_VALUE_{config_count}": repository_url,
        }
    remote_listing = _credential_args(
        root, remote, remote_url, username, token,
        ["ls-remote", "--heads", remote, f"refs/heads/{branch}"], extra_env=extra_env,
    )
    remote_fields = remote_listing.split()
    if len(remote_fields) < 2:
        raise SyncError("não foi possível localizar a branch remota configurada")
    remote_head = remote_fields[0]
    local_head = _run(root, "rev-parse", "HEAD")
    tracking_ref = f"refs/remotes/{remote}/{branch}"
    cached_remote_head = _run(root, "rev-parse", "--verify", tracking_ref, check=False)
    if remote_head == local_head:
        relationship = "up_to_date"
    elif remote_head != cached_remote_head:
        relationship = "remote_relation_unknown"
    else:
        local_is_ancestor = _is_ancestor(root, local_head, cached_remote_head)
        remote_is_ancestor = _is_ancestor(root, cached_remote_head, local_head)
        if local_is_ancestor is None or remote_is_ancestor is None:
            relationship = "remote_relation_unknown"
        elif local_is_ancestor:
            relationship = "remote_ahead"
        elif remote_is_ancestor:
            relationship = "local_ahead"
        else:
            relationship = "diverged"

    return {
        "ok": not blocking_reasons,
        "dry_run": True,
        "branch": branch,
        "remote": remote,
        "local_head": local_head,
        "remote_head": remote_head,
        "cached_remote_head": cached_remote_head or None,
        "relationship": relationship,
        "local_changes": {
            "canonical_paths": canonical_paths,
            "outside_paths": outside_paths,
            "staged_paths": staged_paths,
        },
        "would_create_local_commit": bool(canonical_paths) and not blocking_reasons,
        "would_rebuild_search_index": not blocking_reasons,
        "conflicts_checked": False,
        "blocking_reasons": blocking_reasons,
        "limitations": [
            "A simulação consulta a ponta atual do remoto, mas não baixa arquivos nem altera o Git.",
            "Ela não monta um merge temporário; conflitos de conteúdo só são confirmados durante a sincronização real.",
            *(["A ponta remota mudou desde a última atualização local; sem baixar os novos objetos, a relação entre as branches é desconhecida."]
              if relationship == "remote_relation_unknown" else []),
        ],
    }


def _stage_local(root: Path, author_name: str, author_email: str) -> bool:
    if _run(root, "diff", "--cached", "--name-only"):
        raise SyncError("há arquivos preparados manualmente no Git; faça o commit deles antes da sincronização")
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                            capture_output=True, check=True).stdout
    changed_paths = [entry[3:].decode("utf-8", errors="replace") for entry in status.split(b"\0") if len(entry) > 3]
    outside = [path for path in changed_paths
               if not _inside_canonical(path) and path not in {".omm/index.sqlite3", ".omm-write.lock"}]
    if outside:
        raise SyncError("há mudanças fora dos dados da OMM; revise-as antes de sincronizar: " + ", ".join(outside[:5]))
    _validate_memory(root)
    _validate_sources(root)
    existing = [path for path in CANONICAL_PATHS if (root / path).exists()]
    if existing:
        _run(root, "add", "-A", "--", *existing)
    staged = _run(root, "diff", "--cached", "--name-only")
    outside = [path for path in staged.splitlines() if not _inside_canonical(path)]
    if outside:
        _run(root, "reset", "--quiet")
        raise SyncError("a sincronização só pode salvar memory/, skills/ e sources/")
    if not staged:
        return False
    _run(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
         "commit", "-m", "chore(omm): salvar alterações locais antes da sincronização")
    return True


def _stage_bytes(root: Path, stage: int, path: str) -> bytes | None:
    proc = subprocess.run(["git", "-C", str(root), "show", f":{stage}:{path}"],
                          capture_output=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def _jsonl_union(local: bytes, remote: bytes) -> tuple[bytes, bool]:
    rows: dict[str, dict] = {}
    conflict = False
    for content in (remote, local):
        for line in content.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row.get("id", ""))
            if key and key in rows and rows[key] != row:
                conflict = True
                continue
            rows[key or f"row-{len(rows)}"] = row
    return ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in rows.values()).encode("utf-8"), conflict)


def _merge_topology(local: bytes, remote: bytes) -> tuple[bytes, bool]:
    ours, theirs = json.loads(local), json.loads(remote)
    result = {**ours, **theirs}
    conflict = False
    for section in ("project_profiles",):
        left, right = ours.get(section, {}), theirs.get(section, {})
        merged = dict(left)
        for project, remote_profile in right.items():
            local_profile = left.get(project)
            if local_profile is None:
                merged[project] = remote_profile
                continue
            profile = {**local_profile, **remote_profile}
            for list_key in ("agents", "shared_project_knowledge"):
                if list_key not in local_profile and list_key not in remote_profile:
                    continue
                by_name = {str(item.get("name")): item for item in local_profile.get(list_key, [])}
                for item in remote_profile.get(list_key, []):
                    name = str(item.get("name"))
                    if name in by_name and by_name[name] != item:
                        conflict = True
                    by_name[name] = item
                profile[list_key] = list(by_name.values())
            if any(k not in {"agents", "shared_project_knowledge"} and k in remote_profile and
                   local_profile.get(k) != remote_profile.get(k) for k in local_profile):
                conflict = True
            merged[project] = profile
        result[section] = merged
    if "subagents" in ours or "subagents" in theirs:
        by_name = {str(item.get("name")): item for item in ours.get("subagents", [])}
        for item in theirs.get("subagents", []):
            name = str(item.get("name"))
            if name in by_name and by_name[name] != item:
                conflict = True
            by_name[name] = item
        result["subagents"] = list(by_name.values())
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), conflict


def _resolve_conflicts(root: Path, author_name: str, author_email: str) -> tuple[int, int, str | None]:
    listing = subprocess.run(["git", "-C", str(root), "ls-files", "-u", "-z"],
                             capture_output=True, check=True).stdout
    stages: dict[str, dict[int, bytes | None]] = {}
    for entry in listing.decode().split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        stage = int(metadata.rsplit(" ", 1)[1])
        stages.setdefault(path, {})[stage] = _stage_bytes(root, stage, path)
    if any(not _inside_canonical(path) for path in stages):
        raise SyncError("o backup também alterou arquivos da aplicação; sincronização cancelada")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recovery = root / "memory" / "imports" / "sync-recovery" / stamp
    manifest: list[dict[str, str]] = []
    for path, versions in stages.items():
        local, remote = versions.get(2), versions.get(3)
        target = root / path
        if path.endswith(".jsonl") and local is not None and remote is not None:
            merged, same_id_conflict = _jsonl_union(local, remote)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(merged)
            if same_id_conflict:
                saved = recovery / "local" / path
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(local)
                manifest.append({"path": path, "saved_local_copy": saved.relative_to(root).as_posix(),
                                 "resolution": "registros com IDs diferentes foram unidos; colisões usam o backup"})
        elif path == "memory/agent-topology.json" and local is not None and remote is not None:
            merged, has_collision = _merge_topology(local, remote)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(merged)
            if has_collision:
                saved = recovery / "local" / path
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(local)
                manifest.append({"path": path, "saved_local_copy": saved.relative_to(root).as_posix(),
                                 "resolution": "perfis e agentes unidos; definições de nomes repetidos usam o backup"})
        else:
            if local is not None:
                saved = recovery / "local" / path
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(local)
                manifest.append({"path": path, "saved_local_copy": saved.relative_to(root).as_posix(),
                                 "resolution": "backup aplicado no caminho original; versão local preservada"})
            if remote is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(remote)
        if target.exists():
            _run(root, "add", "--", path)
        else:
            _run(root, "rm", "--cached", "--ignore-unmatch", "--", path)
    if manifest:
        recovery.mkdir(parents=True, exist_ok=True)
        manifest_path = recovery / "manifest.json"
        manifest_path.write_text(json.dumps({"created_at": stamp, "conflicts": manifest},
                                             ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        _run(root, "add", "--", recovery.relative_to(root).as_posix())
        manifest_relative = manifest_path.relative_to(root).as_posix()
    else:
        manifest_relative = None
    return len(stages), len(manifest), manifest_relative


def _sync_with_backup_locked(root: Path, remote: str = "origin", branch: str = "main", repository_url: str = "",
                     username: str = "", token: str = "", author_name: str = "OMM Backup",
                     author_email: str = "omm@localhost") -> dict[str, object]:
    """Save local canonical changes, merge backup updates, preserve conflicts, then push."""
    root = root.resolve()
    if _run(root, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise SyncError("a pasta de dados ainda não é um repositório Git restaurado")
    if _run(root, "branch", "--show-current") != branch:
        raise SyncError(f"a branch atual precisa ser '{branch}' para sincronizar")
    remote_url = _run(root, "remote", "get-url", remote, check=False)
    if repository_url:
        if remote_url:
            _run(root, "remote", "set-url", remote, repository_url)
        else:
            _run(root, "remote", "add", remote, repository_url)
        remote_url = repository_url
    if not remote_url:
        raise SyncError(f"o remoto Git '{remote}' não está configurado")
    local_saved = _stage_local(root, author_name, author_email)
    _credential_args(root, remote, remote_url, username, token, ["fetch", remote, branch])
    remote_ref = f"refs/remotes/{remote}/{branch}"
    exists = subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet", remote_ref],
                            check=False).returncode == 0
    if not exists:
        raise SyncError("não foi possível localizar a branch remota após buscar o backup")
    behind = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", "HEAD", remote_ref], check=False).returncode == 0
    ahead = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", remote_ref, "HEAD"], check=False).returncode == 0
    if behind:
        _run(root, "merge", "--ff-only", remote_ref)
        merged_conflicts, preserved_copies, recovery_manifest = 0, 0, None
    elif ahead:
        merged_conflicts, preserved_copies, recovery_manifest = 0, 0, None
    else:
        # Git checks the committer identity while preparing some merge states,
        # even when --no-commit is set. Keep the OMM identity on this operation
        # as well as on explicit commits; the container may have no global Git
        # configuration at all.
        proc = subprocess.run(["git", "-C", str(root), "-c", f"user.name={author_name}",
                               "-c", f"user.email={author_email}", "merge", "--no-commit", "--no-ff", remote_ref],
                              text=True, capture_output=True, check=False)
        try:
            if proc.returncode:
                merged_conflicts, preserved_copies, recovery_manifest = _resolve_conflicts(
                    root, author_name, author_email)
            else:
                merged_conflicts, preserved_copies, recovery_manifest = 0, 0, None
        except Exception:
            _run(root, "merge", "--abort", check=False)
            raise
        if proc.returncode and not merged_conflicts:
            _run(root, "merge", "--abort", check=False)
            raise SyncError(proc.stderr.strip() or "não foi possível combinar as branches")
        if _run(root, "ls-files", "-u"):
            _run(root, "merge", "--abort", check=False)
            raise SyncError("não foi possível resolver todos os arquivos em conflito; alterações locais continuam salvas no Git")
        _validate_memory(root)
        _validate_sources(root)
        _run(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
             "commit", "-m", "chore(omm): sincronizar backup")
    _credential_args(root, remote, remote_url, username, token,
                     ["push", remote, f"HEAD:refs/heads/{branch}"])
    message = ("Sincronização concluída: alterações locais salvas" if local_saved else "Sincronização concluída") + \
        f"; {merged_conflicts} conflito(s) preservado(s)" + \
        (f"; {preserved_copies} cópia(s) local(is) em {recovery_manifest}"
         if preserved_copies and recovery_manifest else "")
    return {
        "ok": True,
        "branch": branch,
        "remote": remote,
        "pushed": True,
        "local_changes_committed": local_saved,
        "conflicts_preserved": merged_conflicts,
        "local_copies_preserved": preserved_copies,
        "recovery_manifest": recovery_manifest,
        "conflicts_checked": True,
        "message": message,
    }


def sync_with_backup_report(root: Path, remote: str = "origin", branch: str = "main", repository_url: str = "",
                            username: str = "", token: str = "", author_name: str = "OMM Backup",
                            author_email: str = "omm@localhost") -> dict[str, object]:
    """Run a Git sync and return fields suitable for scripts and monitoring."""
    with data_lock(root):
        return _sync_with_backup_locked(root, remote, branch, repository_url,
                                        username, token, author_name, author_email)


def sync_with_backup(root: Path, remote: str = "origin", branch: str = "main", repository_url: str = "",
                     username: str = "", token: str = "", author_name: str = "OMM Backup",
                     author_email: str = "omm@localhost") -> str:
    """Run a whole Git sync without racing another OMM writer or backup job."""
    return str(sync_with_backup_report(root, remote, branch, repository_url, username, token,
                                       author_name, author_email)["message"])
