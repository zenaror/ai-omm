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


def _managed_source_path(root: Path, source: str) -> Path:
    """Resolve an existing Markdown file in sources without following links."""
    if not isinstance(source, str) or not source or "\\" in source:
        raise ValueError("Informe o caminho completo dentro de sources/, em formato Unix.")
    relative = Path(source)
    if (relative.is_absolute() or relative.suffix.lower() != ".md"
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(ord(char) < 32 for char in source)):
        raise ValueError("O caminho precisa apontar para um Markdown dentro de sources/.")
    data_root = root.resolve()
    source_root = data_root / "sources"
    target = data_root / relative
    if relative.parts[:1] != ("sources",) or len(relative.parts) < 3:
        raise ValueError("O caminho precisa ter o formato sources/<escopo>/<arquivo>.md.")
    if (not source_root.is_dir() or source_root.is_symlink()
            or source_root.resolve().parent != data_root):
        raise FileNotFoundError("A pasta sources/ não existe ou não é segura.")
    cursor = source_root
    for part in relative.parts[1:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("O caminho da fonte contém um link simbólico.")
    if target.is_symlink():
        raise ValueError("A fonte é um link simbólico e não foi alterada.")
    if not target.is_file() or not target.resolve().is_relative_to(source_root.resolve()):
        raise FileNotFoundError("Essa fonte não existe dentro de sources/.")
    return target


def read_source_markdown(root: Path, source: str, start_line: int,
                         end_line: int) -> dict[str, object]:
    """Read a bounded source excerpt and expose the full-file digest for guarded edits."""
    if start_line < 1 or end_line < start_line or end_line - start_line >= 80:
        raise ValueError("Escolha um trecho de até 80 linhas.")
    target = _managed_source_path(root, source)
    raw = target.read_bytes()
    if len(raw) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("A fonte é grande demais para abrir por esta ferramenta.")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("A fonte precisa estar em UTF-8 válido.") from exc
    if start_line > len(lines):
        raise ValueError("A linha inicial não existe nessa fonte.")
    selected = "\n".join(lines[start_line - 1:end_line])
    return {
        "source": target.relative_to(root.resolve()).as_posix(),
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "content": selected[:8000],
        "truncated": len(selected) > 8000,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def replace_source_markdown(root: Path, source: str, content: str,
                            expected_sha256: str) -> dict[str, object]:
    """Replace one source only if its current content still matches the supplied hash."""
    target = _managed_source_path(root, source)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256 or ""):
        raise ValueError("Informe o sha256 atual da fonte para confirmar a edição.")
    current = target.read_bytes()
    current_digest = hashlib.sha256(current).hexdigest()
    if current_digest != expected_sha256.lower():
        raise ValueError("A fonte mudou desde a leitura. Leia o sha256 atual antes de tentar de novo.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("O novo conteúdo Markdown está vazio.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("O conteúdo precisa estar em UTF-8 válido.") from exc
    if len(encoded) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("A fonte excede o limite de 20 MiB.")
    digest = hashlib.sha256(encoded).hexdigest()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".omm-source-",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, target.stat().st_mode & 0o777)
        # Recheck immediately before replacement to prevent silent lost updates.
        if hashlib.sha256(target.read_bytes()).hexdigest() != current_digest:
            raise ValueError("A fonte mudou durante a edição. Nenhuma alteração foi aplicada.")
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"status": "updated", "source": source,
            "bytes": len(encoded), "sha256": digest}


