"""Tests for contract generation."""

from __future__ import annotations

import json
from pathlib import Path

from noscope.capabilities import CapabilityGrant
from noscope.planning.models import AcceptancePlan, PlanOutput, PlanTask
from noscope.spec.contract import generate_contract
from noscope.spec.models import AcceptanceCheck, SpecInput


class TestContract:
    def test_generates_json(self, tmp_path: Path) -> None:
        spec = SpecInput(
            name="Test",
            timebox="5m",
            constraints=["Python only"],
            acceptance=[AcceptanceCheck.from_string("cmd: pytest")],
        )
        plan = PlanOutput(
            tasks=[PlanTask(id="t1", title="Build it", kind="edit")],
            mvp_definition=["It runs"],
            exclusions=["No deploy"],
            acceptance_plan=[AcceptancePlan(name="tests", cmd="pytest", must_pass=True)],
        )
        grants = [CapabilityGrant(cap="workspace_rw", approved=True)]

        output = tmp_path / "contract.json"
        generate_contract(spec, plan, grants, output)

        assert output.exists()
        data = json.loads(output.read_text())
        assert data["name"] == "Test"
        assert data["timebox_seconds"] == 300
        assert len(data["tasks"]) == 1
        assert len(data["capability_grants"]) == 1
