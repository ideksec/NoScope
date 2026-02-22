"""Secret redaction for logs and output."""

from __future__ import annotations

import re
from typing import Any

# Keep assignment key names while redacting the value.
_SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b(?:api[_-]?key|secret|token|password|credential(?:s)?)\b\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)
    """
)

_AUTH_HEADER_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b(?:authorization|x-api-key)\b\s*[:=]\s*)
    (?:bearer\s+)?[^\s,;]+
    """
)

_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
)

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
)


def redact(text: str, secrets: dict[str, str]) -> str:
    """Replace explicit secret values in text with [REDACTED:<name>]."""
    if not secrets:
        return text

    # Replace longer values first to avoid partial replacements.
    ordered = sorted(
        ((name, value) for name, value in secrets.items() if value),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    result = text
    for name, value in ordered:
        result = result.replace(value, f"[REDACTED:{name}]")
    return result


def redact_env_vars(text: str) -> str:
    """Redact common token, env-var, and private-key patterns."""
    result = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED:auto]", text)
    result = _AUTH_HEADER_ASSIGNMENT.sub(r"\1[REDACTED:auto]", result)

    for pattern in _TOKEN_PATTERNS:
        result = pattern.sub("[REDACTED:auto]", result)

    return _PRIVATE_KEY_BLOCK.sub("[REDACTED:auto]", result)


def redact_text(text: str, secrets: dict[str, str]) -> str:
    """Apply explicit and automatic redaction to text."""
    return redact_env_vars(redact(text, secrets))


def redact_structured(data: Any, secrets: dict[str, str]) -> Any:
    """Recursively redact secrets from nested structures."""
    if isinstance(data, str):
        return redact_text(data, secrets)
    if isinstance(data, dict):
        return {k: redact_structured(v, secrets) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_structured(item, secrets) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_structured(item, secrets) for item in data)
    return data
