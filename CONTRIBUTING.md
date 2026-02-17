# Contributing to NoScope

Thanks for your interest in contributing to NoScope!

## Development Setup

```bash
# Clone the repo
git clone https://github.com/yourusername/noscope.git
cd noscope

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install in development mode with all extras
make dev

# Verify your setup
uv run noscope doctor
```

## Development Workflow

```bash
# Run tests
make test

# Lint
make lint

# Format code
make fmt

# Type check
make typecheck
```

## Code Style

- Python 3.12+ features are encouraged (type unions with `|`, etc.)
- We use Ruff for linting and formatting
- Type annotations throughout — run `make typecheck` before submitting
- Async-first: use `async/await` for I/O operations

## Pull Request Process

1. Fork the repo and create a feature branch
2. Write tests for new functionality
3. Ensure `make lint && make typecheck && make test` all pass
4. Submit a PR with a clear description of changes

## Reporting Issues

- Use the GitHub issue templates for bugs and feature requests
- Include reproduction steps for bugs
- Example specs that don't work well are especially valuable

## Architecture Overview

- `noscope/cli.py` — Typer CLI entry point
- `noscope/orchestrator.py` — Main execution loop
- `noscope/phases.py` — Phase implementations (PLAN/BUILD/HARDEN/HANDOFF)
- `noscope/deadline.py` — Timebox engine
- `noscope/tools/` — Agent tools (filesystem, shell, git)
- `noscope/llm/` — LLM provider abstraction
- `noscope/spec/` — Spec parsing and contract generation
