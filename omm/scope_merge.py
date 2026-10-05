"""Guarded consolidation of two project scopes in canonical OMM data."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone
from typing import Any


_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_PROJECT_TAG_PREFIX = "project:"


class ScopeMergeError(ValueError):
    """A scope merge cannot be completed safely."""


def _validate_scopes(source: str, target: str) -> None:
    if not isinstance(source, str) or not _SCOPE.fullmatch(source):
        raise ScopeMergeError("O escopo de origem precisa ser um nome simples de projeto.")
    if not isinstance(target, str) or not _SCOPE.fullmatch(target):
        raise ScopeMergeError("O escopo de destino precisa ser um nome simples de projeto.")
    if source == target:
        raise ScopeMergeError("Origem e destino precisam ser escopos diferentes.")
    if source in {"global", "default"} or target in {"global", "default"}:
        raise ScopeMergeError("Escopos global e default não podem ser mesclados como projetos.")


def _jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    raw = path.read_bytes() if path.exists() else b""
    items: list[dict[str, Any]] = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ScopeMergeError(f"JSON inválido em {path.name}, linha {number}.") from exc
        if not isinstance(item, dict):
            raise ScopeMergeError(f"Registro inválido em {path.name}, linha {number}.")
        items.append(item)
    return items, raw


def _encode_jsonl(items: list[dict[str, Any]]) -> bytes:
    if not items:
        return b""
    return ("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items) + "\n").encode("utf-8")


def _safe_under_root(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return False
    return path.resolve().is_relative_to(root)


def _rewrite_paths(value: Any, source: str, target: str) -> Any:
    if isinstance(value, str):
        if value == f"sources/{source}":
            return f"sources/{target}/legacy-{source}"
        return value.replace(f"sources/{source}/", f"sources/{target}/legacy-{source}/")
    if isinstance(value, list):
        return [_rewrite_paths(item, source, target) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_paths(item, source, target) for key, item in value.items()}
    return value


def _rewrite_scope_fields(value: Any, source: str, target: str) -> Any:
    if isinstance(value, list):
        return [_rewrite_scope_fields(item, source, target) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "scope" and item == source:
            item = target
        elif key in {"scope", "project_scope"} and item == f"project:{source}":
            item = f"project:{target}"
        elif key == "tags" and isinstance(item, list):
            item = [f"{_PROJECT_TAG_PREFIX}{target}" if tag == f"{_PROJECT_TAG_PREFIX}{source}" else tag
                    for tag in item]
        if key in {"path", "source", "evidence"}:
            result[key] = _rewrite_paths(item, source, target)
        else:
            result[key] = _rewrite_scope_fields(item, source, target)
    return result


def _scope_counts(items: list[dict[str, Any]], source: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if item.get("scope", "default") == source:
            status = str(item.get("status", "unspecified"))
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _handoff_timestamp(item: dict[str, Any]) -> float:
    value = datetime.fromisoformat(str(item["updated_at"]).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _source_files(root: Path, source: str, target: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    sources_root = root / "sources"
    source_dir = sources_root / source
    target_scope_dir = sources_root / target
    blockers: list[str] = []
    if sources_root.is_symlink() or (sources_root.exists() and not sources_root.resolve().is_relative_to(root)):
        return [], ["A pasta sources/ não é segura."], source_dir.exists()
    if source_dir.is_symlink():
        return [], [f"A pasta sources/{source} é um link simbólico."], True
    if target_scope_dir.is_symlink():
        return [], [f"A pasta sources/{target} é um link simbólico."], source_dir.exists()
    if not source_dir.exists():
        return [], blockers, False
    files: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            blockers.append(f"Há link simbólico em sources/{source}; nenhum arquivo será movido.")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            blockers.append(f"Há um item que não é arquivo comum em sources/{source}.")
            continue
        if path.suffix.lower() != ".md":
            blockers.append(f"A OMM só guarda documentos Markdown; formato não aceito em {path.relative_to(root)}.")
            continue
        if not path.resolve().is_relative_to(source_dir.resolve()):
            blockers.append(f"Uma fonte sai da pasta sources/{source}.")
            continue
        relative = path.relative_to(source_dir)
        destination = target_scope_dir / f"legacy-{source}" / relative
        destination_cursor = root
        unsafe_parent = False
        for part in destination.relative_to(root).parts[:-1]:
            destination_cursor = destination_cursor / part
            if destination_cursor.is_symlink():
                unsafe_parent = True
                break
        if unsafe_parent or not destination.resolve().is_relative_to(root):
            blockers.append("O destino de uma fonte sairia dos dados da OMM.")
            continue
        data = path.read_bytes()
        collision = None
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                blockers.append(f"O destino de uma fonte já existe e não é arquivo comum: {destination.relative_to(root)}.")
            else:
                existing = destination.read_bytes()
                if existing != data:
                    blockers.append(f"Há conteúdo diferente no destino {destination.relative_to(root)}.")
                else:
                    collision = "identical"
        files.append({
            "source": path,
            "destination": destination,
            "relative": path.relative_to(root).as_posix(),
            "destination_relative": destination.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "collision": collision,
            "content": data,
            "mode": stat.S_IMODE(path.stat().st_mode),
        })
    return files, blockers, True


def _skill_files(root: Path, source: str, target: str) -> tuple[list[dict[str, Any]], list[str]]:
    skills_root = root / "skills"
    if not skills_root.exists():
        return [], []
    if skills_root.is_symlink() or not skills_root.resolve().is_relative_to(root):
        return [], ["A pasta skills/ não é segura."]
    files: list[dict[str, Any]] = []
    blockers: list[str] = []
    for path in sorted(skills_root.glob("*/SKILL.md")):
        if not _safe_under_root(root, path) or not path.is_file():
            blockers.append("Há uma skill em caminho não seguro.")
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            blockers.append(f"A skill {path.parent.name} não está em UTF-8.")
            continue
        updated = re.sub(
            rf"(?m)^(\s*scope:\s*)([\"']?)project:{re.escape(source)}([\"']?)(\s*)$",
            lambda match: f"{match.group(1)}{match.group(2)}project:{target}{match.group(3)}{match.group(4)}",
            text,
        )
        if updated != text:
            files.append({"path": path, "old": raw, "new": updated.encode("utf-8"),
                          "sha256": hashlib.sha256(raw).hexdigest()})
    return files, blockers


def _topology_update(root: Path, source: str, target: str) -> tuple[bytes | None, bytes | None, list[str], bool]:
    path = root / "memory" / "agent-topology.json"
    if not path.exists():
        return None, None, [], False
    if not _safe_under_root(root, path):
        return None, None, ["agent-topology.json não está em um caminho seguro."], False
    raw = path.read_bytes()
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return raw, None, ["agent-topology.json não pôde ser lido; nenhum perfil será alterado."], False
    if not isinstance(config, dict):
        return raw, None, ["agent-topology.json não contém um objeto válido."], False
    profiles = config.get("project_profiles", {})
    if not isinstance(profiles, dict):
        return raw, None, ["project_profiles em agent-topology.json não é válido."], False
    if source not in profiles:
        return raw, None, [], False
    if not isinstance(profiles[source], dict):
        return raw, None, [f"O perfil de {source} não é um objeto válido."], True
    if target in profiles:
        return raw, None, [f"A topologia já tem perfis para {source} e {target}; precisam ser combinados manualmente."], True
    if profiles[source].get("shared_scope", source) != source:
        return raw, None, [f"O perfil de {source} tem um shared_scope diferente e precisa ser revisado manualmente."], True
    profile = _rewrite_scope_fields(profiles.pop(source), source, target)
    if isinstance(profile, dict) and profile.get("shared_scope", source) == source:
        profile["shared_scope"] = target
    profiles[target] = profile
    updated = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return raw, updated, [], True


def build_scope_merge_plan(root: Path, source: str, target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a content-free preview and an internal change set for a guarded merge."""
    _validate_scopes(source, target)
    root = root.resolve()
    paths = {
        "records": root / "memory" / "records.jsonl",
        "handoffs": root / "memory" / "handoffs.jsonl",
        "policies": root / "memory" / "policies.jsonl",
        "proposals": root / "memory" / "proposals.jsonl",
    }
    blockers: list[str] = []
    loaded: dict[str, list[dict[str, Any]]] = {}
    original_bytes: dict[str, bytes] = {}
    for name, path in paths.items():
        if not _safe_under_root(root, path):
            blockers.append(f"{path.name} não está em um caminho seguro.")
            loaded[name], original_bytes[name] = [], b""
            continue
        try:
            loaded[name], original_bytes[name] = _jsonl(path)
        except (OSError, UnicodeError, ScopeMergeError) as exc:
            blockers.append(str(exc))
            loaded[name], original_bytes[name] = [], b""

    source_files, source_blockers, source_dir_exists = _source_files(root, source, target)
    blockers.extend(source_blockers)
    skill_files, skill_blockers = _skill_files(root, source, target)
    blockers.extend(skill_blockers)
    topology_old, topology_new, topology_blockers, topology_profile = _topology_update(root, source, target)
    blockers.extend(topology_blockers)

    counts = {
        "memories_by_status": _scope_counts(loaded["records"], source),
        "target_memories_by_status": _scope_counts(loaded["records"], target),
        "handoffs": sum(item.get("scope", "default") == source for item in loaded["handoffs"]),
        "policies": sum(item.get("scope", "default") == source for item in loaded["policies"]),
        "proposals": sum(
            (item.get("record", {}).get("scope") if isinstance(item.get("record"), dict) else None)
            == source or item.get("scope") == source
            for item in loaded["proposals"]
        ),
        "source_files": len(source_files),
        "source_files_already_present": sum(item["collision"] == "identical" for item in source_files),
        "skills_reclassified": len(skill_files),
        "topology_profile": topology_profile,
        "source_directory_exists": source_dir_exists,
    }
    if counts["handoffs"]:
        for item in loaded["handoffs"]:
            try:
                _handoff_timestamp(item)
            except (KeyError, TypeError, ValueError):
                blockers.append("Há handoff sem data legível; não é seguro definir qual estado de projeto é o mais recente.")
    if not any((sum(counts["memories_by_status"].values()), counts["handoffs"], counts["policies"],
                counts["proposals"], counts["source_files"], counts["skills_reclassified"],
                counts["topology_profile"])):
        blockers.append(f"Não há conteúdo de OMM no escopo {source} para mover.")

    snapshot = {
        "source": source,
        "target": target,
        "file_hashes": {name: hashlib.sha256(data).hexdigest() for name, data in original_bytes.items()},
        "source_files": [{key: item[key] for key in ("relative", "destination_relative", "sha256", "collision")}
                         for item in source_files],
        "skill_files": [{"path": item["path"].relative_to(root).as_posix(), "sha256": item["sha256"]}
                        for item in skill_files],
        "topology_sha256": hashlib.sha256(topology_old).hexdigest() if topology_old is not None else None,
        "counts": counts,
        "blockers": sorted(set(blockers)),
    }
    plan_sha256 = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    public_plan = {
        "status": "blocked" if blockers else "ready",
        "source_scope": source,
        "target_scope": target,
        "counts": counts,
        "blockers": sorted(set(blockers)),
        "plan_sha256": plan_sha256,
        "note": "A simulação não altera dados. A execução exige o mesmo plan_sha256 e preserva os IDs e status das anotações.",
    }
    changes = {
        "paths": paths,
        "loaded": loaded,
        "original_bytes": original_bytes,
        "source_files": source_files,
        "skill_files": skill_files,
        "topology_path": root / "memory" / "agent-topology.json",
        "topology_old": topology_old,
        "topology_new": topology_new,
        "plan_sha256": plan_sha256,
        "blockers": sorted(set(blockers)),
    }
    return public_plan, changes


