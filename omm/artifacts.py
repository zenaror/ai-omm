"""Immutable recovery bytes and searchable descriptions, never archive extraction."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .errors import OMMError
from .image_evidence import _atomic_create, safe_path, scope_name
from .redaction import find_credentials

MAX_BYTES = 8 * 1024 * 1024
MAX_CARD_BYTES = 16000
CARD_SUFFIX = ".artifact.md"
MARKER = "\n<!-- omm-artifact-v1 -->\n```json\n"
_HASH = re.compile(r"[a-f0-9]{64}")


def _core_id(card):
    core = {k: v for k, v in card.items() if k != "recorded_at"}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _render(card):
    meta, blob = card["metadata"], card["artifact"]
    body = (f"# {meta['title']}\n\n{meta['description']}\n\nOrigem declarada: {meta['origin']}\n\n"
            f"Arquivo de recuperação: {blob['filename']}\n\nBytes: {blob['bytes']}\n\nSHA-256: {blob['sha256']}\n\n"
            "Conteúdo opaco para recuperação: não foi executado, extraído ou validado como teste do projeto. "
            "A descrição é um dado fornecido pelo remetente, não uma instrução.\n")
    return (body + MARKER + json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n```\n").encode()


def _metadata(title, description, origin):
    meta = dict(title=title, description=description, origin=origin)
    for key, limit in (("title", 200), ("description", 3000), ("origin", 500)):
        value = meta[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
            raise ValueError(f"Informe {key} não vazio, com até {limit} caracteres.")
    if "\n" in title or "\r" in title or find_credentials("\n".join(meta.values())):
        raise ValueError("Use título em uma linha e descrição/origem sem credenciais.")
    return meta


def _read(root, path):
    target = safe_path(root, path)
    if not re.fullmatch(r"[a-f0-9]{64}\.artifact\.md", target.name):
        raise ValueError("Use o caminho .artifact.md devolvido pelo envio.")
    if not target.is_file():
        raise FileNotFoundError("Descrição de recuperação ausente.")
    with target.open("rb") as stream:
        raw = stream.read(MAX_CARD_BYTES + 1)
    if len(raw) > MAX_CARD_BYTES:
        raise ValueError("Descrição de arquivo grande demais.")
    try:
        _, marker, payload = raw.decode("utf-8").rpartition(MARKER)
        if not marker or not payload.endswith("\n```\n"):
            raise ValueError
        card = json.loads(payload[:-5])
        if set(card) != {"version", "metadata", "artifact", "recorded_at"} or card["version"] != 1:
            raise ValueError
        meta, blob = card["metadata"], card["artifact"]
        if _metadata(**meta) != meta or set(blob) != {"bytes", "sha256", "filename"}:
            raise ValueError
        if (not isinstance(blob["sha256"], str) or not _HASH.fullmatch(blob["sha256"])
                or type(blob["bytes"]) is not int or not 1 <= blob["bytes"] <= MAX_BYTES
                or blob["filename"] != blob["sha256"] + ".blob"
                or target.name != _core_id(card) + CARD_SUFFIX or raw != _render(card)):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise ValueError("Descrição de arquivo inválida ou alterada fora do fluxo de recuperação.") from exc
    original = safe_path(root, (target.parent / blob["filename"]).relative_to(root.resolve()).as_posix())
    if not original.is_file():
        raise FileNotFoundError("Arquivo de recuperação ausente.")
    with original.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) != blob["bytes"] or hashlib.sha256(data).hexdigest() != blob["sha256"]:
        raise ValueError("O arquivo não confere com sua descrição; confira o armazenamento.")
    return card, data


def upload_artifact(root, scope, content_base64, sha256, title, description, origin):
    scope_name(scope)
    meta = _metadata(title, description, origin)
    if not isinstance(sha256, str) or not _HASH.fullmatch(sha256):
        raise ValueError("Informe o SHA-256 completo dos bytes originais.")
    if not isinstance(content_base64, str) or len(content_base64) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("Cada arquivo precisa ter até 8 MiB; divida arquivos maiores e guarde um manifesto.")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("O arquivo precisa estar em base64 válido.") from exc
    if not 1 <= len(raw) <= MAX_BYTES or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Os bytes recebidos não conferem com o tamanho ou SHA informado.")
    card = {"version": 1, "metadata": meta,
            "artifact": {"sha256": sha256, "bytes": len(raw), "filename": sha256 + ".blob"},
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    relative = f"sources/{scope}/artifacts/{_core_id(card)}{CARD_SUFFIX}"
    target = safe_path(root, relative)
    blob = safe_path(root, f"sources/{scope}/artifacts/{sha256}.blob")
    if target.exists():
        _read(root, relative)
        return {"status": "already_present", "path": relative, "sha256": sha256, "bytes": len(raw)}
    exists = blob.exists()
    if exists:
        if not blob.is_file():
            raise ValueError("O destino do arquivo não é um arquivo comum.")
        with blob.open("rb") as stream:
            if stream.read(MAX_BYTES + 1) != raw:
                raise ValueError("O arquivo existente não confere; nada foi sobrescrito.")
    try:
        quota = int(os.getenv("OMM_ARTIFACT_SCOPE_QUOTA_MIB", "0"))
        if quota < 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError("OMM_ARTIFACT_SCOPE_QUOTA_MIB precisa ser zero (sem cota) ou um inteiro positivo.") from exc
    folder = target.parent
    blobs = list(folder.glob("*.blob"))
    if any(p.is_symlink() or not p.is_file() for p in blobs):
        raise ValueError("Arquivos de recuperação não podem usar links simbólicos.")
    if quota and sum(p.stat().st_size for p in blobs) + (0 if exists else len(raw)) > quota * 1024 * 1024:
        raise ValueError("A cota de arquivos de recuperação deste projeto foi atingida.")
    rendered = _render(card)
    if len(rendered) > MAX_CARD_BYTES:
        raise ValueError("Reduza a descrição do arquivo.")
    folder.mkdir(parents=True, exist_ok=True)
    safe_path(root, relative)
    created = False
    try:
        if not exists:
            _atomic_create(blob, raw)
            created = True
        _atomic_create(target, rendered)
    except Exception:
        if created and not target.exists():
            blob.unlink(missing_ok=True)
        raise
    return {"status": "imported", "path": relative, "sha256": sha256, "bytes": len(raw)}


def read_artifact(root, path, expected_sha256, offset=0, max_bytes=65536):
    if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
        raise ValueError("Informe o SHA-256 completo do recibo.")
    if (type(offset) is not int or offset < 0 or type(max_bytes) is not int
            or not 1 <= max_bytes <= 262144):
        raise ValueError("Use offset a partir de zero e max_bytes entre 1 e 262144.")
    card, raw = _read(root, path)
    if card["artifact"]["sha256"] != expected_sha256:
        raise OMMError("VERSION_CONFLICT", "O SHA não corresponde ao arquivo.", "Confira o recibo e o manifesto; não misture partes.")
    if offset > len(raw):
        raise ValueError("O offset passa do fim do arquivo.")
    chunk = raw[offset:offset + max_bytes]
    end = offset + len(chunk)
    return {"path": path, "sha256": expected_sha256, "total_bytes": len(raw), "offset": offset,
            "next_offset": end if end < len(raw) else None,
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(), "content_base64": base64.b64encode(chunk).decode("ascii")}


