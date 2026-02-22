"""Timebox deadline engine."""

from __future__ import annotations

import time
from enum import StrEnum


class Phase(StrEnum):
    PLAN = "PLAN"
    REQUEST = "REQUEST"
    BUILD = "BUILD"
    HARDEN = "HARDEN"
    VERIFY = "VERIFY"
    HANDOFF = "HANDOFF"


# Default time allocation per phase (fraction of total)
DEFAULT_ALLOCATION: dict[Phase, float] = {
    Phase.PLAN: 0.10,
    Phase.REQUEST: 0.00,  # REQUEST is interactive, effectively zero budget
    Phase.BUILD: 0.50,
    Phase.HARDEN: 0.25,
    Phase.VERIFY: 0.10,
    Phase.HANDOFF: 0.05,
}

# Phase ordering for transitions
PHASE_ORDER: list[Phase] = [
    Phase.PLAN,
    Phase.REQUEST,
    Phase.BUILD,
    Phase.HARDEN,
    Phase.VERIFY,
    Phase.HANDOFF,
]


class Deadline:
    """Manages the global timebox and per-phase budgets."""

    def __init__(
        self,
        total_seconds: int,
        allocation: dict[Phase, float] | None = None,
    ) -> None:
        self.total_seconds = total_seconds
        self.allocation = allocation or DEFAULT_ALLOCATION
        self._start = time.monotonic()
        self._deadline = self._start + total_seconds
        self._current_phase = Phase.PLAN
        self._phase_start = self._start

        # Compute cumulative phase deadlines
        self._phase_deadlines: dict[Phase, float] = {}
        cumulative = 0.0
        for phase in PHASE_ORDER:
            cumulative += self.allocation.get(phase, 0.0)
            self._phase_deadlines[phase] = self._start + (total_seconds * cumulative)

    @property
    def current_phase(self) -> Phase:
        return self._current_phase

    def advance_phase(self, phase: Phase) -> None:
        """Manually advance to a specific phase."""
        self._current_phase = phase
        self._phase_start = time.monotonic()

    def elapsed(self) -> float:
        """Seconds elapsed since start."""
        return time.monotonic() - self._start

    def remaining(self) -> float:
        """Seconds remaining in the total timebox."""
        return max(0.0, self._deadline - time.monotonic())

    def phase_remaining(self, phase: Phase | None = None) -> float:
        """Seconds remaining for the given (or current) phase."""
        phase = phase or self._current_phase
        deadline = self._phase_deadlines.get(phase, self._deadline)
        return max(0.0, deadline - time.monotonic())

    def is_expired(self) -> bool:
        """True if the global deadline has passed."""
        return time.monotonic() >= self._deadline

    def is_panic_mode(self) -> bool:
        """True if remaining time < max(60s, 10% of total)."""
        threshold = max(60.0, self.total_seconds * 0.10)
        return self.remaining() < threshold

    def should_transition(self, current_phase: Phase | None = None) -> Phase | None:
        """Suggest the next phase if the current phase's time budget is exhausted."""
        current = current_phase or self._current_phase
        if self.phase_remaining(current) <= 0:
            idx = PHASE_ORDER.index(current)
            if idx + 1 < len(PHASE_ORDER):
                return PHASE_ORDER[idx + 1]
        return None

    def format_remaining(self) -> str:
        """Human-readable remaining time."""
        secs = self.remaining()
        if secs <= 0:
            return "0:00"
        minutes = int(secs // 60)
        seconds = int(secs % 60)
        return f"{minutes}:{seconds:02d}"
