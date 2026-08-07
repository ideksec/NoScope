"""Conversation context management.

Agent loops append to a message list every turn and can run for hundreds of
iterations, with whole-file reads and large command outputs landing in context.
Left alone that grows until the request fails on context length — and a failed
request costs the worker its remaining tasks.

Two cheap defenses, both applied before the request is sent:

* ``truncate_tool_output`` caps any single tool result.
* ``trim_history`` drops the oldest turns once the conversation exceeds a
  character budget, keeping the system prompt and the original briefing.
"""

from __future__ import annotations

from noscope.llm.base import Message

# Cap on a single tool result placed into the model's context.
MAX_TOOL_RESULT_CHARS = 10_000

# Budget for the whole conversation (characters, a cheap proxy for tokens —
# roughly 4 chars/token, so ~150k chars ≈ 37k tokens).
MAX_HISTORY_CHARS = 150_000

_ELISION_NOTE = (
    "[Earlier turns in this conversation were dropped to stay within the "
    "context limit. The work already done is on disk — read the files you "
    "need rather than relying on memory of earlier output.]"
)


def truncate_tool_output(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Cap a tool result, keeping the head and tail (where errors usually are)."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [{omitted:,} characters omitted] ...\n{text[-tail:]}"


def _message_size(msg: Message) -> int:
    size = len(msg.content or "")
    for tc in msg.tool_calls or []:
        size += len(tc.name) + len(str(tc.arguments))
    return size


def trim_history(messages: list[Message], max_chars: int = MAX_HISTORY_CHARS) -> list[Message]:
    """Drop the oldest turns when the conversation exceeds ``max_chars``.

    Always preserved: leading system messages and the first user message (the
    task briefing). The kept tail never begins with a ``tool`` message, so a
    tool result is never separated from the assistant turn that requested it —
    an orphaned tool result is an API error, not just wasted context.

    Returns the original list when it already fits.
    """
    if sum(_message_size(m) for m in messages) <= max_chars:
        return messages

    # Preamble: leading system messages plus the first user message.
    preamble_end = 0
    while preamble_end < len(messages) and messages[preamble_end].role == "system":
        preamble_end += 1
    if preamble_end < len(messages) and messages[preamble_end].role == "user":
        preamble_end += 1
    preamble = messages[:preamble_end]
    rest = messages[preamble_end:]

    budget = max_chars - sum(_message_size(m) for m in preamble)
    if budget <= 0:
        # Even the preamble overflows; keep it and the most recent turn.
        return preamble + _valid_tail(rest[-1:])

    # Walk backwards keeping as much recent history as fits.
    kept: list[Message] = []
    used = 0
    for msg in reversed(rest):
        size = _message_size(msg)
        if used + size > budget:
            break
        kept.append(msg)
        used += size
    kept.reverse()

    if len(kept) == len(rest):
        return messages

    return preamble + [Message(role="user", content=_ELISION_NOTE)] + _valid_tail(kept)


def _valid_tail(messages: list[Message]) -> list[Message]:
    """Drop leading ``tool`` messages so no tool result is orphaned."""
    start = 0
    while start < len(messages) and messages[start].role == "tool":
        start += 1
    return messages[start:]
