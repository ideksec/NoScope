"""Tests for build agents and supervisor."""

from __future__ import annotations

from typing import Any

import pytest

from noscope.agents import AuditAgent, AuditFeed, BuildAgent
from noscope.deadline import Deadline
from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage
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
        effort: str | None = None,
    ) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(content="BUILD COMPLETE", stop_reason="end_turn", usage=Usage())


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
        tasks = [PlanTask(id="t1", title="Test task", kind="edit")]
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
        assert result[0].completed is True
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


def _supervisor(max_workers: int = 2) -> Supervisor:
    """A Supervisor with just the fields the pure planning helpers need."""
    sup = Supervisor.__new__(Supervisor)
    sup.max_workers = max_workers
    return sup


class TestSupervisor:
    def test_split_setup(self) -> None:
        supervisor = _supervisor()
        tasks = _make_tasks()
        setup, remaining = supervisor._split_setup(tasks)
        assert len(setup) == 1
        assert setup[0].id == "t1"
        assert len(remaining) == 3

    def test_partition_tasks(self) -> None:
        supervisor = _supervisor()
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
        supervisor = _supervisor()
        assert supervisor._partition_tasks([]) == []

    def test_partition_groups_transitive_chains(self) -> None:
        # t4 -> t3 -> t2 is a transitive chain: all three must share a stream
        # even though t4 never directly names t2 (issue #3)
        supervisor = _supervisor()
        tasks = [
            PlanTask(id="t2", title="Models", kind="edit", depends_on=["t1"]),
            PlanTask(id="t3", title="API", kind="edit", depends_on=["t2"]),
            PlanTask(id="t5", title="Docs", kind="edit", depends_on=["t1"]),
            PlanTask(id="t4", title="Frontend", kind="edit", depends_on=["t3"]),
        ]
        streams = supervisor._partition_tasks(tasks)
        chain_stream = next(s for s in streams if any(t.id == "t2" for t in s))
        chain_ids = [t.id for t in chain_stream]
        assert "t3" in chain_ids
        assert "t4" in chain_ids
        # Dependencies come before dependents within the stream
        assert chain_ids.index("t2") < chain_ids.index("t3") < chain_ids.index("t4")
        # Independent task lands in its own stream
        assert any(t.id == "t5" for s in streams for t in s if s is not chain_stream)

    def test_partition_respects_max_workers(self) -> None:
        tasks = [PlanTask(id=f"t{i}", title=f"Task {i}", kind="edit") for i in range(2, 10)]
        streams = _supervisor()._partition_tasks(tasks)
        assert len(streams) <= 2
        all_ids = {t.id for s in streams for t in s}
        assert all_ids == {t.id for t in tasks}

    def test_topo_sort_handles_cycles(self) -> None:
        tasks = [
            PlanTask(id="t2", title="A", kind="edit", depends_on=["t3"]),
            PlanTask(id="t3", title="B", kind="edit", depends_on=["t2"]),
        ]
        # Cycle must not hang; falls back to plan order
        result = Supervisor._topo_sort(tasks)
        assert [t.id for t in result] == ["t2", "t3"]

    def test_split_setup_with_no_setup_keyword(self) -> None:
        supervisor = _supervisor()
        tasks = [
            PlanTask(id="t1", title="Create API routes", kind="edit"),
            PlanTask(id="t2", title="Add database", kind="edit"),
        ]
        setup, remaining = supervisor._split_setup(tasks)
        # First task should always be setup
        assert len(setup) == 1
        assert setup[0].id == "t1"


class TestAuditFeed:
    def test_publish_dedupes_by_type_and_message(self) -> None:
        feed = AuditFeed()
        finding = {"type": "invalid_json", "message": "package.json is invalid"}
        assert feed.publish([finding]) == [finding]
        # Same finding republished (audit re-detects each cycle) is dropped
        assert feed.publish([dict(finding)]) == []
        other = {"type": "invalid_json", "message": "tsconfig.json is invalid"}
        assert feed.publish([other]) == [other]
        assert feed.all_findings == [finding, other]

    def test_poll_tracks_per_consumer_cursors(self) -> None:
        feed = AuditFeed()
        f1 = {"type": "missing_files", "message": "no entry point"}
        f2 = {"type": "invalid_json", "message": "bad package.json"}
        feed.publish([f1])
        assert feed.poll("worker-0") == [f1]
        assert feed.poll("worker-0") == []  # already seen
        feed.publish([f2])
        assert feed.poll("worker-0") == [f2]
        # A different consumer sees everything from the start
        assert feed.poll("worker-1") == [f1, f2]

    @pytest.mark.asyncio
    async def test_build_agent_injects_audit_findings(self, tool_context: ToolContext) -> None:
        from noscope.logging.events import EventLog, RunDir
        from noscope.tools.dispatcher import ToolDispatcher

        seen_messages: list[list[Message]] = []

        class RecordingProvider(FakeProvider):
            async def complete(
                self,
                messages: list[Message],
                tools: list[ToolSchema] | None = None,
                model: str | None = None,
                json_schema: dict[str, Any] | None = None,
            ) -> LLMResponse:
                seen_messages.append(list(messages))
                return await super().complete(messages, tools, model, json_schema)

        feed = AuditFeed()
        feed.publish([{"type": "invalid_json", "message": "package.json is invalid"}])

        run_dir = RunDir(base=tool_context.workspace.parent / "runs")
        event_log = EventLog(run_dir)
        agent = BuildAgent(
            agent_id="worker-0",
            provider=RecordingProvider([]),
            dispatcher=ToolDispatcher(),
            context=tool_context,
            event_log=event_log,
            deadline=tool_context.deadline,
            feed=feed,
        )
        await agent.run([PlanTask(id="t1", title="Build", kind="edit")], "You are a builder.")
        event_log.close()

        assert seen_messages, "provider was never called"
        injected = [
            m for m in seen_messages[-1] if m.role == "user" and "AUDIT ALERT" in (m.content or "")
        ]
        assert len(injected) == 1
        assert "package.json is invalid" in injected[0].content


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


