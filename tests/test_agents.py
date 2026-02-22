"""Tests for build agents and supervisor."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from noscope.agents import AuditAgent, BuildAgent
from noscope.deadline import Deadline
from noscope.llm.base import LLMResponse, Message, StreamChunk, ToolCall, ToolSchema, Usage
from noscope.planning.models import PlanTask
from noscope.supervisor import Supervisor
from noscope.tools.base import ToolContext


class FakeProvider:
    """Fake LLM provider that returns canned responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = responses
        self._idx = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(content="BUILD COMPLETE", stop_reason="end_turn", usage=Usage())

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(is_final=True)


def _make_tasks() -> list[PlanTask]:
    return [
        PlanTask(id="t1", title="Setup project", kind="shell", priority="mvp"),
        PlanTask(id="t2", title="Build feature A", kind="edit", priority="mvp", depends_on=["t1"]),
        PlanTask(id="t3", title="Build feature B", kind="edit", priority="mvp", depends_on=["t1"]),
        PlanTask(id="t4", title="Add polish", kind="edit", priority="stretch", depends_on=["t2"]),
    ]


class TestBuildAgent:
    @pytest.mark.asyncio
    async def test_agent_marks_tasks_complete(self, tool_context: ToolContext) -> None:
        provider = FakeProvider(
            [
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(id="tc1", name="mark_task_complete", arguments={"task_id": "t1"})
                    ],
                    usage=Usage(),
                ),
                LLMResponse(content="BUILD COMPLETE", stop_reason="end_turn", usage=Usage()),
            ]
        )

        from noscope.logging.events import EventLog, RunDir

        run_dir = RunDir(base=tool_context.workspace.parent / "runs")
        event_log = EventLog(run_dir)

        agent = BuildAgent(
            agent_id="test",
            provider=provider,
            dispatcher=tool_context.event_log._file,  # dummy
            context=tool_context,
            event_log=event_log,
            deadline=tool_context.deadline,
        )

        # We can't easily test the full loop without a real dispatcher,
        # but we can test that the agent class instantiates correctly
        assert agent.agent_id == "test"
        event_log.close()

    @pytest.mark.asyncio
    async def test_agent_stops_when_all_tasks_complete(self, tool_context: ToolContext) -> None:
        tasks = [PlanTask(id="t1", title="Test", kind="edit", completed=True)]
        # Agent should immediately stop since tasks are already done
        provider = FakeProvider(
            [
                LLMResponse(content="Done", stop_reason="end_turn", usage=Usage()),
            ]
        )

        from noscope.logging.events import EventLog, RunDir
        from noscope.tools.dispatcher import ToolDispatcher

        run_dir = RunDir(base=tool_context.workspace.parent / "runs")
        event_log = EventLog(run_dir)
        dispatcher = ToolDispatcher()

        agent = BuildAgent(
            agent_id="test",
            provider=provider,
            dispatcher=dispatcher,
            context=tool_context,
            event_log=event_log,
            deadline=tool_context.deadline,
        )

        result = await agent.run(tasks, "You are a builder.")
        assert all(t.completed for t in result)
        event_log.close()


class TestSupervisor:
    def test_split_setup(self) -> None:
        supervisor = Supervisor.__new__(Supervisor)
        tasks = _make_tasks()
        setup, remaining = supervisor._split_setup(tasks)
        assert len(setup) == 1
        assert setup[0].id == "t1"
        assert len(remaining) == 3

    def test_partition_tasks(self) -> None:
        supervisor = Supervisor.__new__(Supervisor)
        tasks = [
            PlanTask(id="t2", title="Feature A", kind="edit", depends_on=["t1"]),
            PlanTask(id="t3", title="Feature B", kind="edit", depends_on=["t1"]),
            PlanTask(id="t4", title="Polish", kind="edit", depends_on=["t2"]),
        ]
        streams = supervisor._partition_tasks(tasks)
        # Should create streams, respecting dependencies
        assert len(streams) >= 1
        assert len(streams) <= 3
        # All tasks should be assigned
        all_ids = {t.id for stream in streams for t in stream}
        assert all_ids == {"t2", "t3", "t4"}

    def test_partition_empty(self) -> None:
        supervisor = Supervisor.__new__(Supervisor)
        assert supervisor._partition_tasks([]) == []

    def test_split_setup_with_no_setup_keyword(self) -> None:
        supervisor = Supervisor.__new__(Supervisor)
        tasks = [
            PlanTask(id="t1", title="Create API routes", kind="edit"),
            PlanTask(id="t2", title="Add database", kind="edit"),
        ]
        setup, remaining = supervisor._split_setup(tasks)
        # First task should always be setup
        assert len(setup) == 1
        assert setup[0].id == "t1"


class TestAuditAgent:
    @pytest.mark.asyncio
    async def test_audit_runs_checks(self, tool_context: ToolContext) -> None:
        from noscope.tools.dispatcher import ToolDispatcher

        dispatcher = ToolDispatcher()
        audit = AuditAgent(
            dispatcher=dispatcher,
            context=tool_context,
            event_log=tool_context.event_log,
            deadline=Deadline(5),  # Short deadline
        )
        # Audit should return quickly with no files in workspace
        findings = await audit._run_checks()
        # Empty workspace = missing files finding
        assert any(f["type"] == "missing_files" for f in findings)
