"""Tests for orchestrator — uses mocked LLM."""

from __future__ import annotations

from noscope.planning.models import AcceptancePlan, PlanOutput, PlanTask
from noscope.spec.models import AcceptanceCheck, SpecInput


class TestPlanOutput:
    def test_model(self) -> None:
        plan = PlanOutput(
            tasks=[
                PlanTask(id="t1", title="Setup", kind="shell", priority="mvp"),
                PlanTask(id="t2", title="Build", kind="edit", priority="mvp"),
            ],
            mvp_definition=["It runs"],
            exclusions=["No deploy"],
            acceptance_plan=[AcceptancePlan(name="tests", cmd="pytest -q")],
        )
        assert len(plan.tasks) == 2
        assert plan.tasks[0].priority == "mvp"
        assert plan.acceptance_plan[0].cmd == "pytest -q"


class TestSpecInput:
    def test_timebox_parsing(self) -> None:
        spec = SpecInput(name="Test", timebox="10m")
        assert spec.timebox_seconds == 600

    def test_acceptance_checks(self) -> None:
        spec = SpecInput(
            name="Test",
            timebox="5m",
            acceptance=[
                AcceptanceCheck.from_string("cmd: pytest"),
                AcceptanceCheck.from_string("Has a README"),
            ],
        )
        assert spec.acceptance[0].is_cmd is True
        assert spec.acceptance[1].is_cmd is False
