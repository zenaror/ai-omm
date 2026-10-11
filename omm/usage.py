"""Bounded, local MCP counters. Never persist arguments, results or error text."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
from time import perf_counter


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
        return db

    @staticmethod
    def _size(value) -> int:
        # No default=str: arbitrary object representations can be large or unsafe.
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _scope(arguments: dict) -> str:
        value = arguments.get("scope")
        if value is None:
            path = arguments.get("path", "")
            parts = path.split("/") if isinstance(path, str) else []
            value = parts[1] if len(parts) >= 3 and parts[0] == "sources" else "_unspecified"
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value):
            return "_unspecified"
        return value

    def _record(self, tool: str, scope: str, inputs: int, outputs: int,
                elapsed_ms: float, failed: bool):
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self.retention_days - 1)).date().isoformat()
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM usage_daily WHERE day < ?", (cutoff,))
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
            try:
                result = function(*args, **kwargs)
                output_chars = self._size(result)
                failed = False
                return result
            finally:
                elapsed_ms = (perf_counter() - started) * 1000
                try:
                    self._record(function.__name__, scope, input_chars, output_chars, elapsed_ms, failed)
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
                  "measurement": "Caracteres JSON e tempo interno; não mede tokens cobrados, cache, rede ou descrições das ferramentas.",
                  "rows": []}
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
        return result
