"""Detect when parallel build agents write over each other.

The planner is told to give each task its own files, but nothing enforces it,
and a model that ignores the instruction produces the worst kind of failure:
two workers both "succeed", the last write wins, and the run reports a clean
build over silently discarded work.

The ledger records who last wrote each file. A different agent overwriting the
same path with different content is reported back to that agent immediately —
"re-read this file before you edit it" — and collected for the handoff report,
so the loss is visible rather than inferred later from missing code.

Writes are warned about, not blocked. Overlap is sometimes legitimate (a worker
adding a dependency to a manifest the setup agent created), and a harness that
refuses writes mid-build would fail more runs than it saves.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WriteConflict:
    """One agent overwrote a file another agent had written."""

    path: str
    previous_agent: str
    current_agent: str

    def describe(self) -> str:
        return (
            f"{self.path} was written by {self.previous_agent}, "
            f"then overwritten by {self.current_agent}"
        )


@dataclass
class WriteLedger:
    """Tracks the last writer of each workspace path. Shared across agents."""

    _last_writer: dict[str, tuple[str, str]] = field(default_factory=dict)
    conflicts: list[WriteConflict] = field(default_factory=list)

    def record(self, path: str, agent_id: str, content: str) -> WriteConflict | None:
        """Record a write. Returns a conflict if it clobbered another agent's."""
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        previous = self._last_writer.get(path)
        self._last_writer[path] = (agent_id, digest)

        if previous is None:
            return None
        previous_agent, previous_digest = previous
        if previous_agent == agent_id:
            return None
        # Rewriting a file to exactly what it already held costs nothing.
        if previous_digest == digest:
            return None

        conflict = WriteConflict(path=path, previous_agent=previous_agent, current_agent=agent_id)
        self.conflicts.append(conflict)
        return conflict

    def paths_touched_by(self, agent_id: str) -> list[str]:
        return sorted(p for p, (writer, _) in self._last_writer.items() if writer == agent_id)

    def summary(self) -> str:
        """A short report line, or empty when the parallel build stayed clean."""
        if not self.conflicts:
            return ""
        # One line per distinct path — repeated clobbers of the same file are
        # one problem, not several.
        seen: dict[str, WriteConflict] = {}
        for c in self.conflicts:
            seen.setdefault(c.path, c)
        lines = [f"  - {c.describe()}" for c in seen.values()]
        return "Parallel agents overwrote each other's files:\n" + "\n".join(lines)
