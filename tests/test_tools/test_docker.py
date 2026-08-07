"""Tests for Docker sandbox path safety and file writes (pure helpers)."""

from __future__ import annotations

import base64
import re

import pytest

from noscope.tools.docker import build_write_command, preflight_docker, safe_container_path


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


@pytest.mark.asyncio
class TestPreflightDocker:
    async def test_missing_binary_is_explained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("noscope.tools.docker.shutil.which", lambda _: None)
        problem = await preflight_docker()
        assert problem is not None
        assert "docker" in problem
        # The point of the check is telling the user what to do instead.
        assert "--sandbox" in problem

    async def test_unreachable_daemon_reports_what_it_said(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noscope.tools.docker.shutil.which", lambda _: "/usr/bin/docker")

        async def fake_exec(*args: object, **kwargs: object) -> object:
            class _Proc:
                returncode = 1

                async def communicate(self) -> tuple[bytes, bytes]:
                    return b"", b"Cannot connect to the Docker daemon\nmore detail"

            return _Proc()

        monkeypatch.setattr("noscope.tools.docker.asyncio.create_subprocess_exec", fake_exec)
        problem = await preflight_docker()
        assert problem is not None
        assert "daemon is not reachable" in problem
        assert "Cannot connect to the Docker daemon" in problem

    async def test_healthy_daemon_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("noscope.tools.docker.shutil.which", lambda _: "/usr/bin/docker")

        async def fake_exec(*args: object, **kwargs: object) -> object:
            class _Proc:
                returncode = 0

                async def communicate(self) -> tuple[bytes, bytes]:
                    return b"27.0.3\n", b""

            return _Proc()

        monkeypatch.setattr("noscope.tools.docker.asyncio.create_subprocess_exec", fake_exec)
        assert await preflight_docker() is None
