# NoScope
<img width="1536" height="1024" alt="NoScope Github Image" src="https://github.com/user-attachments/assets/af16f300-10f0-4bf7-ad1d-abb9c2973a29" />

**Time-boxed autonomous agent orchestration.** Give it a spec and a deadline — it builds a runnable MVP.

NoScope takes a written specification, a fixed time limit, and explicit capability grants, then autonomously plans, builds, and validates a working software prototype. At deadline, it always produces a runnable artifact and a handoff report.

## Why NoScope?

40% of agentic AI projects get cancelled due to cost overruns. NoScope's timebox is a spend-cap guarantee — when time's up, you get a result, not a bill.

- **One-shot execution** — no interactive prompting after launch
- **Hard deadline** — guaranteed to stop and produce output
- **Capability-gated** — explicit permission model, nothing runs without your approval
- **Observable** — full JSONL event log of every action taken
- **Provider-agnostic** — works with Anthropic Claude and OpenAI GPT

## Quick Start

```bash
# Install
pip install noscope
# or
uv sync

# Set your API key
export NOSCOPE_ANTHROPIC_API_KEY="your-key-here"
# or
export NOSCOPE_OPENAI_API_KEY="your-key-here"

# Check your environment
noscope doctor

# Run with an example spec
noscope run --spec examples/hello-flask.md --time 5m --dir /tmp/my-app
```

## How It Works

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  PLAN   │───▶│ REQUEST │───▶│  BUILD  │───▶│ HARDEN  │───▶│ HANDOFF │
│  (10%)  │    │  (user) │    │  (60%)  │    │  (25%)  │    │  (5%)   │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
 Generate       Approve        Execute        Run tests      Generate
 task plan      capabilities   the plan       & validate     report
```

1. **PLAN** — Parses your spec, generates a structured build plan
2. **REQUEST** — Shows required capabilities (file access, shell, git) for your approval
3. **BUILD** — Autonomously implements the plan using LLM-driven tool use
4. **HARDEN** — Runs acceptance checks and validation
5. **HANDOFF** — Generates a report with what was built, how to run it, and what's left

## Spec Format

Write your spec as Markdown with YAML frontmatter:

```yaml
---
name: "My Web App"
timebox: "10m"
constraints:
  - "Use Flask"
  - "SQLite for storage"
acceptance:
  - "cmd: pip install -r requirements.txt"
  - "cmd: python -c 'import app'"
  - "Has a working /api/health endpoint"
---

# My Web App

Build a REST API with user registration and login...
```

## CLI Reference

```
noscope run --spec <path> --time <duration> --dir <output>
    [--provider anthropic|openai] [--model <model>]
    [--sandbox] [--danger] [--yes] [--tui]

noscope doctor          # Check environment
noscope init            # Create a spec template
noscope replay          # Replay a run (coming in v0.2)
```

## Run Outputs

Every run produces:

```
.noscope/runs/<run_id>/
  events.jsonl              # Full event log
  plan.json                 # Generated build plan
  contract.json             # Immutable scope contract
  capabilities_grant.json   # What was approved
  handoff.md                # Final report (always generated)
```

## Development

```bash
git clone https://github.com/ideksec/NoScope.git
cd noscope
make dev        # Install with all extras
make test       # Run tests
make lint       # Lint check
make typecheck  # Type check
```

## License

MIT
