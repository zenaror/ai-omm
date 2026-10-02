"""Import visible conversation text from a Claude Code JSONL session."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .redaction import redact_text


def _visible_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    # Preserve text the user or assistant could read. Tool payloads and hidden
    # reasoning are omitted; source docs and OMM notes remain separately indexed.
    return "\n\n".join(block.get("text", "").strip() for block in content
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str) and block.get("text", "").strip())


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "data não informada"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return "data não informada"


def import_claude_session(root: Path, session_path: Path, scope: str, title: str,
                          session_id: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", scope):
        raise ValueError("scope deve conter apenas letras, números, ponto, hífen ou sublinhado")
    if not session_path.is_file() or session_path.suffix.lower() != ".jsonl":
        raise ValueError("informe um arquivo de sessão Claude Code em formato .jsonl")
    digest = hashlib.sha256()
    lines: list[str] = []
    message_count = 0
    user_count = 0
    assistant_count = 0
    redactions = {"emails": 0, "tokens": 0, "key_values": 0,
                  "private_keys": 0, "credential_urls": 0}
    parsed_session_id = session_id or session_path.stem
    lines.extend(["---", "format: omm-conversation-source-v1", "platform: Claude Code",
                  f"scope: {scope}", f"session_id: {parsed_session_id}", "---", "",
                  f"# {title}", "",
                  "Transcrição histórica normalizada. Mensagens de texto visíveis foram preservadas; "
                  "raciocínio oculto, chamadas de ferramentas, resultados de ferramentas e anexos binários foram omitidos. "
                  "Confirme fatos e decisões na fonte atual do projeto.", ""])
    with session_path.open("rb") as binary_stream:
        for number, raw_line in enumerate(binary_stream, 1):
            digest.update(raw_line)
            try:
                item = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"linha JSON inválida {number}: {exc}") from exc
            if not isinstance(item, dict) or item.get("type") not in {"user", "assistant"}:
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            text = _visible_text(message)
            if not text:
                continue
            text, counts = redact_text(text)
            for key, count in counts.items():
                redactions[key] += count
            role = item["type"]
            message_count += 1
            user_count += role == "user"
            assistant_count += role == "assistant"
            lines.extend([f"## Mensagem {message_count:04d} — {role} | {_timestamp(item.get('timestamp'))}",
                          "", text, ""])
            if item.get("sessionId") and not session_id:
                parsed_session_id = str(item["sessionId"])
    if message_count == 0:
        raise ValueError("a sessão não contém mensagens de texto visíveis")
    markdown = "\n".join(lines).rstrip() + "\n"
    source_hash = digest.hexdigest()
    target = root / "sources" / scope / "historical-chats" / f"claude-code-{parsed_session_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") != markdown:
        raise ValueError(f"já existe um arquivo diferente no destino: {target}")
    target.write_text(markdown, encoding="utf-8")
    return {"path": target.relative_to(root).as_posix(), "source_sha256": source_hash,
            "session_id": parsed_session_id, "messages": message_count,
            "user_messages": user_count, "assistant_messages": assistant_count,
            "redactions": redactions, "scope": scope, "title": title}
