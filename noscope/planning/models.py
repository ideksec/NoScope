"""Planning output models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from noscope.capabilities import CapabilityRequest


class PlanTask(BaseModel):
    """A single task in the build plan."""

    id: str
    title: str
    kind: Literal["edit", "shell", "test"]
    priority: Literal["mvp", "stretch"] = "mvp"
    description: str = ""
    completed: bool = False
    depends_on: list[str] = []


class AcceptancePlan(BaseModel):
    """An acceptance check from the plan."""

    name: str
    cmd: str | None = None
    must_pass: bool = True


class PlanOutput(BaseModel):
    """Full output from the planning phase."""

    requested_capabilities: list[CapabilityRequest] = []
    tasks: list[PlanTask] = []
    mvp_definition: list[str] = []
    exclusions: list[str] = []
    acceptance_plan: list[AcceptancePlan] = []
