"""Tests for CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from noscope.cli import app

runner = CliRunner()


class TestDoctorCommand:
    def test_doctor_runs(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "NoScope Doctor" in result.output
        assert "Python" in result.output

    def test_doctor_checks_python_version(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert "3.1" in result.output  # Should show Python version


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
