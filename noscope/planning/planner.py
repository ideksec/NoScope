"""LLM-based plan generation from spec."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from noscope.llm.base import LLMProvider, Message
from noscope.planning.models import PlanOutput
from noscope.spec.models import SpecInput

if TYPE_CHECKING:
    from noscope.phases import TokenTracker

PLAN_SYSTEM_PROMPT = """\
You are a software architect planning an MVP build within a strict timebox.

The plan is executed by several agents in parallel: task t1 (setup) runs first
on its own, then the remaining tasks run concurrently across workers. Design
tasks to be independent where you can, and have each own specific files so
parallel workers don't collide — name those files in the description.

The single most important outcome is that the built app actually runs; a broken
app is a failure no matter how many features it has. Favor fewer, working
features over more, half-working ones.

Task rules:
- Always request the workspace_rw and shell_exec capabilities.
- t1 is "Set up project structure and install dependencies" and runs alone; it
  writes the dependency manifest and creates the layout. Every other task
  depends on t1 (or on another task it genuinely needs).
- Don't spend a task on mock-data or placeholder files — inline minimal data
  in code instead.

Scope the ambition to the timebox — a small box needs a simpler stack and fewer
tasks so the result actually runs in time:
- ≤5m: 2-3 tasks, simplest possible stack (vanilla HTML/CSS/JS, a single Flask
  file, or Express) — no TypeScript, React, build tools, or Tailwind.
- 5-10m: 3-5 tasks, lightweight frameworks (Flask, Express); avoid heavy build chains.
- 10-20m: 5-7 tasks; frameworks and TypeScript are fine if the spec needs them.
- 20m+: full stack is fine, 8+ tasks plus stretch tasks. Mark features you'd
  only add with spare time as stretch.

Acceptance checks should answer "does it start?", not test features — 1-2 checks
at most (one to install deps, one to start the app). Commands run from the
workspace root with relative paths (`python3 app.py`, not
`cd project_name && python3 app.py`), and files live at the root, not in a
subdirectory named after the project.

Do not plan tasks or checks that use interactive scaffolding (create-react-app,
npm create, npx create-*, yarn create) — they hang and burn the whole timebox.
Write package.json / requirements.txt by hand, then install.

Use python3 / python3 -m pip, not python / pip.
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

    # Request structured output. On current models this is schema-enforced, so
    # the response is valid JSON; on a model that can't enforce it, the provider
    # returns prose and the fence-strip below is the safety net. One corrective
    # re-ask covers the rare enforced-but-still-malformed case.
    schema = PlanOutput.model_json_schema()
    last_error: Exception | None = None

    for _attempt in range(2):
        response = await provider.complete(messages, json_schema=schema, effort="high")
        if tokens:
            tokens.add(response.usage)
        try:
            return PlanOutput.model_validate(json.loads(_strip_fences(response.content)))
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            messages.append(Message(role="assistant", content=response.content))
            messages.append(
                Message(
                    role="user",
                    content=(
                        f"That did not parse as a valid plan ({e}). "
                        "Reply with the plan as a single valid JSON object and nothing else."
                    ),
                )
            )

    raise ValueError(f"Failed to generate a valid plan: {last_error}")


def _strip_fences(raw: str) -> str:
    """Strip a leading/trailing markdown code fence if the model added one."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    return raw
