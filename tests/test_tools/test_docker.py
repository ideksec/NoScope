"""Tests for Docker sandbox path safety and file writes (pure helpers)."""

from __future__ import annotations

import base64
import re

import pytest

from noscope.tools.docker import build_write_command, safe_container_path


class TestSafeContainerPath:
    def test_accepts_normal_paths(self) -> None:
        assert safe_container_path("app.py") == "app.py"
        assert safe_container_path("src/main.py") == "src/main.py"
        assert safe_container_path("./config.json") == "config.json"
        assert safe_container_path(".") == "."

    @pytest.mark.parametrize(
        "bad",
        [
            "/etc/passwd",
            "../secret",
            "a/../../b",
            "a; rm -rf /",
            "a$(whoami)",
            "a`id`",
            'a" b',
            "a' b",
            "a | b",
        ],
    )
    def test_rejects_unsafe_paths(self, bad: str) -> None:
        with pytest.raises(ValueError):
            safe_container_path(bad)


class TestBuildWriteCommand:
    def _decode(self, cmd: str) -> str:
        m = re.search(r"printf %s '([^']+)'", cmd)
        assert m is not None
        return base64.b64decode(m.group(1)).decode("utf-8")

    def test_round_trips_tricky_content(self) -> None:
        # Backslashes, quotes, the old heredoc marker, and newlines all survive
        # exactly — the failure modes of the previous heredoc implementation.
        content = "x = 1\n\\n not a newline\n'single'\n\"double\"\nNOSCOPE_EOF\n"
        cmd = build_write_command("src/app.py", content)
        assert self._decode(cmd) == content
        assert "/workspace/src/app.py" in cmd
        assert "mkdir -p '/workspace/src'" in cmd

    def test_no_mkdir_for_root_file(self) -> None:
        cmd = build_write_command("app.py", "print(1)\n")
        assert "mkdir" not in cmd
        assert self._decode(cmd) == "print(1)\n"

    def test_rejects_unsafe_path(self) -> None:
        with pytest.raises(ValueError):
            build_write_command("../escape.py", "data")
