"""Tests for actionable API error explanations."""

from __future__ import annotations

from noscope.errors import explain_error, format_run_error


class _FakeError(Exception):
    """Stand-in for an SDK exception (matched by class name / status)."""

    def __init__(self, name: str, status: int | None = None, msg: str = "boom") -> None:
        super().__init__(msg)
        self.__class__.__name__ = name
        if status is not None:
            self.status_code = status


class TestExplainError:
    def test_auth_error_names_the_env_var(self) -> None:
        out = explain_error(_FakeError("AuthenticationError"), provider="anthropic")
        assert out is not None
        assert "NOSCOPE_ANTHROPIC_API_KEY" in out
        assert "doctor --live" in out

    def test_auth_error_is_provider_specific(self) -> None:
        out = explain_error(_FakeError("AuthenticationError"), provider="openai")
        assert out is not None
        assert "NOSCOPE_OPENAI_API_KEY" in out

    def test_model_not_found_suggests_current_models(self) -> None:
        out = explain_error(_FakeError("NotFoundError"), model="claude-old")
        assert out is not None
        assert "claude-old" in out
        assert "claude-sonnet-5" in out

    def test_rate_limit(self) -> None:
        out = explain_error(_FakeError("RateLimitError"))
        assert out is not None
        assert "Rate limited" in out
        assert "--workers" in out

    def test_overloaded_by_status(self) -> None:
        out = explain_error(_FakeError("APIStatusError", status=529))
        assert out is not None
        assert "overloaded" in out.lower()

    def test_matches_on_status_when_name_is_unknown(self) -> None:
        # Providers rename exception classes; the HTTP status still identifies it.
        out = explain_error(_FakeError("SomeNewSdkError", status=401))
        assert out is not None
        assert "rejected your credentials" in out

    def test_connection_error(self) -> None:
        assert explain_error(_FakeError("APIConnectionError")) is not None

    def test_unknown_error_returns_none(self) -> None:
        assert explain_error(ValueError("something else")) is None


class TestFormatRunError:
    def test_known_error_includes_diagnosis_and_details(self) -> None:
        out = format_run_error(
            _FakeError("AuthenticationError", msg="bad key"), provider="anthropic"
        )
        assert "NOSCOPE_ANTHROPIC_API_KEY" in out
        assert "bad key" in out

    def test_unknown_error_still_readable(self) -> None:
        out = format_run_error(ValueError("plain problem"))
        assert "ValueError" in out
        assert "plain problem" in out
