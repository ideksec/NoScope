"""Tests for secret redaction."""

from __future__ import annotations

from noscope.tools.redaction import redact, redact_env_vars, redact_structured


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

    def test_authorization_header_pattern(self) -> None:
        text = "Authorization: Bearer sk-test-token-abcdefghijklmnop"
        result = redact_env_vars(text)
        assert "sk-test-token-abcdefghijklmnop" not in result
        assert "[REDACTED:auto]" in result

    def test_private_key_block_pattern(self) -> None:
        text = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC...\n"
            "-----END PRIVATE KEY-----"
        )
        result = redact_env_vars(text)
        assert "PRIVATE KEY" not in result
        assert result == "[REDACTED:auto]"

    def test_normal_text_unchanged(self) -> None:
        text = "This is normal text with no secrets"
        assert redact_env_vars(text) == text


class TestRedactStructured:
    def test_nested_structures_redacted(self) -> None:
        payload = {
            "token": "Authorization: Bearer sk-abc12345678901234567890",
            "nested": [{"password": "hunter2"}],
        }
        result = redact_structured(payload, {"db_password": "hunter2"})
        assert "hunter2" not in str(result)
        assert "sk-abc12345678901234567890" not in str(result)