def redact_source_spans_markdown(root: Path, source: str,
                                 spans: list[dict[str, int | str]],
                                 expected_sha256: str) -> dict[str, object]:
    """Redact selected 1-based character spans without returning source text."""
    target = _managed_source_path(root, source)
    if not isinstance(expected_sha256, str) or not re.fullmatch(
            r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("Informe o sha256 atual da fonte para confirmar a redação.")
    if not isinstance(spans, list) or not spans or len(spans) > 500:
        raise ValueError("Informe de 1 a 500 trechos para redigir.")

    current = target.read_bytes()
    current_digest = hashlib.sha256(current).hexdigest()
    if current_digest != expected_sha256.lower():
        raise ValueError("A fonte mudou desde a leitura. Leia o sha256 atual antes de tentar de novo.")
    if len(current) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("A fonte excede o limite de 20 MiB.")
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("A fonte precisa estar em UTF-8 válido.") from exc

    lines = text.splitlines(keepends=True)
    by_line: dict[int, list[tuple[int, int, int]]] = {}
    for item in spans:
        if not isinstance(item, dict):
            raise ValueError("Cada trecho precisa informar linha, colunas e tamanho esperado.")
        line_number = item.get("line")
        start_column = item.get("start_column")
        end_column = item.get("end_column")
        expected_length = item.get("expected_length")
        expected_text_sha256 = item.get("expected_text_sha256")
        values = (line_number, start_column, end_column, expected_length)
        if any(type(value) is not int for value in values):
            raise ValueError("Linha, colunas e tamanho esperado precisam ser números inteiros.")
        if not isinstance(expected_text_sha256, str) or not re.fullmatch(
                r"[a-fA-F0-9]{64}", expected_text_sha256):
            raise ValueError("Informe o sha256 do trecho para confirmar cada redação.")
        if (line_number < 1 or line_number > len(lines) or start_column < 1
                or end_column < start_column or expected_length != end_column - start_column + 1):
            raise ValueError("Um trecho tem linha ou colunas inválidas.")
        line = lines[line_number - 1]
        if line.endswith("\r\n"):
            body = line[:-2]
        elif line.endswith(("\n", "\r")):
            body = line[:-1]
        else:
            body = line
        if end_column > len(body):
            raise ValueError("As colunas informadas passam do fim da linha.")
        selected = body[start_column - 1:end_column]
        if len(selected) != expected_length:
            raise ValueError("O tamanho do trecho não confere.")
        if not selected.strip() or selected == "[DADO PESSOAL REDIGIDO]":
            raise ValueError("O trecho está vazio ou já foi redigido.")
        if hashlib.sha256(selected.encode("utf-8")).hexdigest() != expected_text_sha256.lower():
            raise ValueError("O trecho mudou desde a leitura. Nenhuma alteração foi aplicada.")
        by_line.setdefault(line_number, []).append(
            (start_column, end_column, expected_length)
        )

    marker = "[DADO PESSOAL REDIGIDO]"
    for line_number, ranges in by_line.items():
        ordered = sorted(ranges)
        previous_end = 0
        for start_column, end_column, _ in ordered:
            if start_column <= previous_end:
                raise ValueError("Os trechos de uma linha não podem se sobrepor.")
            previous_end = end_column
        line = lines[line_number - 1]
        if line.endswith("\r\n"):
            ending, body = "\r\n", line[:-2]
        elif line.endswith(("\n", "\r")):
            ending, body = line[-1], line[:-1]
        else:
            ending, body = "", line
        for start_column, end_column, _ in reversed(ordered):
            body = body[:start_column - 1] + marker + body[end_column:]
        lines[line_number - 1] = body + ending

    encoded = "".join(lines).encode("utf-8")
    if len(encoded) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("A fonte excede o limite de 20 MiB.")
    digest = hashlib.sha256(encoded).hexdigest()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".omm-redact-",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, target.stat().st_mode & 0o777)
        if hashlib.sha256(target.read_bytes()).hexdigest() != current_digest:
            raise ValueError("A fonte mudou durante a redação. Nenhuma alteração foi aplicada.")
        os.replace(temporary_name, target)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"status": "updated", "source": source,
            "previous_sha256": current_digest,
            "redacted_spans": len(spans), "redacted_lines": len(by_line),
            "bytes": len(encoded), "sha256": digest}


def delete_source_markdown(root: Path, source: str,
                           expected_sha256: str) -> dict[str, object]:
    """Delete one source only after a caller confirms its current SHA-256."""
    target = _managed_source_path(root, source)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256 or ""):
        raise ValueError("Informe o sha256 atual da fonte para confirmar a remoção.")
    current = target.read_bytes()
    digest = hashlib.sha256(current).hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("A fonte mudou desde a leitura. Leia o sha256 atual antes de tentar de novo.")
    target.unlink()
    return {"status": "deleted", "source": source,
            "bytes": len(current), "sha256": digest}


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
