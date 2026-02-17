"""Tests for settings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from noscope.config.settings import NoscopeSettings


class TestSettings:
    def test_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            NoscopeSettings()

    def test_anthropic_key_only(self) -> None:
        with patch.dict(os.environ, {"NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}):
            s = NoscopeSettings()
            assert s.anthropic_api_key == "sk-test"
            assert s.openai_api_key is None

    def test_openai_key_only(self) -> None:
        with patch.dict(os.environ, {"NOSCOPE_OPENAI_API_KEY": "sk-test"}):
            s = NoscopeSettings()
            assert s.openai_api_key == "sk-test"

    def test_both_keys(self) -> None:
        with patch.dict(
            os.environ,
            {
                "NOSCOPE_ANTHROPIC_API_KEY": "sk-ant",
                "NOSCOPE_OPENAI_API_KEY": "sk-oai",
            },
        ):
            s = NoscopeSettings()
            assert s.anthropic_api_key == "sk-ant"
            assert s.openai_api_key == "sk-oai"

    def test_default_timebox(self) -> None:
        with patch.dict(os.environ, {"NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}):
            s = NoscopeSettings()
            assert s.default_timebox == "30m"

    def test_danger_mode_default_false(self) -> None:
        with patch.dict(os.environ, {"NOSCOPE_ANTHROPIC_API_KEY": "sk-test"}):
            s = NoscopeSettings()
            assert s.danger_mode is False
