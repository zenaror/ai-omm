"""Rebuildable full-text retrieval over source documents stored with OMM data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


MAX_CHUNK_CHARS = 2800
MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class SourceChunk:
    id: str
    scope: str
    path: str
    heading: str
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class SourceHit:
    id: str
    scope: str
    path: str
    heading: str
    start_line: int
    end_line: int
    content: str

    @property
    def source(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def read_source_chunks(root: Path, max_chars: int = MAX_CHUNK_CHARS) -> list[SourceChunk]:
    """Read only the explicit data-root/sources Markdown collection."""
    source_root = root / "sources"
    if not source_root.is_dir():
        return []
    chunks: list[SourceChunk] = []
    for path in sorted(source_root.rglob("*.md")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            continue
        relative = path.relative_to(root).as_posix()
        parts = path.relative_to(source_root).parts
        scope = parts[0] if len(parts) > 1 else "global"
        text = path.read_text(encoding="utf-8")
        headings: list[tuple[int, str]] = []
        current: list[tuple[int, str]] = []
        current_chars = 0

        def heading_text() -> str:
            return " / ".join(title for _, title in headings) or path.stem.replace("_", " ").replace("-", " ")

        def flush() -> None:
            nonlocal current, current_chars
            if not current:
                return
            body_parts: list[str] = []
            previous_line: int | None = None
            for line_no, part in current:
                if previous_line is not None and line_no != previous_line:
                    body_parts.append("\n")
                body_parts.append(part)
                previous_line = line_no
            content = "".join(body_parts)
            start_line, end_line = current[0][0], current[-1][0]
            identity = hashlib.sha256(
                f"{relative}:{start_line}:{content}".encode("utf-8")
            ).hexdigest()[:24]
            chunks.append(SourceChunk(identity, scope, relative, heading_text(),
                                      start_line, end_line, content))
            current = []
            current_chars = 0

        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
                flush()
                level = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped[level:].strip()
                headings = [(depth, title) for depth, title in headings if depth < level]
                headings.append((level, heading))
                continue
            if not stripped:
                continue
            # Long source lines are split for FTS but keep the original line number.
            parts = [line[i:i + max_chars] for i in range(0, len(line), max_chars)] or [line]
            for part in parts:
                extra = len(part) + (1 if current and current[-1][0] != number else 0)
                if current and current_chars + extra > max_chars:
                    flush()
                current.append((number, part))
                current_chars += extra
        flush()
    return chunks
