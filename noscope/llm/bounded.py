"""Wrap a provider so no single request can outlive the timebox.

NoScope's headline promise is a hard deadline, but the deadline is
*cooperative* — agents check it between iterations, so it cannot interrupt a
request already in flight. Both SDKs default to a 600-second read timeout and
retry on top of that, which means one stalled call could block for tens of
minutes and blow a five-minute timebox entirely.

This wrapper closes that gap at the one place every call passes through.
Because ``asyncio.wait_for`` wraps the whole SDK call, the bound covers the
SDK's internal retries too, not just a single attempt.

A floor is deliberate: HANDOFF runs *after* the deadline by design ("always
produce output"), so a request is never given zero time — past the deadline it
still gets ``MIN_REQUEST_TIMEOUT`` to produce the report.
"""

from __future__ import annotations

import asyncio
from typing import Any

from noscope.deadline import Deadline
from noscope.llm.base import LLMProvider, LLMResponse, Message, ToolSchema

# Longest any single request may run, however much timebox is left. Generous
# enough for a long structured plan, short enough that a stall is survivable.
DEFAULT_REQUEST_TIMEOUT = 120.0

# Never give a request less than this, even past the deadline — HANDOFF still
# has to be able to write its report.
MIN_REQUEST_TIMEOUT = 30.0


class RequestTimeoutError(Exception):
    """A single LLM request exceeded its share of the timebox."""


class DeadlineBoundProvider:
    """Delegates to a real provider, capping how long any one call may take."""

    def __init__(
        self,
        inner: LLMProvider,
        deadline: Deadline,
        max_seconds: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._inner = inner
        self._deadline = deadline
        self._max_seconds = max_seconds

    def timeout_for_now(self) -> float:
        """Seconds to allow the next request.

        The floor applies to the *remaining timebox*, not to the configured
        cap: an expired deadline must not starve HANDOFF, but an explicitly
        configured ``max_seconds`` is a deliberate choice and always wins.
        """
        return min(self._max_seconds, max(MIN_REQUEST_TIMEOUT, self._deadline.remaining()))

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> LLMResponse:
        timeout = self.timeout_for_now()
        try:
            return await asyncio.wait_for(
                self._inner.complete(
                    messages,
                    tools=tools,
                    model=model,
                    json_schema=json_schema,
                    effort=effort,
                ),
                timeout=timeout,
            )
        except TimeoutError as e:
            raise RequestTimeoutError(
                f"The model did not respond within {timeout:.0f}s. "
                "The request was abandoned so the run keeps its timebox."
            ) from e

    def __getattr__(self, name: str) -> Any:
        # Anything else (provider-specific attributes, test hooks) passes through.
        return getattr(self._inner, name)
