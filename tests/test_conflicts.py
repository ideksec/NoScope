"""Tests for cross-agent write conflict detection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from noscope.conflicts import WriteLedger
from noscope.tools.base import ToolContext
from noscope.tools.filesystem import EditFileTool, WriteFileTool


class TestWriteLedger:
    def test_first_write_is_never_a_conflict(self) -> None:
        assert WriteLedger().record("app.py", "worker-1", "x = 1") is None

    def test_same_agent_rewriting_its_own_file_is_fine(self) -> None:
        ledger = WriteLedger()
        ledger.record("app.py", "worker-1", "x = 1")
        assert ledger.record("app.py", "worker-1", "x = 2") is None
        assert ledger.conflicts == []

    def test_different_agent_overwriting_is_a_conflict(self) -> None:
        ledger = WriteLedger()
        ledger.record("app.py", "worker-1", "x = 1")
        conflict = ledger.record("app.py", "worker-2", "y = 2")

        assert conflict is not None
        assert conflict.previous_agent == "worker-1"
        assert conflict.current_agent == "worker-2"
        assert ledger.conflicts == [conflict]

    def test_identical_content_is_not_a_conflict(self) -> None:
        # Two agents converging on the same file content lost nothing, so
        # warning about it would just be noise.
        ledger = WriteLedger()
        ledger.record("requirements.txt", "setup-deps", "flask\n")
        assert ledger.record("requirements.txt", "setup-structure", "flask\n") is None
        assert ledger.conflicts == []

    def test_distinct_files_are_tracked_separately(self) -> None:
        ledger = WriteLedger()
        ledger.record("a.py", "worker-1", "a")
        assert ledger.record("b.py", "worker-2", "b") is None

    def test_summary_reports_each_path_once(self) -> None:
        # A file clobbered repeatedly is one problem to investigate, not three.
        ledger = WriteLedger()
        ledger.record("app.py", "worker-1", "v1")
        ledger.record("app.py", "worker-2", "v2")
        ledger.record("app.py", "worker-1", "v3")
        ledger.record("app.py", "worker-2", "v4")

        summary = ledger.summary()
        assert summary.count("app.py") == 1
        assert "worker-1" in summary and "worker-2" in summary

    def test_summary_is_empty_when_clean(self) -> None:
        ledger = WriteLedger()
        ledger.record("a.py", "worker-1", "a")
        assert ledger.summary() == ""

    def test_paths_touched_by_reflects_the_last_writer(self) -> None:
        ledger = WriteLedger()
        ledger.record("a.py", "worker-1", "a")
        ledger.record("b.py", "worker-1", "b")
        ledger.record("a.py", "worker-2", "a2")

        assert ledger.paths_touched_by("worker-1") == ["b.py"]
        assert ledger.paths_touched_by("worker-2") == ["a.py"]


@pytest.mark.asyncio
class TestConflictReachesTheAgent:
    async def test_write_tool_warns_the_clobbering_agent(self, tool_context: ToolContext) -> None:
        # The warning has to land in the tool output, because that is the only
        # channel back into the agent's conversation. A conflict recorded but
        # not surfaced would let the agent keep building on discarded work.
        ledger = WriteLedger()
        first = replace(tool_context, agent_id="worker-1", write_ledger=ledger)
        second = replace(tool_context, agent_id="worker-2", write_ledger=ledger)
        tool = WriteFileTool()

        clean = await tool.execute({"path": "app.py", "content": "v1"}, first)
        assert "warning" not in clean.display

        clobber = await tool.execute({"path": "app.py", "content": "v2"}, second)
        assert clobber.status == "ok"  # warned, not blocked
        assert "worker-1" in clobber.display
        assert "read the file back" in clobber.display

    async def test_edit_tool_also_records(self, tool_context: ToolContext) -> None:
        ledger = WriteLedger()
        first = replace(tool_context, agent_id="worker-1", write_ledger=ledger)
        second = replace(tool_context, agent_id="worker-2", write_ledger=ledger)

        await WriteFileTool().execute({"path": "app.py", "content": "x = 1\n"}, first)
        result = await EditFileTool().execute(
            {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}, second
        )
        assert result.status == "ok"
        assert len(ledger.conflicts) == 1
        assert ledger.conflicts[0].path == "app.py"

    async def test_no_ledger_means_no_behavior_change(self, tool_context: ToolContext) -> None:
        # Contexts without a ledger (single-agent paths, older call sites) must
        # keep working exactly as before.
        ctx = replace(tool_context, write_ledger=None)
        result = await WriteFileTool().execute({"path": "a.py", "content": "x"}, ctx)
        assert result.status == "ok"
        assert "warning" not in result.display
