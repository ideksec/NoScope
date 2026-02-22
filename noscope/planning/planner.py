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

IMPORTANT: Multiple agents will execute this plan IN PARALLEL. Task t1 (setup) runs first alone, then remaining tasks run concurrently across workers. Design tasks to be independent where possible.

Given a project specification, produce a structured JSON plan. Your output must be valid JSON matching this schema:

{
  "requested_capabilities": [
    {"cap": "workspace_rw|shell_exec|net_http|git|docker|secrets:<NAME>", "why": "justification", "risk": "low|medium|high"}
  ],
  "tasks": [
    {"id": "t1", "title": "Task name", "kind": "edit|shell|test", "priority": "mvp|stretch", "description": "What to do", "depends_on": []}
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
- Task t1 MUST be "Set up project structure and install dependencies"
- Task t1 runs ALONE before all other tasks — it creates the foundation
- All other tasks should specify depends_on: ["t1"] unless they depend on another task
- Design tasks so parallel agents can work on them WITHOUT file conflicts
- Each task should own specific files/components — describe which files in the description
- Acceptance checks must use paths that match where files are actually created
- Do NOT spend tasks on mock data files or placeholder content — inline minimal data in code

STACK SELECTION — match complexity to timebox:
- ≤5m: 2-3 MVP tasks. Use the SIMPLEST stack: vanilla HTML/CSS/JS, single Python file with Flask, or Node.js with Express. NO TypeScript, NO React, NO build tools, NO Tailwind.
- 5-10m: 3-5 MVP tasks. Lightweight frameworks OK (Flask, Express). Avoid TypeScript and complex build chains.
- 10-20m: 5-7 MVP tasks. Frameworks OK, TypeScript OK if the spec requires it.
- 20m+: Full stack OK, up to 8+ MVP tasks + stretch tasks.

NEVER USE INTERACTIVE SCAFFOLDING TOOLS:
- NEVER plan tasks that use create-react-app, npm create, npx create-*, yarn create, or similar
- These commands HANG and waste the entire timebox
- Instead: write package.json manually, then npm install
- For Python: write requirements.txt, then pip install -r requirements.txt

Mark stretch tasks for features to add if time permits.

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
