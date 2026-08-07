"""Tests for the deadline-bound provider wrapper."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from noscope.deadline import Deadline
from noscope.llm.base import LLMResponse, Message, Usage
from noscope.llm.bounded import (
    MIN_REQUEST_TIMEOUT,
    DeadlineBoundProvider,
    RequestTimeoutError,
)


class _SlowProvider:
    """Blocks for `delay` seconds, recording the kwargs it was handed."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.provider_specific = "passthrough-value"

    async def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return LLMResponse(content="ok", usage=Usage())


@pytest.mark.asyncio
class TestDeadlineBoundProvider:
    async def test_fast_calls_pass_straight_through(self) -> None:
        inner = _SlowProvider()
        p = DeadlineBoundProvider(inner, Deadline(300))
        result = await p.complete([Message(role="user", content="hi")], effort="high")

        assert result.content == "ok"
        assert inner.calls[0]["effort"] == "high"

    async def test_a_stalled_request_is_abandoned(self) -> None:
        # Without this, a hung request outlives the timebox entirely: the SDKs
        # default to a 600s read timeout and retry on top of that, and the
        # deadline is only checked between agent iterations.
        p = DeadlineBoundProvider(_SlowProvider(delay=5), Deadline(300), max_seconds=0.05)

        with pytest.raises(RequestTimeoutError) as exc:
            await p.complete([Message(role="user", content="hi")])
        assert "did not respond" in str(exc.value)

    async def test_timeout_is_capped_by_max_seconds(self) -> None:
        # Lots of timebox left, so the configured cap is what binds.
        p = DeadlineBoundProvider(_SlowProvider(), Deadline(3600), max_seconds=90)
        assert p.timeout_for_now() == 90

    async def test_timeout_shrinks_with_the_remaining_timebox(self) -> None:
        p = DeadlineBoundProvider(_SlowProvider(), Deadline(60), max_seconds=120)
        # 60s left, 120s cap -> the deadline is the binding constraint.
        assert MIN_REQUEST_TIMEOUT < p.timeout_for_now() <= 60

    async def test_handoff_still_gets_time_after_the_deadline(self) -> None:
        # HANDOFF runs *after* the deadline on purpose — the "always produce
        # output" guarantee. A zero-length timeout there would mean an expired
        # run silently produces no report at all.
        expired = Deadline(0)
        p = DeadlineBoundProvider(_SlowProvider(), expired)

        assert expired.remaining() == 0
        assert p.timeout_for_now() == MIN_REQUEST_TIMEOUT

        result = await p.complete([Message(role="user", content="write the report")])
        assert result.content == "ok"

    async def test_unknown_attributes_reach_the_wrapped_provider(self) -> None:
        p = DeadlineBoundProvider(_SlowProvider(), Deadline(300))
        assert p.provider_specific == "passthrough-value"

    async def test_configured_cap_beats_the_floor(self) -> None:
        # The floor guards against an expired deadline, not against a small
        # request_timeout the operator chose on purpose.
        p = DeadlineBoundProvider(_SlowProvider(), Deadline(3600), max_seconds=5)
        assert p.timeout_for_now() == 5
