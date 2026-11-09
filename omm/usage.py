"""Bounded, local MCP counters. Never persist arguments, results or error text."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import wraps
import inspect
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from time import perf_counter

from .errors import error_report


# Never store arbitrary exception codes, messages, arguments or class names.
ERROR_CODES = frozenset({"VERSION_CONFLICT", "UNKNOWN_WORKSTREAM", "PROFILE_NOT_FOUND",
                         "NOT_FOUND", "PERMISSION_DENIED", "ALREADY_EXISTS", "INVALID_ARGUMENT",
                         "TIMEOUT", "STORAGE_UNAVAILABLE", "INTERNAL_ERROR"})


class UsageRecorder:
    def __init__(self, root: Path):
        self.path = root / ".omm" / "usage.sqlite3"
        self.enabled = os.getenv("OMM_USAGE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on", "sim"}
        try:
            self.retention_days = max(1, min(365, int(os.getenv("OMM_USAGE_RETENTION_DAYS", "30"))))
        except ValueError:
            self.retention_days = 30
        self.write_failures = 0

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=1.0)
        db.execute("""CREATE TABLE IF NOT EXISTS usage_daily (
            day TEXT NOT NULL, tool TEXT NOT NULL, scope TEXT NOT NULL,
            calls INTEGER NOT NULL, failures INTEGER NOT NULL,
            input_chars INTEGER NOT NULL, output_chars INTEGER NOT NULL,
            total_ms REAL NOT NULL, max_ms REAL NOT NULL,
            PRIMARY KEY(day, tool, scope))""")
        db.execute("""CREATE TABLE IF NOT EXISTS usage_errors_daily (
            day TEXT NOT NULL, tool TEXT NOT NULL, scope TEXT NOT NULL,
            error_code TEXT NOT NULL, failures INTEGER NOT NULL,
            PRIMARY KEY(day, tool, scope, error_code))""")
        db.execute("""CREATE TABLE IF NOT EXISTS usage_source_failures_daily (
            day TEXT NOT NULL, tool TEXT NOT NULL, scope TEXT NOT NULL,
            error_code TEXT NOT NULL, path_key TEXT NOT NULL, path_kind TEXT NOT NULL,
            request_count INTEGER NOT NULL, failures INTEGER NOT NULL,
            PRIMARY KEY(day,tool,scope,error_code,path_key,path_kind,request_count))""")
        return db

    @staticmethod
    def _size(value) -> int:
        # No default=str: arbitrary object representations can be large or unsafe.
        if isinstance(value, list) and any(getattr(item, "type", None) == "image" for item in value):
            # MCP vision payloads are not text tokens. Count accompanying text
            # only; never stringify pixels/base64 as if they were prose.
            value = [item.text for item in value if getattr(item, "type", None) == "text"]
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _scope(arguments: dict) -> str:
        value = arguments.get("scope")
        if value is None and isinstance(arguments.get("requests"), list) and 1 <= len(arguments["requests"]) <= 8:
            scopes = {UsageRecorder._scope({"path": r.get("path")}) for r in arguments["requests"] if isinstance(r, dict)}
            value = scopes.pop() if len(scopes) == 1 else "_unspecified"
        if value is None:
            path = arguments.get("path", "")
            parts = path.split("/") if isinstance(path, str) else []
            value = parts[1] if len(parts) >= 3 and parts[0] == "sources" else "_unspecified"
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
            return "_unspecified"
        return value

    @staticmethod
    def _source_failure_detail(tool, arguments):
        if tool not in {"read_source", "read_source_batch", "read_artifact"}:
            return None
        requests = arguments.get("requests") if tool == "read_source_batch" else [{"path": arguments.get("path")}]
        if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
            return ("invalid", "invalid", 0)
        paths = []
        for item in requests:
            path = item.get("path") if isinstance(item, dict) else None
            if (not isinstance(path, str) or len(path) > 2048 or "\\" in path
                    or any(ord(c) < 32 for c in path) or not path.startswith("sources/")
                    or any(p in {"", ".", ".."} for p in path.split("/"))):
                return ("invalid", "invalid", len(requests))
            paths.append(path)
        # Correlation only: no filenames, paths, arguments or exception text retained.
        try:
            key = hashlib.sha256(json.dumps(paths, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        except UnicodeError:
            return ("invalid", "invalid", len(requests))
        return (key, "batch" if tool == "read_source_batch" else "single", len(paths))

    def _record(self, tool: str, scope: str, inputs: int, outputs: int,
                elapsed_ms: float, failed: bool, error_code: str | None = None, source_detail=None):
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self.retention_days - 1)).date().isoformat()
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM usage_daily WHERE day < ?", (cutoff,))
            db.execute("DELETE FROM usage_errors_daily WHERE day < ?", (cutoff,))
            db.execute("DELETE FROM usage_source_failures_daily WHERE day < ?", (cutoff,))
            known = db.execute("SELECT 1 FROM usage_daily WHERE scope=? LIMIT 1", (scope,)).fetchone()
            if not known and db.execute("SELECT COUNT(DISTINCT scope) FROM usage_daily").fetchone()[0] >= 128:
                scope = "_other"
            db.execute("""INSERT INTO usage_daily VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day,tool,scope) DO UPDATE SET
                calls=calls+1, failures=failures+excluded.failures,
                input_chars=input_chars+excluded.input_chars,
                output_chars=output_chars+excluded.output_chars,
                total_ms=total_ms+excluded.total_ms, max_ms=MAX(max_ms,excluded.max_ms)""",
                (now.date().isoformat(), tool, scope, 1, int(failed), inputs, outputs, elapsed_ms, elapsed_ms))
            if failed and error_code is not None:
                code = error_code if error_code in ERROR_CODES else "INTERNAL_ERROR"
                db.execute("""INSERT INTO usage_errors_daily VALUES(?,?,?,?,1)
                    ON CONFLICT(day,tool,scope,error_code) DO UPDATE SET failures=failures+1""",
                    (now.date().isoformat(), tool, scope, code))
                if source_detail is not None:
                    key, kind, count = source_detail
                    identity = (now.date().isoformat(), tool, scope, code, key, kind, count)
                    exists = db.execute("SELECT 1 FROM usage_source_failures_daily WHERE day=? AND tool=? AND scope=? AND error_code=? AND path_key=? AND path_kind=? AND request_count=?", identity).fetchone()
                    if not exists and db.execute("SELECT COUNT(*) FROM usage_source_failures_daily WHERE day=?", (identity[0],)).fetchone()[0] >= 200:
                        identity = (identity[0], "_overflow", "_other", "INTERNAL_ERROR", "overflow", "overflow", 0)
                    db.execute("""INSERT INTO usage_source_failures_daily VALUES(?,?,?,?,?,?,?,1)
                        ON CONFLICT(day,tool,scope,error_code,path_key,path_kind,request_count)
                        DO UPDATE SET failures=failures+1""", identity)

    def wrap(self, function):
        signature = inspect.signature(function)
        annotations = inspect.get_annotations(function, eval_str=True)

        @wraps(function)
        def measured(*args, **kwargs):
            if not self.enabled:
                return function(*args, **kwargs)
            started = perf_counter()
            arguments = signature.bind(*args, **kwargs)
            # Count arguments actually received, not Python defaults supplied locally.
            input_chars = self._size(arguments.arguments)
            scope_args = dict(arguments.arguments)
            arguments.apply_defaults()
            scope_args.update(arguments.arguments)
            scope = self._scope(scope_args)
            output_chars = 0
            failed = True
            error_code = None
            try:
                result = function(*args, **kwargs)
                # Memory reads use an ID rather than an input scope. Their
                # returned metadata identifies the project without retaining text.
                if scope == "_unspecified" and isinstance(result, dict) and "scope" in result:
                    scope = self._scope({"scope": result["scope"]})
                output_chars = self._size(result)
                failed = False
                return result
            except Exception as error:
                error_code = error_report(error, function.__name__)["error"]["code"]
                raise
            finally:
                elapsed_ms = (perf_counter() - started) * 1000
                try:
                    detail = self._source_failure_detail(function.__name__, scope_args) if failed else None
                    self._record(function.__name__, scope, input_chars, output_chars, elapsed_ms, failed, error_code, detail)
                except (OSError, sqlite3.Error):
                    # Optional telemetry must never turn a successful write into an error.
                    self.write_failures += 1

        # MCP inspects signatures/types; annotations must resolve in the original
        # module rather than this decorator's globals (Literal, etc.).
        measured.__annotations__ = annotations
        measured.__signature__ = signature.replace(
            parameters=[parameter.replace(annotation=annotations.get(name, parameter.annotation))
                        for name, parameter in signature.parameters.items()],
            return_annotation=annotations.get("return", signature.return_annotation))
        return measured

    def report(self, days: int = 7, scope: str | None = None) -> dict:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= self.retention_days:
            raise ValueError(f"Escolha de 1 a {self.retention_days} dias.")
        result = {"enabled": self.enabled, "days": days, "retention_days": self.retention_days,
                  "timezone": "UTC", "transport": "MCP", "write_failures_since_start": self.write_failures,
                  "measurement": "Caracteres JSON e tempo interno; não mede pixels/imagens, tokens cobrados, cache, rede ou descrições das ferramentas.",
                  "rows": [], "error_rows": [],
                  "source_error_rows": [], "source_errors_overflow": 0,
                  "source_error_measurement": "Amostra limitada a 200 grupos por dia, desde esta versão. path_key é SHA-256 do JSON compacto da lista ordenada de caminhos UTF-8; não guarda caminhos nem conteúdo. Lotes têm chave conjunta, não identificam qual item falhou. Não mede retrospectivamente falhas antigas.",
                  "error_measurement": "Categorias de falhas desde esta versão; falhas antigas não têm categoria. Sem mensagens de erro ou conteúdo das chamadas."}
        if not self.enabled or not self.path.exists():
            return result
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()
        sql = "SELECT tool,scope,SUM(calls),SUM(failures),SUM(input_chars),SUM(output_chars),SUM(total_ms),MAX(max_ms) FROM usage_daily WHERE day>=?"
        params = [cutoff]
        if scope is not None:
            if self._scope({"scope": scope}) != scope:
                raise ValueError("Informe um escopo simples de projeto.")
            sql += " AND scope=?"
            params.append(scope)
        sql += " GROUP BY tool,scope ORDER BY SUM(input_chars)+SUM(output_chars) DESC,scope,tool"
        with closing(self._connect()) as db:
            rows = list(db.execute(sql, params))
            result["group_count"] = len(rows)
            result["truncated"] = len(rows) > 50
            result["totals"] = {"calls": sum(row[2] for row in rows),
                "failures": sum(row[3] for row in rows), "input_chars": sum(row[4] for row in rows),
                "output_chars": sum(row[5] for row in rows)}
            for row in rows[:50]:
                tool, project, calls, failures, inputs, outputs, total_ms, max_ms = row
                result["rows"].append({"tool": tool, "scope": project,
                    "calls": calls, "failures": failures, "input_chars": inputs, "output_chars": outputs,
                    "mean_ms": round(total_ms / calls, 2), "max_ms": round(max_ms, 2)})
            # Include omitted groups in project totals and latency ranking.
            projects = {}
            for row in rows:
                project = projects.setdefault(row[1], {"scope": row[1], "calls": 0, "failures": 0,
                    "input_chars": 0, "output_chars": 0, "total_ms": 0})
                for key, value in zip(("calls", "failures", "input_chars", "output_chars", "total_ms"), row[2:7]):
                    project[key] += value
            result["projects"] = sorted(projects.values(), key=lambda p: p["input_chars"] + p["output_chars"], reverse=True)[:50]
            result["projects_truncated"] = len(projects) > 50
            result["slowest_rows"] = [{"tool": row[0], "scope": row[1], "calls": row[2],
                "total_ms": round(row[6], 2), "mean_ms": round(row[6] / row[2], 2), "max_ms": round(row[7], 2)}
                for row in sorted(rows, key=lambda r: r[6], reverse=True)[:10]]
            daily_sql = "SELECT day,SUM(calls),SUM(failures),SUM(input_chars),SUM(output_chars),SUM(total_ms) FROM usage_daily WHERE day>=?"
            if scope is not None:
                daily_sql += " AND scope=?"
            result["daily"] = [{"day": day, "calls": calls, "failures": failures,
                "input_chars": inputs, "output_chars": outputs, "total_ms": round(elapsed, 2)}
                for day, calls, failures, inputs, outputs, elapsed in db.execute(daily_sql + " GROUP BY day ORDER BY day", params)]
            error_sql = "SELECT tool,scope,error_code,SUM(failures) FROM usage_errors_daily WHERE day>=?"
            if scope is not None:
                error_sql += " AND scope=?"
            errors = list(db.execute(error_sql + " GROUP BY tool,scope,error_code ORDER BY SUM(failures) DESC,scope,tool,error_code", params))
            result["error_group_count"] = len(errors)
            result["errors_truncated"] = len(errors) > 50
            result["classified_failures"] = sum(row[3] for row in errors)
            result["unclassified_failures"] = max(0, result["totals"]["failures"] - result["classified_failures"])
            result["error_rows"] = [{"tool": tool, "scope": project, "code": code, "failures": count}
                                    for tool, project, code, count in errors[:50]]
            source_sql = "SELECT tool,scope,error_code,path_key,path_kind,request_count,SUM(failures) FROM usage_source_failures_daily WHERE day>=? AND path_kind!='overflow'"
            if scope is not None:
                source_sql += " AND scope=?"
            source_rows = list(db.execute(source_sql + " GROUP BY tool,scope,error_code,path_key,path_kind,request_count ORDER BY SUM(failures) DESC,path_key LIMIT 21", params))
            result["source_errors_truncated"] = len(source_rows) > 20
            result["source_error_rows"] = [dict(tool=t, scope=s, code=c, path_key=k, path_kind=kind, request_count=n, failures=f)
                                            for t,s,c,k,kind,n,f in source_rows[:20]]
            # Overflow cannot be assigned to a project reliably; label it globally.
            result["source_errors_overflow"] = db.execute("SELECT COALESCE(SUM(failures),0) FROM usage_source_failures_daily WHERE day>=? AND path_kind='overflow'", (cutoff,)).fetchone()[0]
        return result
