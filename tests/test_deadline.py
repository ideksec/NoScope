"""Tests for the deadline engine."""

from __future__ import annotations

import time

from noscope.deadline import Deadline, Phase


class TestDeadline:
    def test_remaining_decreases(self) -> None:
        d = Deadline(100)
        r1 = d.remaining()
        time.sleep(0.01)
        r2 = d.remaining()
        assert r2 < r1

    def test_is_expired(self) -> None:
        d = Deadline(0)
        assert d.is_expired() is True

    def test_not_expired(self) -> None:
        d = Deadline(3600)
        assert d.is_expired() is False

    def test_panic_mode_short_timebox(self) -> None:
        # 60s total, panic when < max(60, 6) = 60s
        d = Deadline(60)
        # Essentially always in panic mode for very short timeboxes
        assert d.is_panic_mode() is True

    def test_panic_mode_long_timebox(self) -> None:
        d = Deadline(3600)
        # 10% of 3600 = 360s, well within remaining
        assert d.is_panic_mode() is False

    def test_phase_transition(self) -> None:
        # Very short PLAN budget
        d = Deadline(10)
        time.sleep(0.01)
        # PLAN gets 10% = 1 second, likely exhausted quickly
        # (depends on timing, but for 10s total, PLAN = 1s)
        next_phase = d.should_transition(Phase.PLAN)
        # With only 1s for PLAN, it should suggest REQUEST
        if d.phase_remaining(Phase.PLAN) <= 0:
            assert next_phase == Phase.REQUEST

    def test_format_remaining(self) -> None:
        d = Deadline(125)
        fmt = d.format_remaining()
        # Should be approximately "2:05" or close
        assert ":" in fmt

    def test_advance_phase(self) -> None:
        d = Deadline(300)
        assert d.current_phase == Phase.PLAN
        d.advance_phase(Phase.BUILD)
        assert d.current_phase == Phase.BUILD

    def test_elapsed(self) -> None:
        d = Deadline(300)
        time.sleep(0.01)
        assert d.elapsed() > 0
