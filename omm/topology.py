from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .redaction import find_credentials


class TopologyError(ValueError):
    pass


def topology_sha256(root: Path) -> str:
    path = root / "memory" / "agent-topology.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_historical_source(root: Path, scope: str, name: str, platform: str,
                          session_id: str, source_path: str, purpose: str,
                          expected_sha256: str) -> dict[str, object]:
    """Add one already-imported conversation to a project profile with concurrency protection."""
    path = root / "memory" / "agent-topology.json"
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256 or ""):
        raise TopologyError("Informe o sha256 atual da topologia antes de alterá-la.")
    current = path.read_bytes()
    old_digest = hashlib.sha256(current).hexdigest()
    if old_digest != expected_sha256.lower():
        raise TopologyError("A topologia mudou desde a leitura. Consulte-a novamente antes de tentar.")
    if not all(isinstance(value, str) and value.strip() for value in
               (scope, name, platform, session_id, source_path, purpose)):
        raise TopologyError("Projeto, nome, plataforma, sessão, caminho e motivo são obrigatórios.")
    if any(len(value) > limit for value, limit in ((scope, 120), (name, 300), (platform, 100),
                                                   (session_id, 200), (source_path, 1000),
                                                   (purpose, 2000))):
        raise TopologyError("Um dos campos da fonte histórica excede o tamanho permitido.")
    if find_credentials("\n".join((name, platform, session_id, source_path, purpose))):
        raise TopologyError("Os dados da fonte histórica parecem conter senha, token ou chave privada.")
    source = Path(source_path)
    if (source.is_absolute() or "\\" in source_path or source.parts[:1] != ("sources",)
            or source.suffix.lower() != ".md"
            or len(source.parts) < 3 or any(part in {"", ".", ".."} for part in source.parts)):
        raise TopologyError("A fonte precisa ser um caminho seguro dentro de sources/.")
    if source.parts[1] != scope:
        raise TopologyError("A fonte precisa estar dentro do mesmo escopo do perfil histórico.")
    source_file = root / source
    source_root = root / "sources"
    if source_root.is_symlink() or not source_root.resolve().is_relative_to(root.resolve()):
        raise TopologyError("A pasta sources/ precisa ficar dentro dos dados da OMM.")
    cursor = source_root
    for part in source.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise TopologyError("O caminho da fonte contém um link simbólico.")
    if not source_file.is_file() or not source_file.resolve().is_relative_to(source_root.resolve()):
        raise TopologyError("A fonte histórica precisa existir dentro de sources/ antes do vínculo.")
    config = load_topology(root)
    profiles = config.get("project_profiles", {})
    profile = profiles.get(scope)
    if not isinstance(profile, dict):
        raise TopologyError(f"Não há perfil registrado para o escopo {scope}.")
    entries = profile.setdefault("historical_sources", [])
    if not isinstance(entries, list):
        raise TopologyError("historical_sources precisa ser uma lista.")
    if any(item.get("session_id") == session_id or item.get("path") == source_path
           for item in entries if isinstance(item, dict)):
        raise TopologyError("Essa sessão ou fonte já está registrada na topologia.")
    entries.append({"name": name.strip(), "platform": platform.strip(),
                    "session_id": session_id.strip(), "path": source_path,
                    "purpose": purpose.strip()})
    encoded = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".omm-topology-",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        if hashlib.sha256(path.read_bytes()).hexdigest() != old_digest:
            raise TopologyError("A topologia mudou durante a gravação. Nada foi atualizado.")
        os.chmod(temporary_name, path.stat().st_mode & 0o777)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"status": "added", "scope": scope, "session_id": session_id,
            "path": source_path, "sha256": hashlib.sha256(encoded).hexdigest()}


def load_topology(root: Path) -> dict:
    """Load and validate portable single-session subagent configuration."""
    path = root / "memory" / "agent-topology.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TopologyError(f"Missing agent topology: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TopologyError(f"Invalid JSON in {path}: {exc}") from exc

    if config.get("schema_version") != 1:
        raise TopologyError("Unsupported agent topology schema_version")
    if config.get("project_session_model") != "multiple_workflow_sessions_shared_omm":
        raise TopologyError("project_session_model must allow multiple workflow sessions to share OMM")
    if config.get("workflow_session_model") != "single_parent_with_subagents":
        raise TopologyError("workflow_session_model must be single_parent_with_subagents")
    coordinator = config.get("coordinator_role")
    children = config.get("subagents")
    if not isinstance(coordinator, str) or not coordinator.strip():
        raise TopologyError("coordinator_role must be a non-empty string")
    if not isinstance(children, list) or not children:
        raise TopologyError("subagents must be a non-empty list")

    names = set()
    for child in children:
        if not isinstance(child, dict):
            raise TopologyError("each subagent must be an object")
        name = child.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise TopologyError("subagent names must be non-empty and unique")
        names.add(name)
        if child.get("reports_to") != coordinator:
            raise TopologyError(f"subagent {name!r} must report to coordinator {coordinator!r}")
        role_file = child.get("role_file")
        if not isinstance(role_file, str) or not role_file:
            raise TopologyError(f"subagent {name!r} requires a role_file")
        role_path = (root / role_file).resolve()
        if root.resolve() not in role_path.parents or not role_path.is_file():
            raise TopologyError(f"role_file for {name!r} must resolve to a file inside the project")
    if config.get("shared_memory") != "omm_canonical":
        raise TopologyError("subagents must use OMM canonical shared memory")

    profiles = config.get("project_profiles", {})
    if not isinstance(profiles, dict):
        raise TopologyError("project_profiles must be an object")
    for scope, profile in profiles.items():
        if not isinstance(scope, str) or not isinstance(profile, dict):
            raise TopologyError("each project profile must have a project id and an object")
        if profile.get("shared_scope", scope) != scope:
            raise TopologyError(f"project profile {scope!r} must use its own OMM scope")
        agents = profile.get("agents", [])
        if not isinstance(agents, list) or not agents:
            raise TopologyError(f"project profile {scope!r} must define one or more agents")
        names = set()
        parents = {profile.get("coordinator_role", coordinator)}
        for agent in agents:
            if not isinstance(agent, dict) or not isinstance(agent.get("name"), str):
                raise TopologyError(f"project profile {scope!r} contains an invalid agent")
            name = agent["name"]
            if name in names or not name.strip():
                raise TopologyError(f"agent names in project profile {scope!r} must be unique")
            names.add(name)
        parents.update(names)
        for agent in agents:
            role_file = agent.get("role_file")
            if not isinstance(role_file, str) or not role_file:
                raise TopologyError(f"agent {agent['name']!r} requires a role_file")
            role_path = (root / role_file).resolve()
            if root.resolve() not in role_path.parents or not role_path.is_file():
                raise TopologyError(f"role_file for project agent {agent['name']!r} must resolve inside the project")
            if agent.get("reports_to", profile.get("coordinator_role", coordinator)) not in parents:
                raise TopologyError(f"agent {agent['name']!r} has an unknown parent")
        if profile.get("shared_memory", "omm_canonical") != "omm_canonical":
            raise TopologyError(f"project profile {scope!r} must use canonical OMM memory")
    return config
