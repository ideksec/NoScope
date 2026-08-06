"""Tests for phase implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from noscope.deadline import Deadline
from noscope.logging.events import EventLog, RunDir
from noscope.phases import HandoffPhase, HardenPhase, RequestPhase
from noscope.planning.models import AcceptancePlan, PlanOutput, PlanTask
from noscope.spec.models import SpecInput
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher
from noscope.tools.shell import ShellTool


@pytest.mark.asyncio
class TestHardenPhase:
    """HARDEN runs real shell commands — no LLM, so these assert real behavior."""

    def _dispatcher(self) -> ToolDispatcher:
        d = ToolDispatcher()
        d.register(ShellTool())
        return d

    async def test_exit_code_pass_and_fail(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        plan = PlanOutput(
            acceptance_plan=[
                AcceptancePlan(name="ok", cmd="true"),
                AcceptancePlan(name="bad", cmd="false"),
            ]
        )
        spec = SpecInput(name="T", timebox="5m")
        results = await HardenPhase().run(
            plan, spec, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        by_name = {r["name"]: r for r in results}
        assert by_name["ok"]["passed"] is True
        assert by_name["bad"]["passed"] is False

    async def test_expected_output_enforced(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        plan = PlanOutput(
            acceptance_plan=[
                AcceptancePlan(name="match", cmd="echo hello world", expect_output="hello"),
                AcceptancePlan(name="nomatch", cmd="echo goodbye", expect_output="hello"),
            ]
        )
        spec = SpecInput(name="T", timebox="5m")
        results = await HardenPhase().run(
            plan, spec, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        by_name = {r["name"]: r for r in results}
        # Command exits 0 both times; only the one whose output matches passes.
        assert by_name["match"]["passed"] is True
        assert by_name["nomatch"]["passed"] is False
        assert "expected output not found" in by_name["nomatch"]["reason"]


@pytest.mark.asyncio
class TestRequestPhase:
    async def test_auto_approve(self, tmp_path: Path) -> None:
        from noscope.capabilities import CapabilityRequest

        rd = RunDir(base=tmp_path / "runs")
        event_log = EventLog(rd)
        deadline = Deadline(300)

        plan = PlanOutput(
            requested_capabilities=[
                CapabilityRequest(cap="workspace_rw", why="Need to write files", risk="low"),
                CapabilityRequest(cap="shell_exec", why="Need to run commands", risk="medium"),
            ]
        )

        phase = RequestPhase()
        grants = await phase.run(plan, event_log, deadline, auto_approve=True)

        assert len(grants) == 2
        assert all(g.approved for g in grants)
        event_log.close()


@pytest.mark.asyncio
class TestHandoffPhase:
    async def test_fallback_report(self, tmp_path: Path) -> None:
        spec = SpecInput(name="Test", timebox="5m", constraints=["Python"])
        plan = PlanOutput(tasks=[PlanTask(id="t1", title="Build it", kind="edit", completed=True)])
        tasks = plan.tasks
        acceptance_results = [{"name": "check1", "passed": True}]

        rd = RunDir(base=tmp_path / "runs")
        event_log = EventLog(rd)
        Deadline(300)

        phase = HandoffPhase()
        report = phase._fallback_report(spec, tasks, [], acceptance_results)

        assert "# Handoff Report" in report
        assert "Test" in report
        assert "Build it" in report
        event_log.close()
