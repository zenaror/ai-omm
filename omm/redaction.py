"""Small shared scrubber for exported conversation source material."""

from __future__ import annotations

import re


_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(
    r"(?i)(?:\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{24,})\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,})"
)
_KEY_VALUE = re.compile(
    r"(?i)(\b(?:password|passwd|token|secret|api[_-]?key|access[_-]?key)\b\s*[:=]\s*)"
    r"([\"']?)[A-Za-z0-9_./+=-]{12,}"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
_CREDENTIAL_URL = re.compile(r"(https?://)[^/@:\s]+:[^/@\s]+@", re.IGNORECASE)
_HOME_PATH = re.compile(r"(?<!\S)/home/[^/\s]+(?=/)")
_MEDIA_PROJECTS_PATH = re.compile(r"/media/[^/\s]+/Dados/")


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    counts = {"emails": 0, "tokens": 0, "key_values": 0,
              "private_keys": 0, "credential_urls": 0}

    def replace(pattern: re.Pattern[str], replacement: str, key: str, value: str) -> str:
        result, count = pattern.subn(replacement, value)
        counts[key] += count
        return result

    text = replace(_PRIVATE_KEY, "[CHAVE PRIVADA REMOVIDA]", "private_keys", text)
    text = replace(_TOKEN, "[TOKEN REMOVIDO]", "tokens", text)
    text = replace(_KEY_VALUE, r"\1\2[VALOR REMOVIDO]", "key_values", text)
    text = replace(_CREDENTIAL_URL, r"\1[CREDENCIAIS REMOVIDAS]@", "credential_urls", text)
    text = replace(_EMAIL, "[E-MAIL REMOVIDO]", "emails", text)
    text = _HOME_PATH.sub("$HOME", text)
    text = _MEDIA_PROJECTS_PATH.sub("$PROJECTS/", text)
    return text, counts


def find_credentials(text: str) -> list[str]:
    """Return credential categories found, without changing or returning their values."""
    _, counts = redact_text(text)
    return [name for name in ("tokens", "key_values", "private_keys", "credential_urls")
            if counts[name]]
