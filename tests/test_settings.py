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


class TestCostEstimation:
    def test_known_model_has_cost(self) -> None:
        from noscope.ui.console import estimate_cost

        cost = estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000)
        assert cost == 18.0  # $3 in + $15 out per MTok

    def test_unknown_model_returns_none(self) -> None:
        from noscope.ui.console import estimate_cost

        assert estimate_cost("some-future-model", 1000, 1000) is None


class TestCacheAwareCost:
    def test_cache_read_is_cheaper_and_reports_savings(self) -> None:
        from noscope.ui.console import estimate_cost_detailed

        # 1M uncached vs 1M cache-read on claude-sonnet-5 ($3/MTok input)
        uncached, _ = estimate_cost_detailed("claude-sonnet-5", 1_000_000, 0)
        cached, savings = estimate_cost_detailed("claude-sonnet-5", 0, 0, 0, 1_000_000)
        assert cached is not None and uncached is not None
        assert cached < uncached  # reads bill at ~0.1x
        # savings ≈ full input price ($3) minus the 0.1x actually paid ($0.30)
        assert abs(savings - 2.70) < 0.01

    def test_unknown_model_none(self) -> None:
        from noscope.ui.console import estimate_cost_detailed

        cost, savings = estimate_cost_detailed("mystery", 100, 100, 0, 100)
        assert cost is None
        assert savings == 0.0
