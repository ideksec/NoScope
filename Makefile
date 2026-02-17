.PHONY: dev lint fmt test typecheck run clean

dev:
	uv sync --all-extras

lint:
	uv run ruff check noscope/ tests/

fmt:
	uv run ruff format noscope/ tests/
	uv run ruff check --fix noscope/ tests/

test:
	uv run pytest tests/ -v

typecheck:
	uv run mypy noscope/

run:
	uv run noscope run --spec examples/hello-flask.md --time 5m --dir /tmp/noscope-test

clean:
	rm -rf .noscope/runs/ /tmp/noscope-test* dist/ *.egg-info .mypy_cache .pytest_cache .ruff_cache
