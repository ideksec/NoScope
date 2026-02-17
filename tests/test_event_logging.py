"""Tests for event logging."""

from __future__ import annotations

import json
from pathlib import Path

from noscope.logging.events import EventLog, RunDir


class TestRunDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        assert rd.path.exists()
        assert rd.path.is_dir()

    def test_has_expected_paths(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        assert rd.events_path.name == "events.jsonl"
        assert rd.plan_path.name == "plan.json"
        assert rd.contract_path.name == "contract.json"
        assert rd.handoff_path.name == "handoff.md"

    def test_run_id_format(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        # Format: YYYYMMDDTHHMMZ_<8hex>
        parts = rd.run_id.split("_")
        assert len(parts) == 2
        assert parts[0].endswith("Z")
        assert len(parts[1]) == 8


class TestEventLog:
    def test_emit_and_read(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        log = EventLog(rd)
        log.emit("BUILD", "test.event", "Test summary", {"key": "value"})
        log.close()

        lines = rd.events_path.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["phase"] == "BUILD"
        assert event["type"] == "test.event"
        assert event["summary"] == "Test summary"
        assert event["data"]["key"] == "value"
        assert event["seq"] == 1

    def test_sequential_seq(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        log = EventLog(rd)
        log.emit("BUILD", "e1", "First")
        log.emit("BUILD", "e2", "Second")
        log.emit("BUILD", "e3", "Third")
        log.close()

        lines = rd.events_path.read_text().strip().split("\n")
        seqs = [json.loads(line)["seq"] for line in lines]
        assert seqs == [1, 2, 3]

    def test_result_field(self, tmp_path: Path) -> None:
        rd = RunDir(base=tmp_path / "runs")
        log = EventLog(rd)
        log.emit("HARDEN", "check", "Test", result={"passed": True})
        log.close()

        event = json.loads(rd.events_path.read_text().strip())
        assert event["result"]["passed"] is True
