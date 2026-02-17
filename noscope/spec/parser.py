"""Markdown + YAML frontmatter spec parser."""

from __future__ import annotations

from pathlib import Path

import frontmatter

from noscope.spec.models import AcceptanceCheck, SpecInput


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

    return SpecInput(
        name=str(name),
        timebox=str(timebox),
        constraints=[str(c) for c in constraints],
        acceptance=acceptance,
        body=post.content,
        stack_prefs=meta.get("stack_prefs"),
        repo_mode=meta.get("repo_mode", "new"),
        risk_policy=meta.get("risk_policy", "default"),
    )
