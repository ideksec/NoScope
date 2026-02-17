# CLAUDE.md

## Project Overview

NoScope is a time-boxed autonomous agent orchestration tool that builds runnable software MVPs from written specs. It uses LLM providers (Anthropic/OpenAI) to plan and execute build tasks within a strict timebox.

## Quick Reference

```bash
# Install
uv sync --all-extras

# Run tests
uv run pytest tests/ -v

# Lint
uv run ruff check noscope/ tests/

# Format
uv run ruff format noscope/ tests/

# Type check
uv run mypy noscope/

# Run the CLI
uv run noscope doctor
uv run noscope run --spec examples/hello-flask.md --time 5m --dir /tmp/out --yes
```

## Architecture

- **Flat package layout**: `noscope/` (not `src/noscope/`)
- **Async throughout**: all I/O uses `asyncio`
- **Python 3.12+** required

### Key modules

| Module | Purpose |
|---|---|
| `noscope/cli.py` | Typer CLI entry point |
| `noscope/orchestrator.py` | Main run lifecycle — wires all phases |
| `noscope/phases.py` | Phase implementations (Plan/Request/Build/Harden/Handoff) |
| `noscope/deadline.py` | Timebox engine with phase budgets and panic mode |
| `noscope/capabilities.py` | Capability model — gates all agent actions |
| `noscope/tools/` | Agent tools (filesystem, shell, git, docker) |
| `noscope/llm/` | LLM provider abstraction (Anthropic + OpenAI) |
| `noscope/spec/` | Spec parsing (Markdown + YAML frontmatter) and contracts |
| `noscope/logging/events.py` | JSONL event log |
| `noscope/config/settings.py` | Pydantic BaseSettings from env vars / .env |
| `noscope/ui/` | Rich console output and Textual TUI |

### Execution flow

```
parse_spec → Deadline → PLAN (LLM) → REQUEST (user approval) → BUILD (agent loop) → HARDEN (acceptance checks) → HANDOFF (report)
```

The HANDOFF phase always runs, even on error.

## Conventions

- Tests live in `tests/` mirroring the package structure
- Settings accept both `NOSCOPE_*` and standard env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
- Tool calls are capability-gated — every tool declares a required capability
- All tool executions are logged to `events.jsonl`
- Secrets are redacted from all logged output
- Safety filters block dangerous shell commands unless `--danger` is passed

## Config

API keys via environment or `.env` file:
```
NOSCOPE_ANTHROPIC_API_KEY=sk-ant-...
NOSCOPE_OPENAI_API_KEY=sk-...
```

Also accepts standard names as fallback: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
