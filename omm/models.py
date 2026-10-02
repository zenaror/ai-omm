from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import uuid

KINDS = {
    "fact", "observation", "hypothesis", "unknown", "decision",
    "conclusion", "constraint", "contract",
}
STATUSES = {"active", "superseded", "retracted", "unverified"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class MemoryRecord:
    kind: str
    title: str
    content: str
    source: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "active"
    created_at: str = field(default_factory=now_iso)
    created_by: str = "human"
    confidence: str | None = None
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    role: str | None = None
    session_id: str | None = None
    workstream_id: str | None = None
    # Shared service records can belong to "global" or a project-specific scope.
    # Existing JSONL records without this field remain readable as "default".
    scope: str = "default"

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(KINDS))}")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(STATUSES))}")
        for name in ("title", "content", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.scope, str) or not self.scope.strip() or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./" for c in self.scope):
            raise ValueError("scope may contain only letters, numbers, dots, slashes, hyphens, and underscores")
        if not isinstance(self.evidence, list) or any(not isinstance(item, str) for item in self.evidence):
            raise ValueError("evidence must be a list of text references")
        if not isinstance(self.tags, list) or any(not isinstance(item, str) for item in self.tags):
            raise ValueError("tags must be a list of text labels")

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "MemoryRecord":
        record = cls(**json.loads(line))
        record.validate()
        return record
