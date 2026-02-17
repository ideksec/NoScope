"""Tests for phase implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from noscope.deadline import Deadline
from noscope.logging.events import EventLog, RunDir
from noscope.phases import HandoffPhase, RequestPhase
from noscope.planning.models import PlanOutput, PlanTask
from noscope.spec.models import SpecInput


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
        plan = PlanOutput(
            tasks=[PlanTask(id="t1", title="Build it", kind="edit", completed=True)]
        )
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
