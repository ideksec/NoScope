"""Command safety checks — deny dangerous patterns and path traversal."""

from __future__ import annotations

import re
from pathlib import Path

DENY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$"),  # rm -rf /
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$"),  # rm -rf /
    re.compile(r"\bsudo\b"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+.*of=/dev/"),
    re.compile(r"\b:(){ :\|:& };:"),  # fork bomb
    re.compile(r"\bcurl\s+.*\|\s*(?:bash|sh|zsh)\b"),  # pipe to shell
    re.compile(r"\bwget\s+.*\|\s*(?:bash|sh|zsh)\b"),
    re.compile(r"xmrig|cryptominer|minerd|stratum\+tcp"),  # crypto mining
    re.compile(r"\beval\b.*\$\("),  # eval with command substitution
    re.compile(r">\s*/dev/sd[a-z]"),  # write to raw disk
    re.compile(r"\bnc\s+-[a-zA-Z]*l"),  # netcat listener (reverse shell)
]

DENY_REASONS: dict[int, str] = {
    0: "destructive filesystem operation",
    1: "destructive filesystem operation",
    2: "privilege escalation",
    3: "overly permissive file permissions",
    4: "filesystem destruction",
    5: "raw disk write",
    6: "fork bomb",
    7: "piping remote content to shell",
    8: "piping remote content to shell",
    9: "crypto mining",
    10: "dangerous eval",
    11: "raw disk write",
    12: "potential reverse shell",
}


def check_command_safety(command: str, danger_mode: bool = False) -> str | None:
    """Check if a command matches deny patterns.

    Returns None if safe, or a reason string if denied.
    In danger_mode, returns None for all commands.
    """
    if danger_mode:
        return None

    for i, pattern in enumerate(DENY_PATTERNS):
        if pattern.search(command):
            return DENY_REASONS.get(i, "denied by safety filter")

    return None


def check_path_safety(path: str | Path, workspace: Path) -> str | None:
    """Check if a path is safely within the workspace.

    Returns None if safe, or a reason string if denied.
    """
    try:
        p = Path(path)
        workspace_resolved = workspace.resolve()

        # Resolve relative to workspace for relative paths, absolute as-is
        resolved = p.resolve() if p.is_absolute() else (workspace / p).resolve()

        if ".." in str(path) and not str(resolved).startswith(str(workspace_resolved)):
            return "path traversal detected"

        if not str(resolved).startswith(str(workspace_resolved)):
            return "path outside workspace"

    except (OSError, ValueError) as e:
        return f"invalid path: {e}"

    return None


def resolve_workspace_path(path: str, workspace: Path) -> Path:
    """Resolve a path relative to the workspace, with safety checks."""
    resolved = Path(path).resolve() if Path(path).is_absolute() else (workspace / path).resolve()

    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ValueError(f"Path {path} resolves outside workspace: {resolved}")

    return resolved
