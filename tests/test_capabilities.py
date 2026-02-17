"""Tests for the capability model."""

from __future__ import annotations

from noscope.capabilities import (
    Capability,
    CapabilityGrant,
    CapabilityRequest,
    CapabilityStore,
)


class TestCapabilityStore:
    def test_grant_and_check(self) -> None:
        store = CapabilityStore()
        store.grant("workspace_rw")
        assert store.check("workspace_rw") is True

    def test_check_ungranted(self) -> None:
        store = CapabilityStore()
        assert store.check("shell_exec") is False

    def test_deny(self) -> None:
        store = CapabilityStore()
        store.grant("shell_exec")
        store.deny("shell_exec")
        assert store.check("shell_exec") is False

    def test_check_with_enum(self) -> None:
        store = CapabilityStore()
        store.grant(Capability.GIT.value)
        assert store.check(Capability.GIT) is True

    def test_from_grants(self) -> None:
        grants = [
            CapabilityGrant(cap="workspace_rw", approved=True),
            CapabilityGrant(cap="shell_exec", approved=False),
        ]
        store = CapabilityStore(grants)
        assert store.check("workspace_rw") is True
        assert store.check("shell_exec") is False

    def test_secret_capability(self) -> None:
        store = CapabilityStore()
        store.grant("secrets:API_KEY")
        assert store.get_secret("API_KEY") is True
        assert store.get_secret("OTHER") is False

    def test_to_grants(self) -> None:
        store = CapabilityStore()
        store.grant("workspace_rw")
        store.deny("docker")
        grants = store.to_grants()
        assert len(grants) == 2


class TestCapabilityRequest:
    def test_model(self) -> None:
        req = CapabilityRequest(
            cap="shell_exec",
            why="Need to install dependencies",
            risk="medium",
        )
        assert req.cap == "shell_exec"
        assert req.risk == "medium"
