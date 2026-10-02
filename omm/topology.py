from __future__ import annotations

import json
from pathlib import Path


class TopologyError(ValueError):
    pass


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
