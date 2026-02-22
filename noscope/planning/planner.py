"""LLM-based plan generation from spec."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from noscope.llm.base import LLMProvider, Message
from noscope.planning.models import PlanOutput
from noscope.spec.models import SpecInput

if TYPE_CHECKING:
    from noscope.phases import TokenTracker

PLAN_SYSTEM_PROMPT = """\
You are a software architect planning an MVP build within a strict timebox.

Given a project specification, produce a structured JSON plan. Your output must be valid JSON matching this schema:

{
  "requested_capabilities": [
    {"cap": "workspace_rw|shell_exec|net_http|git|docker|secrets:<NAME>", "why": "justification", "risk": "low|medium|high"}
  ],
  "tasks": [
    {"id": "t1", "title": "Task name", "kind": "edit|shell|test", "priority": "mvp|stretch", "description": "What to do"}
  ],
  "mvp_definition": ["What counts as done"],
  "exclusions": ["What is explicitly NOT being built"],
  "acceptance_plan": [
    {"name": "check name", "cmd": "shell command or null", "must_pass": true}
  ]
}

CRITICAL RULES:
- THE APP MUST RUN. A broken app is a total failure regardless of how many features it has.
- Always request workspace_rw and shell_exec capabilities
- One of the FIRST MVP tasks MUST be to create the core app structure and install dependencies
- The LAST MVP task MUST be "Install dependencies and verify app starts"
- Order tasks: project setup → core features → polish → install+verify
- Acceptance checks must use paths that match where files are actually created
- Do NOT spend tasks on mock data files or placeholder content — inline minimal data in code
- Scale ambition to timebox:
  - ≤5m: Bare minimum working app (3-4 MVP tasks). Simple is better than ambitious.
  - 5-10m: Solid MVP with core features (5-6 MVP tasks). Good structure and styling.
  - 10-20m: Full-featured MVP (6-8 MVP tasks + stretch tasks). Polish the UI, add edge cases.
  - 20m+: Comprehensive app (8+ MVP tasks + stretch tasks). Build it properly with good UX.
- Mark stretch tasks for features to add if time permits — these make the app BETTER, not just WORKING

Respond ONLY with the JSON object, no markdown fences or explanation.
"""


async def plan(
    spec: SpecInput, provider: LLMProvider, tokens: TokenTracker | None = None
) -> PlanOutput:
    """Generate a build plan from a spec using an LLM."""
    user_content = f"""Project: {spec.name}
Timebox: {spec.timebox} ({spec.timebox_seconds}s)
Constraints: {json.dumps(spec.constraints)}
Acceptance criteria: {json.dumps([a.raw for a in spec.acceptance])}
Stack preferences: {json.dumps(spec.stack_prefs or [])}
Repo mode: {spec.repo_mode}

Spec body:
{spec.body}
"""

    messages = [
        Message(role="system", content=PLAN_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]

    max_retries = 2
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        response = await provider.complete(messages)
        if tokens:
            tokens.add(response.usage)
        try:
            raw = response.content.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

            data = json.loads(raw)
            return PlanOutput.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            last_error = e
            if attempt < max_retries:
                messages.append(Message(role="assistant", content=response.content))
                messages.append(
                    Message(
                        role="user",
                        content=f"Your response was not valid JSON. Error: {e}. Please try again with valid JSON only.",
                    )
                )

    raise ValueError(
        f"Failed to generate valid plan after {max_retries + 1} attempts: {last_error}"
    )
