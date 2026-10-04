"""Rebuildable full-text retrieval over source documents stored with OMM data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile


MAX_CHUNK_CHARS = 2800
MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")


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


def import_source_markdown(root: Path, scope: str, relative_path: str,
                           content: str) -> dict[str, object]:
    """Store one Markdown source below the canonical data root without overwriting."""
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise ValueError("Escopo inválido. Use um nome simples, como pkhex-linux.")
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError("Informe um caminho relativo em formato Unix, como docs/PORTING.md.")
    relative = Path(relative_path)
    if (relative.is_absolute() or relative.suffix.lower() != ".md"
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(ord(char) < 32 for char in relative_path)):
        raise ValueError("O caminho precisa ser um Markdown relativo, sem '..' ou pastas acima da fonte.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("O conteúdo Markdown está vazio.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("O conteúdo precisa estar em UTF-8 válido.") from exc
    if len(encoded) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("A fonte excede o limite de 20 MiB.")

    data_root = root.resolve()
    source_root = data_root / "sources"
    if source_root.is_symlink():
        raise ValueError("A pasta sources não pode ser um link simbólico.")
    destination = source_root / scope / relative
    if source_root.exists() and not source_root.is_dir():
        raise ValueError("O caminho sources existe, mas não é uma pasta.")

    # Check existing components before creating directories; no import may escape
    # the data root through a symlink planted in an otherwise valid path.
    cursor = source_root
    for part in (scope, *relative.parts[:-1]):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("O caminho de destino contém um link simbólico.")
    source_root.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source_root.resolve() != source_root or source_root.resolve().parent != data_root:
        raise ValueError("A pasta sources precisa permanecer dentro da pasta de dados da OMM.")
    if not destination.parent.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("O caminho de destino precisa permanecer dentro de sources/.")
    if destination.is_symlink():
        raise ValueError("O arquivo de destino é um link simbólico e não foi alterado.")

    digest = hashlib.sha256(encoded).hexdigest()
    if destination.exists():
        existing = destination.read_bytes()
        if existing == encoded:
            return {"status": "already_present", "scope": scope,
                    "source": destination.relative_to(data_root).as_posix(),
                    "bytes": len(encoded), "sha256": digest}
        raise FileExistsError(
            "Já existe uma fonte diferente nesse caminho. Escolha outro nome; a OMM não sobrescreveu o original."
        )

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".omm-import-",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"status": "imported", "scope": scope,
            "source": destination.relative_to(data_root).as_posix(),
            "bytes": len(encoded), "sha256": digest}


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
