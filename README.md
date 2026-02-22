# NoScope

[![CI](https://github.com/ideksec/NoScope/actions/workflows/ci.yml/badge.svg)](https://github.com/ideksec/NoScope/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/noscope.svg)](https://pypi.org/project/noscope/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](https://mypy-lang.org/)

<img width="1536" height="1024" alt="NoScope Github Image" src="https://github.com/user-attachments/assets/af16f300-10f0-4bf7-ad1d-abb9c2973a29" />

**Time-boxed autonomous agent orchestration.** Give it a spec and a deadline — it builds a runnable MVP.

NoScope takes a written specification, a fixed time limit, and explicit capability grants, then autonomously plans, builds, and validates a working software prototype. When the timer runs out, you always get a runnable artifact and a handoff report — never a hanging process or a surprise bill.

---

## Why NoScope?

40% of agentic AI projects get cancelled due to cost overruns. NoScope's timebox is a spend-cap guarantee — when time's up, you get a result, not a bill.

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

## Quick Start

### Installation

```bash
pip install noscope
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install noscope
```

### Set your API key

```bash
export NOSCOPE_ANTHROPIC_API_KEY="your-key-here"
# or
export NOSCOPE_OPENAI_API_KEY="your-key-here"
```

### Verify your setup

```bash
noscope doctor
```

### Run your first build

```bash
noscope run --spec examples/hello-flask.md --time 5m --dir /tmp/my-app
```

Or create a project interactively:

```bash
noscope new
```

---

## How It Works

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  PLAN   │───▶│ REQUEST │───▶│  BUILD  │───▶│ HARDEN  │───▶│ VERIFY  │───▶│ HANDOFF │
│  (10%)  │    │  (user) │    │  (50%)  │    │  (25%)  │    │  (10%)  │    │  (5%)   │
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
  - "cmd: pip install -r requirements.txt"
  - "cmd: python -c 'import app'"
  - "API supports CRUD operations for todos"
---

# Todo API

Build a REST API for managing todo items with CRUD endpoints,
SQLite persistence, and JSON request/response format.
```

See the [`examples/`](examples/) directory for more spec templates.

---

## CLI Reference

```
noscope run --spec <path> --time <duration> --dir <output>
    [--provider anthropic|openai] [--model <model>]
    [--sandbox] [--danger] [--yes] [--tui]

noscope new             # Create and run a project interactively
noscope doctor          # Check environment and API keys
noscope init            # Create a spec file template
```

| Flag | Description |
|------|-------------|
| `--spec`, `-s` | Path to the spec file |
| `--time`, `-t` | Timebox duration (e.g., `5m`, `1h`, `30m`) |
| `--dir`, `-d` | Output directory for the built project |
| `--provider`, `-p` | LLM provider: `anthropic` or `openai` |
| `--model`, `-m` | Model override (e.g., `claude-sonnet-4-20250514`, `gpt-4o`) |
| `--sandbox` | Run agent commands inside a Docker container |
| `--danger` | Bypass safety filters (use only with trusted specs) |
| `--yes`, `-y` | Auto-approve all capability requests |

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