def _atomic_write(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".omm-scope-merge-", suffix=".tmp",
                                         delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if mode is not None:
            os.chmod(temporary_name, mode)
        elif path.exists():
            os.chmod(temporary_name, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def apply_scope_merge(root: Path, source: str, target: str,
                      expected_plan_sha256: str) -> dict[str, Any]:
    """Apply a previewed merge. Callers must hold the OMM operation lock."""
    if not re.fullmatch(r"[a-f0-9]{64}", expected_plan_sha256 or ""):
        raise ScopeMergeError("Execute primeiro a simulação e informe o plan_sha256 completo.")
    plan, changes = build_scope_merge_plan(root, source, target)
    if not hmac.compare_digest(plan["plan_sha256"], expected_plan_sha256):
        raise ScopeMergeError("Os dados mudaram desde a simulação. Faça uma nova simulação antes de continuar.")
    if plan["status"] != "ready":
        raise ScopeMergeError("A simulação encontrou bloqueios: " + "; ".join(plan["blockers"]))

    updated_jsonl: dict[Path, bytes] = {}
    for name, items in changes["loaded"].items():
        updated = [_rewrite_scope_fields(item, source, target) for item in items]
        if name == "handoffs" and any(item.get("scope", "default") == source for item in items):
            indexed = list(enumerate(updated))
            indexed.sort(key=lambda pair: (
                _handoff_timestamp(pair[1]),
                pair[0],
            ))
            updated = [item for _, item in indexed]
        if updated != items:
            updated_jsonl[changes["paths"][name]] = _encode_jsonl(updated)
    topology_path = changes["topology_path"]
    if changes["topology_new"] is not None:
        updated_jsonl[topology_path] = changes["topology_new"]
    for item in changes["skill_files"]:
        updated_jsonl[item["path"]] = item["new"]

    originals: dict[Path, bytes | None] = {}
    for path in updated_jsonl:
        originals[path] = path.read_bytes() if path.exists() else None
    created_source_destinations: list[Path] = []
    removed_sources: list[tuple[Path, bytes, int]] = []
    created_directories: set[Path] = set()
    applied_metadata: list[Path] = []
    staged_files: list[Path] = []
    try:
        # Install source copies first; the original paths remain available until metadata is committed.
        for item in changes["source_files"]:
            if item["collision"] == "identical":
                continue
            destination = item["destination"]
            missing: list[Path] = []
            parent = destination.parent
            while parent != root and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_directories.update(missing)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".omm-scope-merge-",
                                             suffix=".tmp", delete=False) as temporary:
                temporary.write(item["content"])
                temporary.flush()
                os.fsync(temporary.fileno())
                staged_files.append(Path(temporary.name))
                os.chmod(temporary.name, item["mode"])
                os.replace(temporary.name, destination)
                staged_files.remove(Path(temporary.name))
            created_source_destinations.append(destination)

        for path, content in updated_jsonl.items():
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            _atomic_write(path, content, mode)
            applied_metadata.append(path)

        for item in changes["source_files"]:
            path = item["source"]
            removed_sources.append((path, item["content"], item["mode"]))
            path.unlink()
        source_root = root / "sources" / source
        if source_root.exists():
            for directory in sorted((p for p in source_root.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                source_root.rmdir()
            except OSError:
                pass
    except Exception as exc:
        for path, content, mode in reversed(removed_sources):
            try:
                _atomic_write(path, content, mode)
            except OSError:
                pass
        for path in reversed(applied_metadata):
            try:
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, original)
            except OSError:
                pass
        for path in created_source_destinations:
            path.unlink(missing_ok=True)
        for path in sorted(created_directories, key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        for path in staged_files:
            path.unlink(missing_ok=True)
        raise ScopeMergeError(f"A consolidação foi revertida após uma falha: {type(exc).__name__}.") from exc

    return {
        "status": "merged",
        "source_scope": source,
        "target_scope": target,
        "counts": plan["counts"],
        "plan_sha256": plan["plan_sha256"],
        "note": "IDs, conteúdo, proveniência e status das anotações foram preservados. O índice lexical foi reconstruído pelo serviço.",
    }
