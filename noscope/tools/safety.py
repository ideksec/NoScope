"""Command safety checks — deny dangerous patterns and path traversal."""

from __future__ import annotations

import re
from pathlib import Path

DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$"), "destructive filesystem operation"),
    (
        re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$"),
        "destructive filesystem operation",
    ),
    (re.compile(r"(?:^|/|\b)sudo\b"), "privilege escalation"),
    (re.compile(r"\bchmod\s+0?777\b"), "overly permissive file permissions"),
    (re.compile(r"\bmkfs\b"), "filesystem destruction"),
    (re.compile(r"\bdd\s+.*of=/dev/"), "raw disk write"),
    (re.compile(r"\b:(){ :\|:& };:"), "fork bomb"),
    (re.compile(r"\bcurl\s+.*\|\s*(?:bash|sh|zsh|dash)\b"), "piping remote content to shell"),
    (re.compile(r"\bwget\s+.*\|\s*(?:bash|sh|zsh|dash)\b"), "piping remote content to shell"),
    (re.compile(r"\bbase64\b.*\|\s*(?:bash|sh|zsh|dash)\b"), "piping decoded content to shell"),
    (re.compile(r"xmrig|cryptominer|minerd|stratum\+tcp"), "crypto mining"),
    (re.compile(r"\beval\b.*\$\("), "dangerous eval"),
    (re.compile(r">\s*/dev/sd[a-z]"), "raw disk write"),
    (re.compile(r"\bnc\s+-[a-zA-Z]*l"), "potential reverse shell"),
    (re.compile(r"\bdocker\s+.*--privileged\b"), "privileged container"),
    (
        re.compile(r"\bpython3?\s+-c\s+['\"].*\b(?:os\.system|subprocess|exec)\b"),
        "code execution evasion",
    ),
]

# Interactive commands that hang waiting for stdin — blocked with helpful message.
INTERACTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bnpx\s+create-"),
        "interactive scaffolding (npx create-*). Write project files manually instead",
    ),
    (
        re.compile(r"\bnpm\s+create\b"),
        "interactive scaffolding (npm create). Write project files manually instead",
    ),
    (
        re.compile(r"\bnpm\s+init\b(?!.*\s-[yY]\b)"),
        "interactive npm init. Use 'npm init -y' for non-interactive, or write package.json manually",
    ),
    (
        re.compile(r"\byarn\s+create\b"),
        "interactive scaffolding (yarn create). Write project files manually instead",
    ),
]


def check_command_safety(command: str, danger_mode: bool = False) -> str | None:
    """Check if a command matches deny patterns.

    Returns None if safe, or a reason string if denied.
    In danger_mode, returns None for all commands.
    """
    if danger_mode:
        return None

    for pattern, reason in DENY_PATTERNS:
        if pattern.search(command):
            return reason

    # Block interactive commands that hang waiting for stdin
    for pattern, reason in INTERACTIVE_PATTERNS:
        if pattern.search(command):
            return reason

    return None


def check_path_safety(path: str | Path, workspace: Path) -> str | None:
    """Check if a path is safely within the workspace.

    Returns None if safe, or a reason string if denied.
    """
    try:
        p = Path(path)
        workspace_resolved = workspace.resolve()

        # Resolve relative to workspace for relative paths, absolute as-is.
        resolved = p.resolve() if p.is_absolute() else (workspace_resolved / p).resolve()

        if _is_outside_workspace(resolved, workspace_resolved):
            if ".." in p.parts:
                return "path traversal detected"
            return "path outside workspace"

    except (OSError, ValueError) as e:
        return f"invalid path: {e}"

    return None


def resolve_workspace_path(path: str, workspace: Path) -> Path:
    """Resolve a path relative to the workspace, with safety checks."""
    p = Path(path)
    workspace_resolved = workspace.resolve()
    resolved = p.resolve() if p.is_absolute() else (workspace_resolved / p).resolve()

    if _is_outside_workspace(resolved, workspace_resolved):
        raise ValueError(f"Path {path} resolves outside workspace: {resolved}")

    return resolved


def _is_outside_workspace(resolved: Path, workspace: Path) -> bool:
    """True when resolved path does not live within workspace root."""
    try:
        resolved.relative_to(workspace)
        return False
    except ValueError:
        return True
