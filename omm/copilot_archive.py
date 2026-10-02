"""Import a VS Code GitHub Copilot chat export as searchable source material."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from .redaction import redact_text


def _assistant_text(request: dict) -> str:
    pieces = []
    for item in request.get("response", []):
        # GitHub Copilot stores internal reasoning, tools and final text together.
        # Keep only rendered answer blocks; discard hidden reasoning/tool payloads.
        if isinstance(item, dict) and "kind" not in item and isinstance(item.get("value"), str):
            value = item["value"].strip()
            if value:
                pieces.append(value)
    return "\n\n".join(pieces)


def import_copilot_chat(root: Path, export_path: Path, scope: str,
                        title: str = "Histórico do GitHub Copilot") -> dict:
    """Write a compact, redacted Markdown archive under data-root/sources/.

    The archive is a locator and historical record. It is never appended to the
    canonical memory JSONL file.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", scope):
        raise ValueError("scope deve conter apenas letras, números, ponto, hífen ou sublinhado")
    if not export_path.is_file():
        raise ValueError(f"arquivo não encontrado: {export_path}")
    raw = export_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        export = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"exportação não é JSON UTF-8 válido: {exc}") from exc
    if not isinstance(export, dict) or export.get("responderUsername") != "GitHub Copilot":
        raise ValueError("o arquivo não parece uma exportação do GitHub Copilot para VS Code")
    requests = export.get("requests")
    if not isinstance(requests, list) or not requests:
        raise ValueError("a exportação não contém conversas")

    total_redactions = {"emails": 0, "tokens": 0, "key_values": 0,
                        "private_keys": 0, "credential_urls": 0}
    lines = ["---", "format: omm-conversation-source-v1", "platform: GitHub Copilot",
             f"scope: {scope}", f"source_sha256: {digest}", "---", "",
             f"# {title}", "",
             "Arquivo histórico importado do GitHub Copilot. Use a busca para localizar trechos; "
             "confirme decisões e fatos no projeto atual antes de aplicá-los.", ""]
    imported = 0
    for number, request in enumerate(requests, 1):
        if not isinstance(request, dict):
            continue
        prompt_obj = request.get("message", {})
        prompt = prompt_obj.get("text", "") if isinstance(prompt_obj, dict) else ""
        if not isinstance(prompt, str):
            prompt = ""
        answer = _assistant_text(request)
        if not prompt.strip() and not answer.strip():
            continue
        stamp = request.get("timestamp")
        try:
            when = datetime.fromtimestamp(float(stamp) / 1000, timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError, OverflowError):
            when = "data não informada"
        prompt, counts = redact_text(prompt)
        for key, count in counts.items():
            total_redactions[key] += count
        answer, counts = redact_text(answer)
        for key, count in counts.items():
            total_redactions[key] += count
        lines.extend([f"## Interação {number:03d} — {when}", "", "### Pedido", "",
                      prompt.strip() or "(sem texto)", "", "### Resposta visível", "",
                      answer.strip() or "(sem resposta textual)", ""])
        imported += 1

    markdown = "\n".join(lines).rstrip() + "\n"
    target = root / "sources" / scope / "historical-chats" / f"github-copilot-{digest[:12]}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") != markdown:
        raise ValueError(f"já existe um arquivo diferente no destino: {target}")
    target.write_text(markdown, encoding="utf-8")
    return {"path": target.relative_to(root).as_posix(), "source_sha256": digest,
            "requests": len(requests), "interactions": imported,
            "redactions": total_redactions, "title": title, "scope": scope}
