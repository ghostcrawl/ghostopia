"""The harness Task/mission contract.

A **Task** is declarative DATA (the WHAT) that names a **Behavior** (the HOW) and
parameterizes it — the seam that lets non-coders AND an AI be productive without
writing code. Two AI-safety properties are load-bearing:

- ``extra='forbid'`` (``ConfigDict(extra="forbid")``) rejects hallucinated fields; an
  invalid/hallucinated spec fails validation and is NEVER executed (decode-only).
- ``concurrency`` is capped 1..50 (default 5) so an AI/user cannot over-fan the
  proxy-exit-bound governor.

This module also owns the ``task.*``/``mission.*`` management-verb message variants so
plans 20/21/22 depend only on ``ghostopia-shared``. The concrete behavior registry +
server verbs live downstream; the shape anchors here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Base: reject unknown keys everywhere (AI-safety)."""

    model_config = ConfigDict(extra="forbid")


class TaskTarget(_Strict):
    """Where the work runs (dual-target: cloud OR self-host)."""

    gc_target: Literal["cloud", "selfhost"] = "cloud"
    section: str | None = None
    profile: str | None = None


class TaskInputs(_Strict):
    """The work payload."""

    urls: list[str] | None = None
    query: str | None = None
    extract_schema: dict[str, Any] | None = None


class RetrySpec(_Strict):
    """Retry policy; honors the SDK ``retry_after`` from mapped errors by default."""

    max: int = 2
    honor_retry_after: bool = True


class TaskSpec(_Strict):
    """A declarative, parameterized task. Emittable by non-coders and AI; validated
    against this schema + the named behavior's own ``paramSchema`` at assign time."""

    id: str | None = None
    title: str
    behavior: str  # MUST exist in the behavior registry — validated downstream
    target: TaskTarget = Field(default_factory=TaskTarget)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: TaskInputs = Field(default_factory=TaskInputs)
    concurrency: int = Field(default=5, ge=1, le=50)
    identities: list[str] | None = None
    retry: RetrySpec = Field(default_factory=RetrySpec)


class MissionSpec(_Strict):
    """A named collection of tasks (the '500 companies' fan-out)."""

    id: str | None = None
    title: str
    tasks: list[TaskSpec] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Management-verb message variants — WS/HTTP command surface.
# Server-authoritative; the concrete handlers live in the task-manager.
# --------------------------------------------------------------------------------------


class TaskCreate(_Strict):
    spec: TaskSpec


class TaskAssign(_Strict):
    task_id: str
    section: str | None = None
    ghost_ids: list[str] | None = None


class TaskRun(_Strict):
    task_id: str


class TaskUpdate(_Strict):
    task_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class TaskPause(_Strict):
    task_id: str


class TaskResume(_Strict):
    task_id: str


class TaskRetarget(_Strict):
    task_id: str
    target: TaskTarget


class TaskCancel(_Strict):
    task_id: str


class TaskMonitor(_Strict):
    """``task.subscribe`` — stream ``task.*``/``result.*``/``ghost.*`` for a task."""

    task_id: str


class MissionCreate(_Strict):
    spec: MissionSpec


class MissionAssign(_Strict):
    mission_id: str
    section: str | None = None


class MissionRun(_Strict):
    mission_id: str


class MissionUpdate(_Strict):
    mission_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class MissionPause(_Strict):
    mission_id: str


class MissionResume(_Strict):
    mission_id: str


class MissionRetarget(_Strict):
    mission_id: str
    target: TaskTarget


class MissionCancel(_Strict):
    mission_id: str


class MissionMonitor(_Strict):
    mission_id: str
