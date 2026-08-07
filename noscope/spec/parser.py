"""Markdown + YAML frontmatter spec parsing and generation."""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml

from noscope.spec.models import AcceptanceCheck, SpecInput


def slugify(name: str) -> str:
    """Turn a project name into a safe single-segment filename stem."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "spec"


def build_spec_file(
    name: str,
    timebox: str,
    constraints: list[str],
    acceptance: list[str],
    body: str,
) -> str:
    """Render a spec file with correctly escaped YAML frontmatter.

    Uses a real YAML dumper so names or constraints containing quotes,
    colons, or newlines produce a valid file.
    """
    meta = {
        "name": name,
        "timebox": timebox,
        "constraints": constraints,
        "acceptance": acceptance,
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{front}---\n\n{body}\n"


def parse_spec(path: Path) -> SpecInput:
    """Parse a spec file into a validated SpecInput model."""
    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")

    text = path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    meta = dict(post.metadata)

    name = meta.get("name")
    if not name:
        raise ValueError("Spec must include 'name' in frontmatter")

    timebox = meta.get("timebox")
    if not timebox:
        raise ValueError("Spec must include 'timebox' in frontmatter")

    constraints = meta.get("constraints", [])
    if not isinstance(constraints, list):
        raise ValueError("'constraints' must be a list")

    raw_acceptance = meta.get("acceptance", [])
    if not isinstance(raw_acceptance, list):
        raise ValueError("'acceptance' must be a list")
    acceptance = [AcceptanceCheck.from_string(a) for a in raw_acceptance]

    stack_prefs = meta.get("stack_prefs")
    if stack_prefs is not None and not isinstance(stack_prefs, list):
        raise ValueError("'stack_prefs' must be a list")

    repo_mode = meta.get("repo_mode", "new")
    if repo_mode not in ("new", "existing"):
        raise ValueError("'repo_mode' must be 'new' or 'existing'")

    risk_policy = meta.get("risk_policy", "default")
    if risk_policy not in ("strict", "default", "permissive"):
        raise ValueError("'risk_policy' must be 'strict', 'default', or 'permissive'")

    return SpecInput(
        name=str(name),
        timebox=str(timebox),
        constraints=[str(c) for c in constraints],
        acceptance=acceptance,
        body=post.content,
        stack_prefs=[str(s) for s in stack_prefs] if stack_prefs is not None else None,
        repo_mode=repo_mode,
        risk_policy=risk_policy,
    )
