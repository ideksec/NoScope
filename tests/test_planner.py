"""Tests for the LLM-based planner."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from noscope.llm.base import LLMResponse, Message, StreamChunk, ToolSchema, Usage
from noscope.phases import TokenTracker
from noscope.planning.models import PlanOutput
from noscope.planning.planner import plan
from noscope.spec.models import AcceptanceCheck, SpecInput


def _make_spec() -> SpecInput:
    return SpecInput(
        name="Test App",
        timebox="5m",
        constraints=["Use Python"],
        acceptance=[AcceptanceCheck.from_string("cmd: python --version")],
        body="# Test App\n\nBuild a simple test app.",
    )


def _valid_plan_json() -> str:
    return json.dumps(
        {
            "requested_capabilities": [
                {"cap": "workspace_rw", "why": "Write files", "risk": "low"},
                {"cap": "shell_exec", "why": "Run commands", "risk": "medium"},
            ],
            "tasks": [
                {
                    "id": "t1",
                    "title": "Create app",
                    "kind": "edit",
                    "priority": "mvp",
                    "description": "Create the app",
                },
                {
                    "id": "t2",
                    "title": "Verify",
                    "kind": "shell",
                    "priority": "mvp",
                    "description": "Verify it runs",
                },
            ],
            "mvp_definition": ["App runs"],
            "exclusions": ["Tests"],
            "acceptance_plan": [
                {"name": "Python check", "cmd": "python --version", "must_pass": True},
            ],
        }
    )


class FakeProvider:
    """Fake LLM provider for testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return LLMResponse(
            content=self._responses[idx],
            usage=Usage(input_tokens=100, output_tokens=50),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta_text="", is_final=True)


class TestPlanner:
    @pytest.mark.asyncio
    async def test_valid_plan_generation(self) -> None:
        provider = FakeProvider([_valid_plan_json()])
        result = await plan(_make_spec(), provider)
        assert isinstance(result, PlanOutput)
        assert len(result.tasks) == 2
        assert result.tasks[0].title == "Create app"
        assert len(result.requested_capabilities) == 2

    @pytest.mark.asyncio
    async def test_plan_with_markdown_fences(self) -> None:
        fenced = f"```json\n{_valid_plan_json()}\n```"
        provider = FakeProvider([fenced])
        result = await plan(_make_spec(), provider)
        assert isinstance(result, PlanOutput)
        assert len(result.tasks) == 2

    @pytest.mark.asyncio
    async def test_plan_retry_on_invalid_json(self) -> None:
        provider = FakeProvider(["not json", _valid_plan_json()])
        result = await plan(_make_spec(), provider)
        assert isinstance(result, PlanOutput)
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_plan_fails_after_retries(self) -> None:
        provider = FakeProvider(["bad", "still bad", "nope"])
        with pytest.raises(ValueError, match="Failed to generate valid plan"):
            await plan(_make_spec(), provider)

    @pytest.mark.asyncio
    async def test_token_tracking(self) -> None:
        provider = FakeProvider([_valid_plan_json()])
        tracker = TokenTracker()
        await plan(_make_spec(), provider, tokens=tracker)
        assert tracker.input_tokens == 100
        assert tracker.output_tokens == 50
