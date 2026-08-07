"""Tests for CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from noscope.cli import app

runner = CliRunner()


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient keys and no .env pickup, so doctor's verdict is the test's."""
    for var in (
        "NOSCOPE_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "NOSCOPE_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


class TestDoctorCommand:
    def test_doctor_reports_environment(self, clean_env: None) -> None:
        result = runner.invoke(app, ["doctor"])
        assert "NoScope Doctor" in result.output
        assert "Python" in result.output
        assert "3.1" in result.output

    def test_doctor_fails_without_a_key(self, clean_env: None) -> None:
        # doctor is meant to be usable as a gate in scripts and CI, so a missing
        # requirement has to show up in the exit code, not just the text.
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "At least one API key" in result.output

    def test_doctor_passes_with_one_key(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Either provider alone is enough — the unset one must not fail the run.
        monkeypatch.setenv("NOSCOPE_ANTHROPIC_API_KEY", "test-key")
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output


class TestInitCommand:
    def test_init_creates_spec(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Created" in result.output
        # Check that a spec file was created
        spec_files = list(tmp_path.glob("spec*.md"))
        assert len(spec_files) == 1
        content = spec_files[0].read_text()
        assert "name:" in content
        assert "timebox:" in content

    def test_init_increments_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "spec.md").write_text("existing")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / "spec-1.md").exists()


class TestRunCommand:
    def test_run_missing_spec(self) -> None:
        result = runner.invoke(app, ["run", "--spec", "/nonexistent/spec.md", "--time", "5m"])
        # Should fail because spec file doesn't exist
        assert result.exit_code != 0
