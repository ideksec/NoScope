"""Tests for secret redaction."""

from __future__ import annotations

from noscope.tools.redaction import redact, redact_env_vars


class TestRedact:
    def test_basic_redaction(self) -> None:
        text = "api key is sk-abc123 and secret is hunter2"
        secrets = {"api_key": "sk-abc123", "password": "hunter2"}
        result = redact(text, secrets)
        assert "sk-abc123" not in result
        assert "hunter2" not in result
        assert "[REDACTED:api_key]" in result
        assert "[REDACTED:password]" in result

    def test_no_secrets(self) -> None:
        text = "nothing to redact"
        assert redact(text, {}) == text

    def test_empty_value_ignored(self) -> None:
        text = "hello world"
        assert redact(text, {"key": ""}) == text


class TestRedactEnvVars:
    def test_api_key_pattern(self) -> None:
        text = "API_KEY=sk-verysecretkey123"
        result = redact_env_vars(text)
        assert "sk-verysecretkey123" not in result

    def test_anthropic_key_pattern(self) -> None:
        text = "key is sk-ant-api03-xxxxxxxxxxxxxxxxxxxx"
        result = redact_env_vars(text)
        assert "sk-ant-" not in result

    def test_normal_text_unchanged(self) -> None:
        text = "This is normal text with no secrets"
        assert redact_env_vars(text) == text
