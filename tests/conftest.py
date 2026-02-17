"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from noscope.capabilities import Capability, CapabilityStore
from noscope.deadline import Deadline
from noscope.logging.events import EventLog, RunDir
from noscope.tools.base import ToolContext


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def run_dir(tmp_path: Path) -> RunDir:
    """Create a temporary run directory."""
    return RunDir(base=tmp_path / "runs")


@pytest.fixture
def event_log(run_dir: RunDir) -> EventLog:
    """Create an event log in a temporary run directory."""
    log = EventLog(run_dir)
    yield log
    log.close()


@pytest.fixture
def capability_store() -> CapabilityStore:
    """Create a capability store with all capabilities granted."""
    store = CapabilityStore()
    for cap in Capability:
        store.grant(cap.value)
    return store


@pytest.fixture
def deadline() -> Deadline:
    """Create a deadline with 5 minutes."""
    return Deadline(300)


@pytest.fixture
def tool_context(
    tmp_workspace: Path,
    capability_store: CapabilityStore,
    event_log: EventLog,
    deadline: Deadline,
) -> ToolContext:
    """Create a tool context for testing."""
    return ToolContext(
        workspace=tmp_workspace,
        capabilities=capability_store,
        event_log=event_log,
        deadline=deadline,
    )


@pytest.fixture
def sample_spec_path(tmp_path: Path) -> Path:
    """Create a sample spec file."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        '''---
name: "Test Project"
timebox: "5m"
constraints:
  - "Use Python"
acceptance:
  - "cmd: python --version"
  - "Has a README"
---

# Test Project

Build a simple test project.
''',
        encoding="utf-8",
    )
    return spec
