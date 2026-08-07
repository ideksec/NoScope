"""Tests for orchestrator — uses mocked LLM."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from noscope.llm.base import LLMResponse, Message, ToolCall, ToolSchema, Usage
from noscope.planning.models import AcceptancePlan, PlanOutput, PlanTask
from noscope.spec.models import AcceptanceCheck, SpecInput

# A minimal, valid plan the scripted provider returns for the PLAN phase.
_PLAN_JSON = json.dumps(
    {
        "requested_capabilities": [
            {"cap": "workspace_rw", "why": "write files", "risk": "low"},
            {"cap": "shell_exec", "why": "run checks", "risk": "medium"},
        ],
        "tasks": [
            {
                "id": "t1",
                "title": "Set up project",
                "kind": "edit",
                "priority": "mvp",
                "description": "create app.py",
                "depends_on": [],
            }
        ],
        "mvp_definition": ["app.py exists"],
        "exclusions": [],
        "acceptance_plan": [{"name": "app exists", "cmd": "test -f app.py", "must_pass": True}],
    }
)


class ScriptedProvider:
    """A fake provider that drives every phase of a full run deterministically.

    It decides what to return from the call's shape — a structured-output
    request is the planner, a tool set containing confirm_running is VERIFY, one
    containing mark_task_complete is a build agent, and a plain call is HANDOFF.
    No network, no API key.
    """

    def __init__(self) -> None:
        self._structure_calls = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        system = messages[0].content if messages else ""
        names = {t.name for t in (tools or [])}

        if json_schema is not None:  # PLAN
            return LLMResponse(content=_PLAN_JSON, usage=Usage(input_tokens=50, output_tokens=50))

        if "confirm_running" in names:  # VERIFY — propose a proof the harness runs
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="v1",
                        name="confirm_running",
                        arguments={"command": "test -f app.py", "summary": "app.py present"},
                    )
                ],
                usage=Usage(input_tokens=20, output_tokens=10),
            )

        if "mark_task_complete" in names:  # BUILD agent
            if "DEPS agent" in system:  # the deps agent has nothing to do here
                return LLMResponse(content="deps ready", stop_reason="end_turn", usage=Usage())
            # structure agent: write the file, then mark the task done
            self._structure_calls += 1
            if self._structure_calls == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="write_file",
                            arguments={"path": "app.py", "content": "print('hello')\n"},
                        )
                    ],
                    usage=Usage(input_tokens=30, output_tokens=20),
                )
            return LLMResponse(
                tool_calls=[
                    ToolCall(id="m1", name="mark_task_complete", arguments={"task_id": "t1"})
                ],
                usage=Usage(input_tokens=10, output_tokens=5),
            )

        # HANDOFF — a plain call with no tools and no schema
        return LLMResponse(
            content="# Handoff\n\nBuilt app.py.\n",
            stop_reason="end_turn",
            usage=Usage(input_tokens=40, output_tokens=30),
        )


class TestFullPipeline:
    def test_run_produces_verified_artifact_and_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run entirely inside a temp cwd so .noscope/ and outputs stay isolated,
        # and give settings a dummy key so the provider constructs (no network).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "test-key")

        from noscope.config.settings import load_settings
        from noscope.orchestrator import Orchestrator

        orch = Orchestrator(load_settings(), console=Console(file=io.StringIO()))
        orch.provider = ScriptedProvider()  # type: ignore[assignment]

        spec = SpecInput(name="Demo", timebox="5m")
        workspace = tmp_path / "out"
        run_path = asyncio.run(orch.run(spec_input=spec, output_dir=workspace, auto_approve=True))

        # The build produced the artifact...
        assert (workspace / "app.py").exists()
        # ...and the run directory holds the full record, always including a report.
        assert (run_path / "plan.json").exists()
        assert (run_path / "contract.json").exists()
        handoff = (run_path / "handoff.md").read_text()
        assert handoff.strip()

        # The event log proves the pipeline reached an independently-confirmed
        # verification and ran to completion.
        events = (run_path / "events.jsonl").read_text()
        types = {json.loads(line)["type"] for line in events.splitlines() if line.strip()}
        assert "verify.pass" in types
        assert "run.complete" in types
        assert "acceptance.check" in types

    def test_sandbox_preflight_aborts_before_spending_tokens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unusable --sandbox must stop the run *before* PLAN. Discovering it
        # mid-build wastes both the timebox and the tokens already spent.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "test-key")

        async def no_docker(timeout: float = 15.0) -> str:
            return "Docker is not available in this test."

        monkeypatch.setattr("noscope.orchestrator.preflight_docker", no_docker)

        from noscope.config.settings import load_settings
        from noscope.orchestrator import Orchestrator

        calls = 0

        class _CountingProvider(ScriptedProvider):
            async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
                nonlocal calls
                calls += 1
                return await super().complete(*args, **kwargs)

        orch = Orchestrator(load_settings(), console=Console(file=io.StringIO()))
        orch.provider = _CountingProvider()  # type: ignore[assignment]

        run_path = asyncio.run(
            orch.run(
                spec_input=SpecInput(name="Demo", timebox="5m"),
                output_dir=tmp_path / "out",
                sandbox=True,
                auto_approve=True,
            )
        )

        types = [
            json.loads(line)["type"]
            for line in (run_path / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert "run.aborted" in types
        assert calls == 0, "aborted before PLAN, so no model call should have happened"

    def test_handoff_runs_even_when_planning_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If a phase raises, the run must still produce a handoff report —
        # the "always produce output" guarantee.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "test-key")

        from noscope.config.settings import load_settings
        from noscope.orchestrator import Orchestrator

        class ExplodingProvider(ScriptedProvider):
            async def complete(self, messages: Any, **kw: Any) -> LLMResponse:
                if kw.get("json_schema") is not None:
                    raise RuntimeError("planner boom")
                return await super().complete(messages, **kw)

        orch = Orchestrator(load_settings(), console=Console(file=io.StringIO()))
        orch.provider = ExplodingProvider()  # type: ignore[assignment]

        run_path = asyncio.run(
            orch.run(
                spec_input=SpecInput(name="Demo", timebox="5m"),
                output_dir=tmp_path / "out",
                auto_approve=True,
            )
        )
        # Even though PLAN raised, a handoff report exists.
        assert (run_path / "handoff.md").read_text().strip()


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


class TestDryRun:
    def test_dry_run_completes_without_a_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole pipeline must run with no API key and no network: this is
        # the smoke test users run before spending tokens.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "dry-run")

        from noscope.config.settings import load_settings
        from noscope.llm.dryrun import DRY_RUN_FILE
        from noscope.orchestrator import Orchestrator

        orch = Orchestrator(load_settings(), console=Console(file=io.StringIO()), dry_run=True)
        workspace = tmp_path / "out"
        run_path = asyncio.run(
            orch.run(
                spec_input=SpecInput(name="Dry", timebox="3m"),
                output_dir=workspace,
                auto_approve=True,
            )
        )

        assert (workspace / DRY_RUN_FILE).exists()
        assert (run_path / "handoff.md").read_text().strip()
        events = (run_path / "events.jsonl").read_text()
        types = {json.loads(line)["type"] for line in events.splitlines() if line.strip()}
        assert "verify.pass" in types
        assert "run.complete" in types

    def test_dry_run_spends_no_tokens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "dry-run")

        from noscope.config.settings import load_settings
        from noscope.orchestrator import Orchestrator

        orch = Orchestrator(load_settings(), console=Console(file=io.StringIO()), dry_run=True)
        run_path = asyncio.run(
            orch.run(
                spec_input=SpecInput(name="Dry", timebox="3m"),
                output_dir=tmp_path / "out",
                auto_approve=True,
            )
        )
        complete = [
            json.loads(line)
            for line in (run_path / "events.jsonl").read_text().splitlines()
            if line.strip() and json.loads(line)["type"] == "run.complete"
        ][0]
        assert complete["data"]["input_tokens"] == 0
        assert complete["data"]["output_tokens"] == 0
