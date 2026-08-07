"""Tests for spec parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from noscope.spec.models import AcceptanceCheck, _parse_duration
from noscope.spec.parser import parse_spec


class TestParseDuration:
    def test_minutes(self) -> None:
        assert _parse_duration("30m") == 1800

    def test_hours(self) -> None:
        assert _parse_duration("1h") == 3600

    def test_hours_and_minutes(self) -> None:
        assert _parse_duration("1h30m") == 5400

    def test_seconds(self) -> None:
        assert _parse_duration("90s") == 90

    def test_bare_number_is_minutes(self) -> None:
        assert _parse_duration("5") == 300

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_duration("abc")

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_duration("0m")


class TestAcceptanceCheck:
    def test_cmd_prefix(self) -> None:
        ac = AcceptanceCheck.from_string("cmd: pytest -q")
        assert ac.is_cmd is True
        assert ac.command == "pytest -q"

    def test_plain_string(self) -> None:
        ac = AcceptanceCheck.from_string("Server starts on port 8000")
        assert ac.is_cmd is False
        assert ac.command is None

    def test_cmd_case_insensitive(self) -> None:
        ac = AcceptanceCheck.from_string("CMD: echo hello")
        assert ac.is_cmd is True
        assert ac.command == "echo hello"

    def test_expected_output(self) -> None:
        ac = AcceptanceCheck.from_string("cmd: curl -s localhost:5000 ==> Hello")
        assert ac.is_cmd is True
        assert ac.command == "curl -s localhost:5000"
        assert ac.expect == "Hello"

    def test_no_expected_output(self) -> None:
        ac = AcceptanceCheck.from_string("cmd: pytest -q")
        assert ac.expect is None


class TestParseSpec:
    def test_valid_spec(self, sample_spec_path: Path) -> None:
        spec = parse_spec(sample_spec_path)
        assert spec.name == "Test Project"
        assert spec.timebox == "5m"
        assert spec.timebox_seconds == 300
        assert len(spec.constraints) == 1
        assert len(spec.acceptance) == 2
        assert spec.acceptance[0].is_cmd is True
        assert spec.acceptance[1].is_cmd is False

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_spec(tmp_path / "nonexistent.md")

    def test_missing_name(self, tmp_path: Path) -> None:
        spec = tmp_path / "bad.md"
        spec.write_text("---\ntimebox: '5m'\n---\nHello\n")
        with pytest.raises(ValueError, match="name"):
            parse_spec(spec)

    def test_missing_timebox(self, tmp_path: Path) -> None:
        spec = tmp_path / "bad.md"
        spec.write_text("---\nname: 'Test'\n---\nHello\n")
        with pytest.raises(ValueError, match="timebox"):
            parse_spec(spec)

    def test_optional_fields(self, tmp_path: Path) -> None:
        spec = tmp_path / "full.md"
        spec.write_text(
            """---
name: "Full"
timebox: "10m"
constraints: []
acceptance: []
stack_prefs:
  - "FastAPI"
repo_mode: existing
risk_policy: strict
---
Body here.
""",
            encoding="utf-8",
        )
        result = parse_spec(spec)
        assert result.stack_prefs == ["FastAPI"]
        assert result.repo_mode == "existing"
        assert result.risk_policy == "strict"
        assert result.body.strip() == "Body here."


class TestSpecFileGeneration:
    def test_round_trips_through_the_parser(self, tmp_path: Path) -> None:
        from noscope.spec.parser import build_spec_file

        content = build_spec_file(
            name="My Project",
            timebox="10m",
            constraints=["Use Python"],
            acceptance=["cmd: pytest -q"],
            body="# My Project\n\nDo the thing.",
        )
        path = tmp_path / "s.md"
        path.write_text(content, encoding="utf-8")
        spec = parse_spec(path)
        assert spec.name == "My Project"
        assert spec.timebox == "10m"
        assert spec.constraints == ["Use Python"]
        assert spec.acceptance[0].command == "pytest -q"

    def test_quotes_and_colons_survive(self, tmp_path: Path) -> None:
        # The old hand-built f-string frontmatter produced invalid YAML here.
        from noscope.spec.parser import build_spec_file

        tricky = 'The "Best" App: v2'
        path = tmp_path / "s.md"
        path.write_text(
            build_spec_file(
                name=tricky,
                timebox="5m",
                constraints=['Must say "hello"', "Use A: B"],
                acceptance=[],
                body="body",
            ),
            encoding="utf-8",
        )
        spec = parse_spec(path)
        assert spec.name == tricky
        assert spec.constraints == ['Must say "hello"', "Use A: B"]


class TestSlugify:
    def test_basic(self) -> None:
        from noscope.spec.parser import slugify

        assert slugify("My Project") == "my-project"

    def test_strips_unsafe_characters(self) -> None:
        from noscope.spec.parser import slugify

        # A name with a path separator must not escape into a directory.
        assert "/" not in slugify("a/../b")
        assert slugify('The "Best": v2!') == "the-best-v2"

    def test_falls_back_when_empty(self) -> None:
        from noscope.spec.parser import slugify

        assert slugify("!!!") == "spec"
