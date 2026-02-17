"""Scope contract generation — the immutable success criteria for a run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noscope.capabilities import CapabilityGrant
from noscope.planning.models import PlanOutput
from noscope.spec.models import SpecInput


def generate_contract(
    spec: SpecInput,
    plan: PlanOutput,
    grants: list[CapabilityGrant],
    output_path: Path,
) -> dict[str, Any]:
    """Generate and write the NOSCOPE_CONTRACT.json."""
    contract: dict[str, Any] = {
        "name": spec.name,
        "timebox": spec.timebox,
        "timebox_seconds": spec.timebox_seconds,
        "constraints": spec.constraints,
        "mvp_definition": plan.mvp_definition,
        "exclusions": plan.exclusions,
        "tasks": [t.model_dump() for t in plan.tasks],
        "acceptance_plan": [a.model_dump() for a in plan.acceptance_plan],
        "capability_grants": [g.model_dump() for g in grants],
        "spec_acceptance": [a.model_dump() for a in spec.acceptance],
    }

    output_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    return contract
