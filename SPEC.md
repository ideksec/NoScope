# No Scope — SPEC.md

## 1. Overview

No Scope is an open-source, time-boxed agent orchestration tool that autonomously builds a runnable software MVP from a written specification.

Given:
- a spec
- a fixed time limit
- explicit capability grants

No Scope plans, requests access once, then builds autonomously until the timer expires.  
At deadline, it always produces a runnable artifact and a handoff report.

This is intentionally a “one-shot” system. No interactive prompting after execution begins.

---

## 2. Goals and Non-Goals

### Goals
- Produce a runnable MVP within a strict timebox
- Be fully autonomous after a single permission step
- Make agent behaviour observable, replayable, and auditable
- Be impressive enough to demo publicly while remaining practically useful

### Non-Goals
- Long-running conversational agents
- Production-grade deployment tooling
- Continuous iteration or human-in-the-loop refinement
- Perfect sandboxing guarantees

---

## 3. Target Users

Primary:
- Software engineers prototyping fast MVPs
- Engineering and security leaders demoing agent capability and risk

Secondary:
- Content creators and educators demonstrating LLM autonomy

---

## 4. Execution Model

### 4.1 Phases

1. **PLAN**
   - Parse spec
   - Generate task plan
   - Define MVP boundary
   - Request required capabilities
   - No irreversible side effects by default

2. **REQUEST**
   - Single user interaction
   - Approve or deny requested capabilities and secrets
   - No further prompts allowed after this phase

3. **BUILD**
   - Implement MVP features
   - Create runnable code
   - Prioritise “works over complete”

4. **HARDEN**
   - Run tests
   - Lint / basic validation
   - Acceptance checks

5. **HANDOFF**
   - Produce final report
   - Ensure run instructions are correct
   - Always executed, even on failure

---

## 5. Timeboxing Rules

- Timebox is absolute
- Hard stop at T=0
- No new actions after deadline
- A small grace window (default 15s) may capture in-flight command output

### Default Allocation
- PLAN: ≤10% (max 3 minutes)
- BUILD: ~60%
- HARDEN: ~25%
- HANDOFF: ≥5% (never skipped)

### Panic Mode
Triggered when remaining time < max(60s, 10% of total):
- Stop adding features
- Focus only on runnable demo path
- Skip refactors and optimisations

---

## 6. Capability Model

All actions are gated by explicit capabilities.

### 6.1 Capability Types (MVP)

- `workspace_rw` — read/write project directory (required)
- `shell_exec` — execute commands inside workspace
- `net_http` — outbound HTTP(S)
- `git` — git status, diff, commit
- `docker` — build/run containers
- `secrets:<NAME>` — named secrets only

### 6.2 Capability Request

- Requested once after planning
- Includes:
  - capability name
  - justification
  - risk level
- User may approve or deny each individually
- No re-prompting permitted

### 6.3 Enforcement

All tools must:
- Verify capability grants
- Enforce workspace boundaries
- Log all actions
- Redact secrets from logs

---

## 7. Input Spec Format

Markdown with YAML frontmatter.

### Required Fields
```yaml
name: "Example Project"
timebox: "30m"
constraints:
  - "Use sqlite"
  - "No cloud deployment"
acceptance:
  - "cmd: pytest -q"
  - "Server starts on port 8000"
Optional Fields
stack_prefs
repo_mode: new | existing
risk_policy: strict | default | permissive
Parsing Rules
acceptance entries beginning with cmd: are executed
Others are documented as manual checks
8. Scope Contract
Before execution begins, No Scope writes an immutable contract:
NOSCOPE_CONTRACT.json

Includes:

Normalised requirements
MVP definition
Explicit exclusions
Acceptance checks
Capability grants
This contract defines success for the run.
9. Architecture
9.1 Language and Tooling
Python 3.12+
CLI: typer
Logging/UI: rich
TUI (optional): textual
Config: YAML + Pydantic
Distribution: uv or pipx
10. Repository Structure
noscope/
  pyproject.toml
  src/noscope/
    cli.py
    orchestrator.py
    phases.py
    deadline.py

    spec/
      parser.py
      models.py
      contract.py

    planning/
      planner.py
      models.py

    llm/
      base.py
      providers/

    tools/
      dispatcher.py
      filesystem.py
      shell.py
      http.py
      git.py
      docker.py
      safety.py
      redaction.py

    logging/
      events.py
      replay.py

    ui/
      console.py
      tui.py

    config/
      settings.py
11. Event Logging
11.1 Run Directory
.noscope/runs/<run_id>/
  events.jsonl
  plan.json
  contract.json
  capabilities_request.json
  capabilities_grant.json
  handoff.md
  artifacts/
11.2 Event Schema (JSONL)
{
  "ts": "2026-02-17T09:12:33.123Z",
  "run_id": "20260217T0912Z_ab12cd34",
  "phase": "BUILD",
  "seq": 42,
  "type": "tool.exec_cmd",
  "summary": "pip install -r requirements.txt",
  "data": {
    "tool": "shell_exec",
    "cmd": "pip install -r requirements.txt",
    "cwd": "./out"
  },
  "result": {
    "status": "ok",
    "exit_code": 0
  }
}
Event log is append-only and authoritative.
12. Planner Output Contract
Planner must return structured JSON:
{
  "requested_capabilities": [
    {
      "cap": "shell_exec",
      "why": "Install dependencies and run tests",
      "risk": "medium"
    }
  ],
  "tasks": [
    {
      "id": "t1",
      "title": "Scaffold project",
      "kind": "edit",
      "priority": "mvp"
    }
  ],
  "mvp_definition": [
    "Project runs locally",
    "Acceptance tests pass"
  ],
  "exclusions": [
    "No cloud deployment"
  ],
  "acceptance_plan": [
    {
      "name": "tests",
      "cmd": "pytest -q",
      "must_pass": true
    }
  ]
}
13. Tooling Rules
13.1 Workspace Boundary
All file operations must remain within workspace root
Shell commands execute with cwd pinned to workspace
Path traversal is blocked
13.2 Command Safety
Default deny patterns:
destructive filesystem commands
privilege escalation
crypto mining
Override requires explicit danger flag.
14. LLM Provider Abstraction
LLM interface must support:
structured JSON output
streaming tokens
provider-agnostic adapters
Minimum providers:
OpenAI-compatible HTTP
Anthropic-compatible HTTP
No hardcoded vendor logic.
15. Handoff Report
handoff.md must include:
Contract summary
What was built
How to run it (exact commands)
Acceptance results
Known gaps and risks
Next recommended steps
Generated even if build fails.
16. CLI Interface
Commands
noscope run
noscope replay
noscope init
noscope doctor
Examples
noscope run --spec spec.md --time 30m --dir ./out
noscope run --repo . --spec patch.md --time 20m
noscope replay .noscope/runs/20260217T0912Z_ab12cd34
17. Definition of Done (MVP)
Produces runnable code within timebox
Single permission step only
Full event log and replayable run
Deterministic scope contract
Always produces a handoff report
18. Security Posture
No Scope executes code.
Treat it as untrusted automation unless sandboxed.

Recommended execution:

Docker container with workspace mount
“Danger mode” must be explicit and visually obvious.
19. Success Criteria
MVP is runnable in >70% of reasonable specs
Handoff report generated in 100% of runs
Behaviour is observable and replayable
Timebox and scope boundaries are respected