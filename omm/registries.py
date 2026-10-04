"""Safe writers for canonical OMM skills and agent-role files."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import tempfile

from .redaction import find_credentials

MAX_REGISTRY_FILE_BYTES = 1024 * 1024
_NAME_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _validate_content(content: str) -> bytes:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("O conteúdo não pode ficar vazio.")
    if find_credentials(content):
        raise ValueError("O texto parece conter uma senha, token ou chave. Remova o segredo antes de salvar.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("O conteúdo precisa estar em UTF-8 válido.") from exc
    if len(encoded) > MAX_REGISTRY_FILE_BYTES:
        raise ValueError("O arquivo excede o limite de 1 MiB.")
    return encoded


def _safe_directory(root: Path, relative: Path) -> Path:
    data_root = root.resolve()
    destination = data_root / relative
    cursor = data_root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("O destino contém um link simbólico; nenhum arquivo foi alterado.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.parent.resolve().is_relative_to(data_root):
        raise ValueError("O destino precisa permanecer dentro dos dados da OMM.")
    if destination.is_symlink():
        raise ValueError("O arquivo de destino é um link simbólico; nenhum arquivo foi alterado.")
    return destination


def _save(root: Path, relative: Path, content: str,
          expected_sha256: str | None) -> dict[str, object]:
    encoded = _validate_content(content)
    destination = _safe_directory(root, relative)
    digest = hashlib.sha256(encoded).hexdigest()
    current = destination.read_bytes() if destination.exists() else None
    if current == encoded:
        return {"status": "already_present", "path": relative.as_posix(),
                "bytes": len(encoded), "sha256": digest}
    if current is not None:
        current_digest = hashlib.sha256(current).hexdigest()
        if not expected_sha256 or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise FileExistsError(
                f"Já existe conteúdo diferente em {relative.as_posix()}. "
                f"Leia o arquivo e envie expected_sha256={current_digest} para confirmar a atualização."
            )
        if not hmac.compare_digest(current_digest, expected_sha256):
            raise FileExistsError(
                f"O arquivo mudou desde a leitura (sha256 atual: {current_digest}). "
                "Leia a versão atual antes de tentar novamente."
            )
    elif expected_sha256:
        raise FileExistsError("O arquivo não existe mais; atualize a lista antes de salvar.")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".omm-registry-",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"status": "created" if current is None else "updated",
            "path": relative.as_posix(), "bytes": len(encoded), "sha256": digest}


def save_skill(root: Path, name: str, content: str,
               expected_sha256: str | None = None) -> dict[str, object]:
    if not isinstance(name, str) or not _NAME_PART.fullmatch(name):
        raise ValueError("Nome inválido. Use letras, números, ponto, hífen ou sublinhado; sem pastas.")
    if not content.lstrip().startswith("---\n") or "\nname:" not in content or "\ndescription:" not in content:
        raise ValueError("Uma skill precisa começar com frontmatter Markdown contendo name: e description:.")
    return _save(root, Path("skills") / name / "SKILL.md", content, expected_sha256)


def save_role(root: Path, name: str, content: str,
              expected_sha256: str | None = None) -> dict[str, object]:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Informe o nome do papel, por exemplo open-gbp/planner.")
    parts = name.split("/")
    if any(not _NAME_PART.fullmatch(part) or part in {".", ".."} for part in parts):
        raise ValueError("Nome de papel inválido. Use pastas simples separadas por '/'.")
    role_file = Path(*parts[:-1], parts[-1] + ".md")
    return _save(root, Path("memory") / "roles" / role_file,
                 content, expected_sha256)
