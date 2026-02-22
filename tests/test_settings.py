"""Tests for settings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from noscope.config.settings import NoscopeSettings

# Keys that must be cleared to isolate tests from the user's .env file
_CLEAR_KEYS = {
    "NOSCOPE_ANTHROPIC_API_KEY": "",
    "NOSCOPE_OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_API_KEY": "",
}


class TestSettings:
    def test_requires_api_key(self) -> None:
        with (
            patch.dict(os.environ, _CLEAR_KEYS, clear=False),
            pytest.raises(ValueError, match="API key"),
        ):
            NoscopeSettings(_env_file=None)  # type: ignore[call-arg]

    def test_anthropic_key_only(self) -> None:
        env = {**_CLEAR_KEYS, "NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.anthropic_api_key == "sk-test"
            assert not s.openai_api_key  # None or empty string

    def test_openai_key_only(self) -> None:
        env = {**_CLEAR_KEYS, "NOSCOPE_OPENAI_API_KEY": "sk-test"}
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.openai_api_key == "sk-test"

    def test_both_keys(self) -> None:
        env = {
            **_CLEAR_KEYS,
            "NOSCOPE_ANTHROPIC_API_KEY": "sk-ant",
            "NOSCOPE_OPENAI_API_KEY": "sk-oai",
        }
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.anthropic_api_key == "sk-ant"
            assert s.openai_api_key == "sk-oai"

    def test_fallback_to_standard_env_vars(self) -> None:
        env = {**_CLEAR_KEYS, "ANTHROPIC_API_KEY": "sk-fallback"}
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.anthropic_api_key == "sk-fallback"

    def test_default_timebox(self) -> None:
        env = {**_CLEAR_KEYS, "NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.default_timebox == "30m"

    def test_danger_mode_default_false(self) -> None:
        env = {**_CLEAR_KEYS, "NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}
        with patch.dict(os.environ, env, clear=False):
            s = NoscopeSettings(_env_file=None)  # type: ignore[call-arg]
            assert s.danger_mode is False
