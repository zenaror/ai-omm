"""Small, dependency-free web screen for managing a trusted local OMM."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import binascii
from datetime import datetime
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit

from .service import OMM
from .topology import TopologyError, load_topology
from .sync import SyncError, sync_with_backup
from .backup_worker import BackupError


def _json_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            result.append(json.loads(line))
    return result


def _skills(root: Path) -> list[dict]:
    result = []
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        content = path.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        description = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        scope = re.search(r"^\s+scope:\s*(.+)$", content, re.MULTILINE)
        result.append({
            "name": name.group(1).strip() if name else path.parent.name,
            "scope": scope.group(1).strip().strip("\"'") if scope else "não definido",
            "description": description.group(1).strip() if description else "",
            "source": path.relative_to(root).as_posix(),
        })
    return result


def dashboard_data(omm: OMM, query: str = "", scope: str = "", show_archived: bool = False) -> dict:
    """Return canonical data for the screen; SQLite is used only to find active notes."""
    with omm.operation_lock():
        all_records = list(omm.store.records())
        active = [r for r in all_records if r.status == "active"]
        removed = [r for r in all_records if r.status != "active"]
        source_root = omm.root / "sources"
        source_scopes = {p.name for p in source_root.iterdir() if p.is_dir()} if source_root.exists() else set()
        scopes = sorted({r.scope for r in all_records} | source_scopes | {"global"})
        source_hits = []
        if query.strip():
            matched_ids = {r.id for r in omm.search(query, 200, scopes=[scope] if scope else None)}
            source_hits = [
                {"id": hit.id, "scope": hit.scope, "heading": hit.heading,
                 "content": hit.content, "source": hit.source}
                for hit in omm.search_sources(query, 8, scopes=[scope] if scope else None)
            ]
            if show_archived:
                needle = query.casefold()
                archived_matches = {r.id for r in removed if needle in (r.title + " " + r.content + " " + r.source).casefold()}
                matched_ids |= archived_matches
            chosen = [r for r in all_records if r.id in matched_ids]
        else:
            chosen = active + (removed if show_archived else [])
        if scope:
            chosen = [r for r in chosen if r.scope == scope]
        chosen.sort(key=lambda r: r.created_at, reverse=True)
        records = [{
            "id": r.id, "kind": r.kind, "title": r.title, "content": r.content,
            "source": r.source, "evidence": r.evidence, "scope": r.scope,
            "status": r.status, "created_at": r.created_at, "tags": r.tags,
        } for r in chosen[:200]]

        kinds: dict[str, int] = {}
        by_scope: dict[str, int] = {}
        for r in active:
            kinds[r.kind] = kinds.get(r.kind, 0) + 1
            by_scope[r.scope] = by_scope.get(r.scope, 0) + 1
        memory_root = omm.root / "memory"
        memory_bytes = sum(p.stat().st_size for p in memory_root.rglob("*") if p.is_file()) if memory_root.exists() else 0
        source_bytes = sum(p.stat().st_size for p in source_root.rglob("*") if p.is_file()) if source_root.exists() else 0
        index_path = omm.root / ".omm" / "index.sqlite3"
        index_bytes = index_path.stat().st_size if index_path.exists() else 0
        index_updated_at = datetime.fromtimestamp(index_path.stat().st_mtime).astimezone().isoformat(timespec="minutes") if index_path.exists() else None
        canonical_files = [path for base in (memory_root, source_root) if base.exists()
                           for path in base.rglob("*") if path.is_file() and not path.is_symlink()]
        newest_canonical_change = max((path.stat().st_mtime for path in canonical_files), default=0)
        index_current = bool(index_path.exists() and index_path.stat().st_mtime >= newest_canonical_change)
        backup_commit_at = None
        try:
            backup_commit_at = subprocess.run(
                ["git", "-C", str(omm.root), "log", "-1", "--format=%cI", "--", "memory", "skills", "sources"],
                capture_output=True, text=True, check=False, timeout=2,
            ).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
        skills = _skills(omm.root)
        try:
            topology = load_topology(omm.root)
        except TopologyError:
            topology = {}
        profiles = topology.get("project_profiles", {})
        profile = profiles.get(scope, {}) if scope else {}
        agents = [{**agent, "scope": scope} for agent in profile.get("agents", [])]
        if scope and not agents:
            for agent in topology.get("subagents", []):
                activation = str(agent.get("activation", ""))
                normalized_scope = scope.replace("-", "_")
                applies = normalized_scope in activation or scope in agent.get("name", "")
                if applies:
                    agents.append({**agent, "scope": scope})
        applicable_skills = [item for item in skills
                             if item["scope"] in {"global", "cross-project-domain", f"project:{scope}"}]
        shared_knowledge = profile.get("shared_project_knowledge", [])
        project_sources = []
        selected_source_root = source_root / scope if scope in scopes and scope and source_root.exists() else None
        if selected_source_root and selected_source_root.is_dir():
            for path in sorted(selected_source_root.rglob("*.md")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(omm.root).as_posix()
                with path.open("r", encoding="utf-8") as source_file:
                    preview = source_file.read(4096)
                first_heading = next(
                    (line.strip()[2:].strip() for line in preview.splitlines()
                     if line.lstrip().startswith("# ")),
                    path.stem.replace("-", " "),
                )
                project_sources.append({"path": relative, "title": first_heading})
        scoped_records = [r for r in active if r.scope == scope] if scope else []
        organization = {
            "scope": scope,
            "display_name": profile.get("display_name", scope or "Todos os projetos"),
            "agents": agents,
            "skills": applicable_skills if scope else skills,
            "shared_project_knowledge": shared_knowledge,
            "sources": project_sources,
            "active_memory_count": len(scoped_records),
            "unknown_count": sum(r.kind == "unknown" for r in scoped_records),
            "handoff": next((h for h in reversed(_json_lines(omm.store.handoffs_path))
                             if scope and h.get("scope", "default") == scope), None),
        }
        scope_labels = {name: profiles.get(name, {}).get(
            "display_name", name.replace("-", " ").title())
            for name in scopes}
        skill_bytes = sum((omm.root / item["source"]).stat().st_size for item in skills)
        policies = _json_lines(omm.store.policies_path)
        handoffs = _json_lines(omm.store.handoffs_path)
        scoped_handoffs = [h for h in handoffs if not scope or h.get("scope", "default") == scope]
        return {
            "summary": {
                "active_count": len(active), "archived_count": len(removed),
                "unknown_count": sum(r.kind == "unknown" for r in active),
                "memory_bytes": memory_bytes, "skill_count": len(skills),
                "skill_bytes": skill_bytes, "source_bytes": source_bytes,
                "index_bytes": index_bytes, "index_updated_at": index_updated_at,
                "index_current": index_current,
                "backup_commit_at": backup_commit_at,
                "source_chunk_count": omm.source_chunk_count(), "policy_count": len(policies),
                "handoff_count": len(handoffs), "agent_count": len(agents),
                "kinds": kinds, "scopes": by_scope,
            },
            "scopes": scopes,
            "scope_labels": scope_labels,
            "records": records,
            "source_hits": source_hits,
            "organization": organization,
            "policies": [p for p in policies if not scope or p.get("scope", "default") == scope],
            "handoff": scoped_handoffs[-1] if scoped_handoffs else None,
            "skills": skills,
            "agents": agents,
        }


def make_handler(omm: OMM):
    web_root = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        server_version = "OMMWeb/0.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: dict) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            password = os.getenv("OMM_WEB_PASSWORD", "")
            if not password:
                return True
            expected_user = os.getenv("OMM_WEB_USERNAME", "omm")
            supplied = self.headers.get("Authorization", "")
            try:
                scheme, encoded = supplied.split(" ", 1)
                if scheme.lower() != "basic":
                    raise ValueError("not basic auth")
                username, candidate = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
            except (ValueError, UnicodeError, binascii.Error):
                username, candidate = "", ""
            valid_user = hmac.compare_digest(username.encode(), expected_user.encode())
            valid_password = hmac.compare_digest(candidate.encode(), password.encode())
            if valid_user and valid_password:
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="OMM", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._send(200, (web_root / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            if parsed.path in {"/app.js", "/styles.css"}:
                content_type = "text/javascript; charset=utf-8" if parsed.path.endswith(".js") else "text/css; charset=utf-8"
                self._send(200, (web_root / parsed.path.lstrip("/")).read_bytes(), content_type)
                return
            if parsed.path == "/api/dashboard":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0][:300]
                scope = params.get("scope", [""])[0][:120]
                archived = params.get("archived", ["0"])[0] == "1"
                try:
                    self._send_json(200, dashboard_data(omm, query, scope, archived))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self._send_json(500, {"error": f"Não foi possível ler a memória: {exc}"})
                return
            self._send_json(404, {"error": "Página não encontrada."})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            if urlsplit(self.path).path == "/api/sync":
                try:
                    with omm.operation_lock():
                        result = sync_with_backup(
                            omm.root,
                            os.getenv("OMM_GIT_BACKUP_REMOTE", "origin"),
                            os.getenv("OMM_GIT_BACKUP_BRANCH", "main"),
                            os.getenv("OMM_GIT_BACKUP_REPOSITORY_URL", "").strip(),
                            os.getenv("OMM_GIT_BACKUP_USERNAME", "x-access-token"),
                            os.getenv("OMM_GIT_BACKUP_TOKEN", ""),
                            os.getenv("OMM_GIT_BACKUP_AUTHOR_NAME", "OMM Backup"),
                            os.getenv("OMM_GIT_BACKUP_AUTHOR_EMAIL", "omm@localhost"),
                        )
                        omm.rebuild()
                    self._send_json(200, {"message": result})
                except (SyncError, BackupError, OSError, ValueError) as exc:
                    self._send_json(409, {"error": str(exc)})
                return
            match = re.fullmatch(r"/api/records/([A-Za-z0-9-]+)/status", urlsplit(self.path).path)
            if not match:
                self._send_json(404, {"error": "Ação não encontrada."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 2048:
                    raise ValueError("pedido inválido")
                payload = json.loads(self.rfile.read(size))
                status = payload.get("status")
                record = omm.set_record_status(unquote(match.group(1)), status)
                self._send_json(200, {"id": record.id, "status": record.status})
            except KeyError:
                self._send_json(404, {"error": "Essa anotação não existe mais."})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc) or "Pedido inválido."})

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            match = re.fullmatch(r"/api/records/([A-Za-z0-9-]+)", urlsplit(self.path).path)
            if not match:
                self._send_json(404, {"error": "Ação não encontrada."})
                return
            try:
                record = omm.delete_retracted_record(unquote(match.group(1)))
                self._send_json(200, {"id": record.id, "deleted": True})
            except KeyError:
                self._send_json(404, {"error": "Essa anotação não existe mais."})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            print("OMM web: " + format % args, flush=True)

    return Handler


def start_dashboard(omm: OMM, host: str = "127.0.0.1", port: int = 8001) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(omm))
    import threading
    thread = threading.Thread(target=server.serve_forever, name="omm-web", daemon=True)
    thread.start()
    return server


def main() -> None:
    from .service import OMM
    root = Path(os.getenv("OMM_DATA_ROOT", "/data"))
    host = os.getenv("OMM_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("OMM_WEB_PORT", "8001"))
    omm = OMM(root)
    omm.init()
    server = ThreadingHTTPServer((host, port), make_handler(omm))
    print(f"Painel OMM disponível em http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
