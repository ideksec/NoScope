# Release checklist

Steps to cut a NoScope release.

## Pre-flight

- [ ] `uv run pytest tests/ -v` — all green
- [ ] `uv run ruff check noscope/ tests/` and `uv run ruff format --check noscope/ tests/`
- [ ] `uv run mypy noscope/` — clean
- [ ] `uv run noscope doctor` — environment checks pass
- [ ] Version bumped in `pyproject.toml` **and** `noscope/__init__.py` (they must match)
- [ ] `CHANGELOG.md` has a dated entry for this version
- [ ] Live smoke test with a real key (below)

## Live smoke test

Not covered by CI (no API key in CI). Run once before tagging:

```bash
export NOSCOPE_ANTHROPIC_API_KEY=sk-ant-...
uv run noscope run --spec examples/cli-calculator.md --time 5m --dir /tmp/noscope-smoke --yes
```

Confirm: it completes within the timebox, HARDEN checks pass (the calculator
spec asserts `... ==> 5`), VERIFY reports an independently-confirmed verdict,
and the final summary shows a cost (and cache savings on Anthropic).

## Tag and publish

- [ ] Commit the version bump + changelog
- [ ] `git tag v<version>` and push the tag

## Known limitations to weigh before a "1.0" claim

These are tracked in `PLAN.md` and are acceptable for a 0.2.x preview, not for a
stability guarantee:

- No conversation-context management yet — very long runs can hit context limits.
- `--sandbox` (Docker) uses a Python-only image; git tools still act on the host
  tree during sandbox runs, and the exec timeout is not deadline-aware.
- `MAX_WORKERS` is fixed at 2.
- Live end-to-end behavior is validated by hand, not in CI.
