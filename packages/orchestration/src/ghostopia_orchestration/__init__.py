"""ghostopia-orchestration — the STAGE-6 execution engine (mission split + WorkQueue).

Splits one mission into many kind-tagged tasks (:func:`split_mission`) and drives them
through a bounded, backoff-aware :class:`WorkQueue` that fans out to sections BY ROLE
and honours GhostCrawl's concurrency limits (``retry_after`` backoff, section
capacity). The queue is the SINGLE dispatch choke point the Task/mission management
API composes over — nothing bypasses it (governor safety).
"""

from __future__ import annotations

from ghostopia_orchestration.backoff import compute_backoff
from ghostopia_orchestration.mission_splitter import MissionRequest, split_mission
from ghostopia_orchestration.work_queue import (
    DispatchResult,
    TaskOutcome,
    WorkQueue,
)

__all__ = [
    "DispatchResult",
    "MissionRequest",
    "TaskOutcome",
    "WorkQueue",
    "compute_backoff",
    "split_mission",
]
