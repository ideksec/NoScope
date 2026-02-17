"""Secret redaction for logs and output."""

from __future__ import annotations

import re


def redact(text: str, secrets: dict[str, str]) -> str:
    """Replace secret values in text with [REDACTED:<name>]."""
    if not secrets:
        return text

    result = text
    for name, value in secrets.items():
        if value:
            result = result.replace(value, f"[REDACTED:{name}]")
    return result


def redact_env_vars(text: str) -> str:
    """Redact common environment variable patterns that look like secrets."""
    patterns = [
        r'(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)=[^\s"\']+',
        r'sk-[a-zA-Z0-9]{20,}',
        r'sk-ant-[a-zA-Z0-9\-]{20,}',
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "[REDACTED:auto]", result)
    return result