class TestTokenBudget:
    def test_exceeded_only_with_budget(self) -> None:
        from noscope.phases import TokenTracker

        unbounded = TokenTracker()
        unbounded.add(Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        assert unbounded.exceeded() is False  # no budget -> never exceeded

        capped = TokenTracker(budget=100)
        capped.add(Usage(input_tokens=60, output_tokens=30))
        assert capped.total() == 90
        assert capped.exceeded() is False
        capped.add(Usage(input_tokens=20))
        assert capped.exceeded() is True

    def test_total_counts_cache_tokens(self) -> None:
        from noscope.phases import TokenTracker

        t = TokenTracker()
        t.add(
            Usage(
                input_tokens=1,
                output_tokens=2,
                cache_creation_input_tokens=3,
                cache_read_input_tokens=4,
            )
        )
        assert t.total() == 10

    @pytest.mark.asyncio
    async def test_build_agent_stops_on_budget(self, tool_context: ToolContext) -> None:
        from noscope.logging.events import EventLog, RunDir
        from noscope.phases import TokenTracker
        from noscope.tools.dispatcher import ToolDispatcher

        # Each call reports usage that exceeds the tiny budget; the agent should
        # make at most one model call and then stop rather than looping.
        calls = {"n": 0}

        class CountingProvider(FakeProvider):
            async def complete(
                self,
                messages: list[Message],
                tools: list[ToolSchema] | None = None,
                model: str | None = None,
                json_schema: Any | None = None,
                effort: str | None = None,
            ) -> LLMResponse:
                calls["n"] += 1
                return LLMResponse(content="working", usage=Usage(input_tokens=1000))

        run_dir = RunDir(base=tool_context.workspace.parent / "runs")
        event_log = EventLog(run_dir)
        tokens = TokenTracker(budget=100)
        agent = BuildAgent(
            agent_id="w",
            provider=CountingProvider([]),
            dispatcher=ToolDispatcher(),
            context=tool_context,
            event_log=event_log,
            deadline=tool_context.deadline,
            tokens=tokens,
        )
        await agent.run([PlanTask(id="t1", title="Build", kind="edit")], "Build it.")
        event_log.close()
        assert calls["n"] == 1  # stopped after the first over-budget call


class TestAuditSyntaxChecks:
    @pytest.mark.asyncio
    async def test_detects_python_syntax_error(self, tool_context: ToolContext) -> None:
        from noscope.tools.dispatcher import ToolDispatcher
        from noscope.tools.shell import ShellTool

        (tool_context.workspace / "requirements.txt").write_text("flask\n")
        (tool_context.workspace / "app.py").write_text("def broken(:\n")

        dispatcher = ToolDispatcher()
        dispatcher.register(ShellTool())
        audit = AuditAgent(
            dispatcher=dispatcher,
            context=tool_context,
            event_log=tool_context.event_log,
            deadline=tool_context.deadline,
        )
        findings = await audit._run_checks()
        assert any(f["type"] == "syntax_error" for f in findings)

    @pytest.mark.asyncio
    async def test_clean_python_passes(self, tool_context: ToolContext) -> None:
        from noscope.tools.dispatcher import ToolDispatcher
        from noscope.tools.shell import ShellTool

        (tool_context.workspace / "requirements.txt").write_text("flask\n")
        (tool_context.workspace / "app.py").write_text("def fine():\n    return 1\n")

        dispatcher = ToolDispatcher()
        dispatcher.register(ShellTool())
        audit = AuditAgent(
            dispatcher=dispatcher,
            context=tool_context,
            event_log=tool_context.event_log,
            deadline=tool_context.deadline,
        )
        findings = await audit._run_checks()
        assert not any(f["type"] == "syntax_error" for f in findings)
