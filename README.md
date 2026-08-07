# NoScope

[![CI](https://github.com/ideksec/NoScope/actions/workflows/ci.yml/badge.svg)](https://github.com/ideksec/NoScope/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](https://mypy-lang.org/)

<img width="1536" height="1024" alt="NoScope Github Image" src="https://github.com/user-attachments/assets/af16f300-10f0-4bf7-ad1d-abb9c2973a29" />

**Time-boxed autonomous agent orchestration.** Give it a spec and a deadline — it builds a runnable MVP.

NoScope takes a written specification, a fixed time limit, and explicit capability grants, then autonomously plans, builds, and validates a working software prototype. When the timer runs out, you always get a runnable artifact and a handoff report — never a hanging process or a surprise bill.

> **Deep dives:** [`ARCHITECTURE.md`](ARCHITECTURE.md) — how it works and why (with diagrams) · [`PLAN.md`](PLAN.md) — the design audit and the build-our-own-harness-vs-wrap-the-SDK decision · [`CHANGELOG.md`](CHANGELOG.md)

---

## Why NoScope?

Gartner projects that [over 40% of agentic AI projects will be scrapped by 2027](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027), citing cost and unclear value among the causes. NoScope's timebox is a spend-cap guarantee — when time's up, you get a result, not a bill.

| | Traditional Agent | NoScope |
|---|---|---|
| **Cost control** | Open-ended, unpredictable | Hard deadline, guaranteed stop |
| **Interaction** | Continuous prompting | One-shot: spec in, MVP out |
| **Permissions** | Implicit, broad access | Capability-gated, explicit approval |
| **Observability** | Opaque | Full JSONL event log of every action |
| **Output guarantee** | May fail silently | Always produces artifact + handoff report |

---

## Features

- **One-shot execution** — no interactive prompting after launch
- **Hard deadline** — guaranteed to stop and produce output within your timebox
- **Capability-gated** — explicit permission model; nothing runs without your approval
- **Observable** — full JSONL event log of every action taken
- **Provider-agnostic** — works with Anthropic Claude and OpenAI GPT
- **Docker sandbox** — optional container isolation for untrusted specs
- **Acceptance checks** — automated validation that the built software actually works

---

## When to use NoScope

NoScope is a **time-boxed proof-of-concept builder** — it turns a written spec
into a runnable MVP inside a fixed budget, then hands you the artifact, a
verification verdict, and a full event log. It's built to demonstrate what
autonomous agents can do and to sculpt quick prototypes.

**Reach for NoScope when** you want a runnable POC from a spec with a hard
spend cap, an auditable record of every action, and an explicit permission
gate — a self-contained, one-shot "spec in, MVP out" run.

**Reach for something else when** you want to pair interactively on a codebase
(use [Claude Code](https://claude.com/claude-code) or Cursor), or you want an
open-ended cloud agent to own a ticket end-to-end (use a Devin-style agent).
NoScope is deliberately one-shot and time-boxed; it is not an interactive
editor or a long-running autonomous engineer.

---

## Quick Start

### Installation

```bash
git clone https://github.com/ideksec/NoScope.git
cd NoScope
uv sync --all-extras
```

> **Note:** NoScope requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

### Set your API key

Create a `.env` file in the project root, or export directly:

```bash
export NOSCOPE_ANTHROPIC_API_KEY="your-key-here"
# or
export NOSCOPE_OPENAI_API_KEY="your-key-here"
```

### Verify your setup

```bash
uv run noscope doctor
```

### Try it with no API calls first

```bash
uv run noscope run --spec examples/static-portfolio.md --time 3m --dir /tmp/demo --yes --dry-run
```

`--dry-run` exercises the entire pipeline — workspace setup, capability
gating, tool execution, acceptance checks, verification, and the handoff
report — without contacting any provider or spending a token. If a dry run
completes, the harness works; anything that fails on a live run is about the
model or the API, not the plumbing.

### Confirm your key and model work

```bash
uv run noscope doctor --live
```

`--live` makes one tiny API call, so a bad key or a wrong `--model` fails in
seconds instead of part-way through a real build.

### Run your first build

```bash
uv run noscope run --spec examples/hello-flask.md --time 5m --dir /tmp/my-app
```

Or create a project interactively:

```bash
uv run noscope new
```

---

## How It Works

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  PLAN   │───▶│ REQUEST │───▶│  BUILD  │───▶│ HARDEN  │───▶│ VERIFY  │───▶│ HANDOFF │
│  (5%)   │    │  (user) │    │  (65%)  │    │  (10%)  │    │  (15%)  │    │  (5%)   │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
 Generate       Approve        Execute        Run tests      Confirm        Generate
 task plan      capabilities   the plan       & validate     MVP runs       report
```

| Phase | What happens |
|-------|-------------|
| **PLAN** | Parses your spec, generates a structured build plan with tasks |
| **REQUEST** | Shows required capabilities (file access, shell, git) for your approval |
| **BUILD** | Autonomously implements the plan using LLM-driven tool use |
| **HARDEN** | Runs acceptance checks and validation against your criteria |
| **VERIFY** | Confirms the MVP actually runs; aggressively fixes issues if needed |
| **HANDOFF** | Generates a report with what was built, how to run it, and what's left |

The **HANDOFF** phase always runs, even if the build fails — you always get a report.

---

## Design highlights

The interesting engineering is in how the "always terminate, always produce
output" guarantee is enforced. A few decisions worth a closer look
([`ARCHITECTURE.md`](ARCHITECTURE.md) has the full picture):

- **Verification executes, it doesn't trust.** The failure mode to avoid is a
  confident model declaring success on a broken build. HARDEN passes a check
  only if the command *actually* exits 0 and its output matches; VERIFY makes
  the agent propose a proof command (`curl -sf localhost:5000`) and the harness
  *runs it* — the verdict is grounded in a real command every time. A failing
  check triggers a bounded auto-repair, then re-runs.

- **Two spend caps: time and tokens.** A cooperative wall-clock `Deadline`
  gives every agent loop a cheap per-iteration check so it winds down and the
  pipeline still reaches HANDOFF; an optional `--token-budget` caps spend in
  tokens the same way. The product's promise is a spend guarantee — enforced in
  both dimensions.

- **Parallel build with dependency-aware partitioning.** A supervisor splits
  setup into concurrent structure + deps agents, then partitions the remaining
  tasks with union-find over the dependency graph (+ topological sort) so
  transitively-dependent work lands on one worker and cycles can't hang. A
  concurrent audit agent feeds corrections back into workers mid-build.

- **Capability-gated, typed tools over raw bash.** Every action is a typed tool
  that can be permission-gated, path-checked against the workspace, logged, and
  secret-redacted — including an `edit_file` whose uniqueness guard *fails* on
  an ambiguous match instead of editing the wrong region.

- **A thin, modern LLM layer.** Structured outputs, prompt caching (with
  cache-savings reporting), adaptive thinking/effort with graceful degradation,
  and SDK-native retries — kept small on purpose so a model/API change is a
  one-line default, not a refactor.

---

## Spec Format

Write your spec as Markdown with YAML frontmatter:

```yaml
---
name: "Todo API"
timebox: "10m"
constraints:
  - "Use Flask or FastAPI"
  - "Use SQLite for storage"
acceptance:
  - "cmd: python3 -m pip install -r requirements.txt"
  - "cmd: python3 -c 'import app'"
  - "API supports CRUD operations for todos"
---

# Todo API

Build a REST API for managing todo items with CRUD endpoints,
SQLite persistence, and JSON request/response format.
```

### Acceptance checks

Each `acceptance` entry is either a `cmd:` check (run in the HARDEN phase) or a
plain-language note (recorded in the handoff, not executed).

- `cmd: <command>` — passes when the command exits `0`.
- `cmd: <command> ==> <text>` — passes only when the command exits `0` **and**
  its output contains `<text>`, so the check asserts real behavior rather than
  just "the process started". For example:

  ```yaml
  acceptance:
    - "cmd: python3 calc.py add 2 3 ==> 5"
  ```

If a `cmd:` check fails, HARDEN makes a bounded attempt to fix the cause and
re-runs it before recording the result.

See the [`examples/`](examples/) directory for more spec templates.

---

## CLI Reference

```
uv run noscope run --spec <path> --time <duration> --dir <output>
    [--provider anthropic|openai] [--model <model>]
    [--sandbox] [--danger] [--yes] [--serve]
    [--token-budget <n>] [--workers <n>]

uv run noscope new             # Create and run a project interactively
uv run noscope doctor          # Check environment and API keys
uv run noscope doctor --live   # ...and prove the key/model work with one API call
uv run noscope init            # Create a spec file template
```

| Flag | Description |
|------|-------------|
| `--spec`, `-s` | Path to the spec file |
| `--time`, `-t` | Timebox duration (e.g., `5m`, `1h`, `30m`) |
| `--dir`, `-d` | Output directory for the built project |
| `--provider`, `-p` | LLM provider: `anthropic` or `openai` |
| `--model`, `-m` | Model override (e.g., `claude-sonnet-5`, `gpt-5.6-terra`) |
| `--sandbox` | Run agent commands inside a Docker container |
| `--danger` | Bypass safety filters (use only with trusted specs) |
| `--yes`, `-y` | Auto-approve all capability requests |
| `--serve` | After a verified build, launch the app and stream output (blocks until Ctrl+C) |
| `--token-budget` | Stop the build once this many total tokens are used (a spend cap alongside the timebox) |
| `--workers` | Parallel build workers (default 2) |
| `--dry-run` | Run the full pipeline with no API calls and no tokens spent |

---

## Run Outputs

Every run produces a structured output in `.noscope/runs/<run_id>/`:

```
.noscope/runs/<run_id>/
  events.jsonl              # Full event log — every tool call and result
  plan.json                 # Generated build plan with task breakdown
  contract.json             # Immutable scope contract (what was agreed)
  capabilities_grant.json   # What capabilities were approved
  handoff.md                # Final report (always generated, even on failure)
```

The **event log** is append-only JSONL — every action is reconstructable after
the fact (secrets redacted, bulky fields trimmed):

```jsonl
{"ts":"2026-08-06T18:22:01Z","phase":"BUILD","seq":42,"type":"tool.write_file","summary":"Calling write_file","data":{"tool":"write_file","args":{"path":"app.py"}}}
{"ts":"2026-08-06T18:22:07Z","phase":"BUILD","seq":58,"type":"task.complete","summary":"[worker-0] Task t2: CRUD endpoints","data":{"task_id":"t2","agent_id":"worker-0"}}
{"ts":"2026-08-06T18:23:14Z","phase":"HARDEN","seq":71,"type":"acceptance.check","summary":"✓ cmd: python3 -c 'import app'","result":{"passed":true,"reason":""}}
{"ts":"2026-08-06T18:23:40Z","phase":"VERIFY","seq":80,"type":"verify.pass","summary":"MVP verified (independently confirmed): responds on / with the todo list"}
```

The **handoff report** (`handoff.md`) is written even if the build fails — it
records the contract, what was built, exact run commands, the pass/fail table,
and known gaps. See the [Run Outputs section of `ARCHITECTURE.md`](ARCHITECTURE.md#observability)
for the full model.

---

## Security

NoScope executes LLM-generated code on your machine. It ships with multiple layers of protection:

- **Capability gating** — every action requires explicit permission
- **Command safety filters** — deny-list blocks dangerous shell patterns (sudo, rm -rf, crypto mining, reverse shells)
- **Path traversal protection** — agents cannot write outside the workspace
- **Docker sandbox** — optional container isolation with resource limits
- **Secret redaction** — API keys are scrubbed from event logs

See [SECURITY.md](.github/SECURITY.md) for the full security model, known limitations, and how to report vulnerabilities.

---

## Development

```bash
git clone https://github.com/ideksec/NoScope.git
cd NoScope
make dev        # Install with all extras
make test       # Run tests
make lint       # Lint check
make fmt        # Auto-format
make typecheck  # Type check with mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on submitting pull requests.

---

## Project Structure

```
noscope/
  cli.py              # Typer CLI entry point
  orchestrator.py      # Main run lifecycle
  phases.py            # Phase implementations (Plan/Build/Harden/Verify/Handoff)
  deadline.py          # Timebox engine with phase budgets
  capabilities.py      # Capability model — gates all agent actions
  tools/               # Agent tools (filesystem, shell, git, docker)
  llm/                 # LLM provider abstraction (Anthropic + OpenAI)
  spec/                # Spec parsing and contract generation
  config/              # Settings from env vars / .env
  logging/             # JSONL event log
  ui/                  # Rich console output
```

---

## License

MIT -- see [LICENSE](LICENSE) for details.
