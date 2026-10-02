"""Reviewable import pipeline for normalized agent conversation histories."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from .models import MemoryRecord, now_iso
from .store import CanonicalStore

FORMAT = "omm-history-v1"


class HistoryImportError(ValueError):
    pass


def _imports_dir(root: Path) -> Path:
    path = root / "memory" / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _import_path(root: Path, import_id: str) -> Path:
    if not import_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in import_id):
        raise HistoryImportError("invalid import ID")
    return _imports_dir(root) / f"{import_id}.json"


def _validate_bundle(payload: bytes) -> dict:
    try:
        bundle = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HistoryImportError(f"history bundle must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(bundle, dict):
        raise HistoryImportError("history bundle must be a JSON object")
    if bundle.get("format") != FORMAT:
        raise HistoryImportError(f"format must be {FORMAT}")
    for field in ("platform", "session_id", "source_ref"):
        if not isinstance(bundle.get(field), str) or not bundle[field].strip():
            raise HistoryImportError(f"{field} must be a non-empty string")
    records = bundle.get("records")
    if not isinstance(records, list) or not records:
        raise HistoryImportError("records must be a non-empty list of reviewed memory candidates")
    for index, value in enumerate(records):
        if not isinstance(value, dict):
            raise HistoryImportError(f"records[{index}] must be an object")
        for field in ("kind", "title", "content"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise HistoryImportError(f"records[{index}].{field} must be a non-empty string")
        for field in ("source", "scope", "confidence", "role"):
            if field in value and value[field] is not None and not isinstance(value[field], str):
                raise HistoryImportError(f"records[{index}].{field} must be a string")
        for field in ("evidence", "tags"):
            if field in value and (not isinstance(value[field], list) or not all(isinstance(x, str) for x in value[field])):
                raise HistoryImportError(f"records[{index}].{field} must be a list of strings")
        try:
            candidate = MemoryRecord(
                kind=value["kind"], title=value["title"], content=value["content"],
                source=value.get("source") or bundle["source_ref"],
                confidence=value.get("confidence"), evidence=value.get("evidence", []),
                tags=value.get("tags", []), role=value.get("role"),
                scope=value.get("scope", "default"),
            )
            candidate.validate()
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoryImportError(f"invalid records[{index}]: {exc}") from exc
    workstream = bundle.get("workstream_id")
    if workstream is not None and (not isinstance(workstream, str) or not workstream.strip()):
        raise HistoryImportError("workstream_id must be a non-empty string when supplied")
    return bundle


def stage_history(root: Path, payload: bytes) -> dict:
    bundle = _validate_bundle(payload)
    root = root.resolve()
    store = CanonicalStore(root)
    store.initialize()
    if bundle.get("workstream_id") and not any(w["id"] == bundle["workstream_id"] for w in store.workstreams()):
        raise HistoryImportError(f"unknown workstream: {bundle['workstream_id']}; create it first")
    staged = {
        "id": str(uuid.uuid4()), "format": FORMAT, "platform": bundle["platform"],
        "session_id": bundle["session_id"], "source_ref": bundle["source_ref"],
        "workstream_id": bundle.get("workstream_id"), "status": "pending_review",
        "staged_at": now_iso(), "records": bundle["records"],
    }
    path = _import_path(root, staged["id"])
    path.write_text(json.dumps(staged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return staged


def list_history_imports(root: Path) -> list[dict]:
    paths = sorted(_imports_dir(root).glob("*.json"))
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def show_history_import(root: Path, import_id: str) -> dict:
    path = _import_path(root, import_id)
    if not path.is_file():
        raise HistoryImportError(f"unknown import: {import_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def accept_history(root: Path, import_id: str, approved_by: str) -> list[MemoryRecord]:
    if not approved_by.strip():
        raise HistoryImportError("approved_by cannot be empty")
    root = root.resolve()
    path = _import_path(root, import_id)
    if not path.is_file():
        raise HistoryImportError(f"unknown import: {import_id}")
    staged = json.loads(path.read_text(encoding="utf-8"))
    if staged.get("status") != "pending_review":
        raise HistoryImportError(f"import is not pending review: {import_id}")
    source_ref = staged["source_ref"]
    records = []
    store = CanonicalStore(root)
    for value in staged["records"]:
        evidence = list(value.get("evidence", []))
        evidence.append(source_ref)
        record = MemoryRecord(
            kind=value["kind"], title=value["title"], content=value["content"],
            source=value.get("source") or source_ref, status="active", created_by=approved_by,
            confidence=value.get("confidence"), evidence=evidence,
            tags=list(value.get("tags", [])) + [f"source-platform:{staged['platform']}"],
            role=value.get("role"), session_id=staged["session_id"],
            workstream_id=staged.get("workstream_id"), scope=value.get("scope", "default"),
        )
        store.append(record)
        records.append(record)
    staged.update({"status": "accepted", "accepted_at": now_iso(), "accepted_by": approved_by,
                   "record_ids": [record.id for record in records]})
    path.write_text(json.dumps(staged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records
