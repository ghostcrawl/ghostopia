"""ghostopia-task-manager — the Task/mission management COMMAND surface.

A **Task** is declarative, parameterized DATA (a validated
:class:`~ghostopia_shared.task.TaskSpec` naming a vetted behavior) distinct from a
**Behavior** (the executable HOW). This package ships:

* :class:`~ghostopia_task_manager.task_manager.TaskManager` — the nine server-authoritative
  verbs (create/assign/run/update/pause/resume/retarget/cancel/monitor) that VALIDATE every
  spec/patch and compose over the bounded :class:`~ghostopia_orchestration.WorkQueue` +
  section role fan-out, never bypassing the governor-safe queue;
* :mod:`~ghostopia_task_manager.task_store` — the injectable :class:`TaskStore` Protocol
  (in-memory default + a stdlib-``sqlite3`` adapter seam), so the server binds durable
  persistence while the package never imports a db.

AI + non-coders drive the harness by emitting a validated ``TaskSpec`` with ZERO
code execution — a hallucinated field / unknown behavior / bad params is rejected.
"""

from __future__ import annotations

from ghostopia_task_manager.task_manager import (
    TaskManager,
    TaskManagerError,
    TaskRoutingError,
    TaskStateError,
    TaskValidationError,
    preview_route,
)
from ghostopia_task_manager.task_store import (
    InMemoryTaskStore,
    MissionRecord,
    RecordNotFoundError,
    SqliteTaskStore,
    TaskRecord,
    TaskStore,
)

__all__ = [
    "InMemoryTaskStore",
    "MissionRecord",
    "RecordNotFoundError",
    "SqliteTaskStore",
    "TaskManager",
    "TaskManagerError",
    "TaskRecord",
    "TaskRoutingError",
    "TaskStateError",
    "TaskStore",
    "TaskValidationError",
    "preview_route",
]
