"""Tests for phase implementations."""

from __future__ import annotations

from pathlib import Path

import pytest

from noscope.deadline import Deadline
from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage
from noscope.logging.events import EventLog, RunDir
from noscope.phases import HandoffPhase, HardenPhase, RequestPhase, VerifyPhase
from noscope.planning.models import AcceptancePlan, PlanOutput, PlanTask
from noscope.spec.models import SpecInput
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher
from noscope.tools.shell import ShellTool


class _ToolProvider:
    """Fake provider that replays canned tool-call responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = responses
        self._idx = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: object | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(content="done", stop_reason="end_turn", usage=Usage())


def _confirm(command: str, expect: str | None = None) -> LLMResponse:
    args: dict[str, object] = {"command": command}
    if expect is not None:
        args["expect"] = expect
    return LLMResponse(
        tool_calls=[ToolCall(id="c1", name="confirm_running", arguments=args)],
        usage=Usage(),
    )


def _report_failed(reason: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id="f1", name="report_failed", arguments={"reason": reason})],
        usage=Usage(),
    )


@pytest.mark.asyncio
class TestVerifyPhase:
    """VERIFY confirms by running the agent's proof command, not by trusting it."""

    def _dispatcher(self) -> ToolDispatcher:
        d = ToolDispatcher()
        d.register(ShellTool())
        return d

    async def test_confirmation_must_actually_pass(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        # Agent proposes a command that really succeeds -> verified.
        provider = _ToolProvider([_confirm("echo READY", expect="READY")])
        spec = SpecInput(name="App", timebox="5m")
        ok, msg = await VerifyPhase().run(
            spec, provider, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        assert ok is True

    async def test_failing_proof_is_rejected(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        # First proof command fails; the agent then gives up. The harness must
        # not accept the failing proof as success.
        provider = _ToolProvider([_confirm("false"), _report_failed("cannot start")])
        spec = SpecInput(name="App", timebox="5m")
        ok, msg = await VerifyPhase().run(
            spec, provider, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        assert ok is False
        assert "cannot start" in msg

    async def test_wrong_output_is_rejected(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        # Command exits 0 but its output lacks the expected text -> not verified.
        provider = _ToolProvider(
            [_confirm("echo nope", expect="READY"), _report_failed("wrong content")]
        )
        spec = SpecInput(name="App", timebox="5m")
        ok, _ = await VerifyPhase().run(
            spec, provider, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        assert ok is False


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

    async def test_repair_makes_a_failing_check_pass(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        # The check greps for a file that doesn't exist yet, so it fails first.
        # The repair "agent" writes that file; the re-run then passes.
        from noscope.tools.filesystem import WriteFileTool

        dispatcher = self._dispatcher()
        dispatcher.register(WriteFileTool())

        fix = LLMResponse(
            tool_calls=[
                ToolCall(
                    id="w1",
                    name="write_file",
                    arguments={"path": "marker.txt", "content": "READY\n"},
                )
            ],
            usage=Usage(),
        )
        provider = _ToolProvider([fix])

        plan = PlanOutput(
            acceptance_plan=[
                AcceptancePlan(name="marker", cmd="cat marker.txt", expect_output="READY")
            ]
        )
        spec = SpecInput(name="T", timebox="5m")
        results = await HardenPhase().run(
            plan,
            spec,
            dispatcher,
            tool_context,
            event_log,
            tool_context.deadline,
            provider=provider,
        )
        assert results[0]["passed"] is True
        assert results[0]["repaired"] is True

    async def test_no_repair_without_provider(
        self, tool_context: ToolContext, event_log: EventLog
    ) -> None:
        plan = PlanOutput(acceptance_plan=[AcceptancePlan(name="x", cmd="false")])
        spec = SpecInput(name="T", timebox="5m")
        results = await HardenPhase().run(
            plan, spec, self._dispatcher(), tool_context, event_log, tool_context.deadline
        )
        assert results[0]["passed"] is False
        assert results[0]["repaired"] is False


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
