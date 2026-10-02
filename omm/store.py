from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone

from .models import MemoryRecord


class CanonicalStore:
    """Plain-file canonical store. It intentionally has no database dependency."""

    def __init__(self, root: Path):
        self.root = root
        self.memory = root / "memory"
        self.records_path = self.memory / "records.jsonl"
        self.policies_path = self.memory / "policies.jsonl"
        self.handoffs_path = self.memory / "handoffs.jsonl"
        self.workstreams_path = self.memory / "workstreams.jsonl"
        self.state_path = self.memory / "state.json"

    def initialize(self) -> None:
        (self.memory / "roles").mkdir(parents=True, exist_ok=True)
        (self.memory / "imports").mkdir(parents=True, exist_ok=True)
        self.records_path.touch(exist_ok=True)
        self.policies_path.touch(exist_ok=True)
        self.handoffs_path.touch(exist_ok=True)
        self.workstreams_path.touch(exist_ok=True)
        if not self.state_path.exists():
            self.write_state({
                "schema_version": 1, "status": "not_started", "summary": "",
                "blockers": [], "open_questions": [], "next_actions": [],
                "updated_at": None, "updated_by": None,
            })

    def append(self, record: MemoryRecord) -> None:
        record.validate()
        self.initialize()
        with self.records_path.open("a", encoding="utf-8") as stream:
            stream.write(record.to_json() + "\n")

    def update_record_status(self, record_id: str, status: str) -> MemoryRecord:
        """Change one record's status without discarding its provenance."""
        if status not in {"active", "retracted"}:
            raise ValueError("status must be active or retracted")
        records = list(self.records())
        for record in records:
            if record.id == record_id:
                record.status = status
                record.validate()
                lines = [item.to_json() for item in records]
                temporary = self.records_path.with_suffix(".jsonl.tmp")
                temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                temporary.replace(self.records_path)
                return record
        raise KeyError(record_id)

    def delete_retracted_record(self, record_id: str) -> MemoryRecord:
        """Permanently remove an already retracted record from current files."""
        records = list(self.records())
        target = next((record for record in records if record.id == record_id), None)
        if target is None:
            raise KeyError(record_id)
        if target.status != "retracted":
            raise ValueError("archive the record before permanently deleting it")
        remaining = [record for record in records if record.id != record_id]
        temporary = self.records_path.with_suffix(".jsonl.tmp")
        temporary.write_text("\n".join(item.to_json() for item in remaining) +
                             ("\n" if remaining else ""), encoding="utf-8")
        temporary.replace(self.records_path)
        return target

    def records(self) -> Iterable[MemoryRecord]:
        if not self.records_path.exists():
            return
        with self.records_path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    try:
                        yield MemoryRecord.from_json(line)
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"Invalid record at {self.records_path}:{number}: {exc}") from exc

    def read_state(self, workstream_id: str | None = None, scope: str | None = None) -> dict:
        if workstream_id and self.handoffs_path.exists():
            matching = []
            for line in self.handoffs_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("workstream_id") == workstream_id and (scope is None or item.get("scope", "default") == scope):
                        matching.append(item)
            if matching:
                return matching[-1]
        elif scope and scope != "default" and self.handoffs_path.exists():
            matching = []
            for line in self.handoffs_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("scope", "default") == scope:
                        matching.append(item)
            if matching:
                return matching[-1]
        if scope and scope not in {"default", "global"}:
            return {"schema_version": 1, "scope": scope, "status": "not_started", "summary": "",
                    "blockers": [], "open_questions": [], "next_actions": [],
                    "updated_at": None, "updated_by": None, "session_id": None, "workstream_id": None}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def append_handoff(self, handoff: dict) -> None:
        self.initialize()
        with self.handoffs_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(handoff, ensure_ascii=False, sort_keys=True) + "\n")

    def create_workstream(self, workstream_id: str, title: str, created_by: str, status: str = "active") -> dict:
        if not workstream_id.strip() or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in workstream_id):
            raise ValueError("workstream id may contain only letters, numbers, hyphens, and underscores")
        if status not in {"active", "completed", "archived"}:
            raise ValueError("workstream status must be active, completed, or archived")
        workstreams = list(self.workstreams())
        if any(item["id"] == workstream_id for item in workstreams):
            raise ValueError(f"workstream already exists: {workstream_id}")
        item = {"id": workstream_id, "title": title, "status": status,
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "created_by": created_by}
        self.initialize()
        with self.workstreams_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        return item

    def workstreams(self) -> list[dict]:
        if not self.workstreams_path.exists():
            return []
        return [json.loads(line) for line in self.workstreams_path.read_text(encoding="utf-8").splitlines() if line.strip()]
