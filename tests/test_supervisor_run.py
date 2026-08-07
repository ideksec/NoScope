"""End-to-end tests for the multi-agent supervisor.

The partitioning helpers are unit-tested elsewhere; this file exercises
``Supervisor.run`` itself — parallel setup, worker fan-out over the
partitioned streams, the concurrent audit agent, and the shared write ledger.
That machinery is the most intricate part of the harness and the part that has
never actually been *run* in a test. A scripted provider drives it: no key, no
network.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noscope.conflicts import WriteLedger
from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage
from noscope.logging.events import EventLog
from noscope.planning.models import PlanOutput, PlanTask
from noscope.supervisor import Supervisor
from noscope.tools.base import ToolContext
from noscope.tools.dispatcher import ToolDispatcher
from noscope.tools.filesystem import WriteFileTool


class _WorkerProvider:
    """Each build agent writes the file for its current task, then marks it done.

    The agent identity is read out of the system prompt, so concurrent agents
    stay independent without the provider needing any cross-agent state beyond
    the lock that guards its own counters.
    """

    def __init__(self) -> None:
        self.calls = 0
        self._written: set[str] = set()
        self._lock = asyncio.Lock()

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        async with self._lock:
            self.calls += 1
        names = {t.name for t in (tools or [])}
        usage = Usage()

        # The audit agent gets no mark_task_complete tool — it just reports.
        if "mark_task_complete" not in names:
            return LLMResponse(content='{"findings": []}', stop_reason="end_turn", usage=usage)

        pending = _pending_task_ids(messages)
        if not pending:
            return LLMResponse(content="nothing to do", stop_reason="end_turn", usage=usage)

        task_id = pending[0]
        async with self._lock:
            needs_write = task_id not in self._written
            if needs_write:
                self._written.add(task_id)

        if needs_write:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id=f"w-{task_id}",
                        name="write_file",
                        arguments={"path": f"{task_id}.py", "content": f"# {task_id}\n"},
                    )
                ],
                usage=usage,
            )
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"d-{task_id}", name="mark_task_complete", arguments={"task_id": task_id}
                )
            ],
            usage=usage,
        )


def _pending_task_ids(messages: list[Message]) -> list[str]:
    """Task ids this agent was assigned, minus the ones it already marked done."""
    assigned: list[str] = []
    for m in messages:
        if m.role == "user" and "Execute these tasks" in (m.content or ""):
            for line in (m.content or "").splitlines():
                if line.startswith("- ["):
                    assigned.append(line[3 : line.index("]")])
    done = {
        str(tc.arguments.get("task_id"))
        for m in messages
        for tc in (m.tool_calls or [])
        if tc.name == "mark_task_complete"
    }
    return [t for t in assigned if t not in done]


def _dispatcher() -> ToolDispatcher:
    d = ToolDispatcher()
    d.register(WriteFileTool())
    return d


def _plan() -> PlanOutput:
    # t1 is setup (runs alone). t2/t3 form one dependency chain, t4/t5 another,
    # so partitioning has two independent streams to fan out across workers.
    return PlanOutput(
        tasks=[
            PlanTask(id="t1", title="Setup", kind="edit", description="scaffold"),
            PlanTask(id="t2", title="A", kind="edit", description="a", depends_on=["t1"]),
            PlanTask(id="t3", title="B", kind="edit", description="b", depends_on=["t2"]),
            PlanTask(id="t4", title="C", kind="edit", description="c", depends_on=["t1"]),
            PlanTask(id="t5", title="D", kind="edit", description="d", depends_on=["t4"]),
        ]
    )


@pytest.mark.asyncio
class TestSupervisorRun:
    async def test_all_tasks_complete_across_parallel_workers(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        ledger = WriteLedger()
        context = replace(tool_context, workspace=tmp_workspace, write_ledger=ledger)
        provider = _WorkerProvider()

        supervisor = Supervisor(
            provider=provider,  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=2,
        )
        tasks = await supervisor.run(_plan(), tmp_workspace)

        assert [t.id for t in tasks if not t.completed] == [], "every task should finish"
        # The workers really wrote files, rather than only claiming completion.
        for task_id in ("t2", "t3", "t4", "t5"):
            assert (tmp_workspace / f"{task_id}.py").exists()

    async def test_work_is_split_across_two_workers(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # Two independent chains and two workers should mean genuine fan-out,
        # not one worker doing everything while the other idles.
        ledger = WriteLedger()
        context = replace(tool_context, workspace=tmp_workspace, write_ledger=ledger)

        supervisor = Supervisor(
            provider=_WorkerProvider(),  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=2,
        )
        await supervisor.run(_plan(), tmp_workspace)

        writers = {agent for agent in ("worker-0", "worker-1") if ledger.paths_touched_by(agent)}
        assert writers == {"worker-0", "worker-1"}

    async def test_disjoint_streams_produce_no_write_conflicts(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # Partitioning exists so workers don't collide. When each task owns its
        # own file, the ledger should stay clean — a conflict here would mean
        # the partitioning put dependent work on different workers.
        ledger = WriteLedger()
        context = replace(tool_context, workspace=tmp_workspace, write_ledger=ledger)

        supervisor = Supervisor(
            provider=_WorkerProvider(),  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=2,
        )
        await supervisor.run(_plan(), tmp_workspace)

        assert ledger.conflicts == [], ledger.summary()

    async def test_single_worker_still_completes_everything(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # --workers 1 collapses every stream onto one agent; the merge path in
        # _partition_tasks must still preserve dependency order.
        context = replace(tool_context, workspace=tmp_workspace, write_ledger=WriteLedger())

        supervisor = Supervisor(
            provider=_WorkerProvider(),  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=1,
        )
        tasks = await supervisor.run(_plan(), tmp_workspace)
        assert all(t.completed for t in tasks)

    async def test_empty_plan_is_a_no_op(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        supervisor = Supervisor(
            provider=_WorkerProvider(),  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=replace(tool_context, workspace=tmp_workspace),
            event_log=event_log,
            deadline=tool_context.deadline,
            max_workers=2,
        )
        assert await supervisor.run(PlanOutput(tasks=[]), tmp_workspace) == []

    async def test_build_does_not_wait_for_the_auditor(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # The auditor loops until BUILD is nearly over. Gathering it alongside
        # the workers meant a build finishing in seconds still sat idle for the
        # rest of BUILD's budget — burning the timebox this tool exists to
        # protect. With a 300s deadline this test would take minutes if the
        # supervisor waited on the audit task.
        import time

        context = replace(tool_context, workspace=tmp_workspace, write_ledger=WriteLedger())
        supervisor = Supervisor(
            provider=_WorkerProvider(),  # type: ignore[arg-type]
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=2,
        )

        started = time.monotonic()
        await supervisor.run(_plan(), tmp_workspace)
        elapsed = time.monotonic() - started

        assert context.deadline.total_seconds >= 60, "fixture must have a long deadline to matter"
        assert elapsed < 10, f"supervisor blocked on the audit agent for {elapsed:.1f}s"


class _FlakyProvider(_WorkerProvider):
    """Fails the first `fail_first` calls, then behaves normally."""

    def __init__(self, fail_first: int) -> None:
        super().__init__()
        self._remaining_failures = fail_first

    async def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("transient upstream error")
        return await super().complete(messages, **kwargs)


class _AlwaysFailingProvider(_WorkerProvider):
    async def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        raise RuntimeError("upstream is down")


@pytest.mark.asyncio
class TestAgentResilience:
    def _supervisor(self, provider: Any, context: ToolContext, event_log: EventLog) -> Supervisor:
        return Supervisor(
            provider=provider,
            dispatcher=_dispatcher(),
            context=context,
            event_log=event_log,
            deadline=context.deadline,
            max_workers=2,
        )

    async def test_a_transient_failure_does_not_cost_the_stream(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # One blip used to propagate out of the worker and take every
        # remaining task in its stream with it.
        context = replace(tool_context, workspace=tmp_workspace, write_ledger=WriteLedger())
        supervisor = self._supervisor(_FlakyProvider(fail_first=2), context, event_log)

        tasks = await supervisor.run(_plan(), tmp_workspace)
        assert all(t.completed for t in tasks)

    async def test_persistent_failure_stands_down_instead_of_spinning(
        self, tool_context: ToolContext, event_log: EventLog, tmp_workspace: Path
    ) -> None:
        # A provider that is simply down must not burn 200 iterations per
        # worker; each agent gives up after MAX_CONSECUTIVE_LLM_FAILURES.
        from noscope.agents import MAX_CONSECUTIVE_LLM_FAILURES

        context = replace(tool_context, workspace=tmp_workspace, write_ledger=WriteLedger())
        provider = _AlwaysFailingProvider()
        supervisor = self._supervisor(provider, context, event_log)

        tasks = await supervisor.run(_plan(), tmp_workspace)

        # Setup (2 agents) + workers (2 streams), each capped at the retry limit.
        assert provider.calls <= 4 * MAX_CONSECUTIVE_LLM_FAILURES
        # The run still returns its tasks rather than raising.
        assert len(tasks) == 5
