"""Mission/task → section fan-out.

Task routing is **by section ROLE, not by ghost**: a task routes to an explicit
``TaskSpec.target.section`` when set (validated it exists + ``accepts`` the kind), else
to the FIRST section whose ``accepts`` includes ``task.kind`` — honoring a spawning
section's ``routes_to`` preference order. A section at ``capacity`` queues the task in
its sub-queue rather than over-assigning; a freed slot drains the next queued task. The
resolved section's default ``gc_target`` applies when the task omits
its own. An unroutable kind returns an explicit ``routed=False`` result (never a raise).

These are PURE functions over the :class:`~ghostopia_sections.section.Section` runtimes —
no ``ghostcrawl`` import, no server/SDK. The orchestrator + task-manager
inject the live pool and invoke :func:`route_task` as the routing callback for every
``task.spawned`` / ``task.assign``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ghostopia_shared.types import Mission, Task

from ghostopia_sections.section import GcTarget, Section

__all__ = ["RouteResult", "fan_out_mission", "route_task"]


@dataclass(frozen=True)
class RouteResult:
    """The explicit outcome of routing one task (never a raise-into-the-void).

    * ``routed``    — True when a section accepted the task (assigned OR queued).
    * ``section``   — the section id it routed to (``None`` when unroutable).
    * ``ghost_id``  — the roster ghost it was assigned to, or ``None`` when queued.
    * ``queued``    — True when the section was at capacity and enqueued the task.
    * ``gc_target`` — the effective dual-target (task's own, else the section default).
    * ``reason``    — why routing failed, when ``routed`` is False.
    """

    routed: bool
    section: str | None = None
    ghost_id: str | None = None
    queued: bool = False
    gc_target: GcTarget | None = None
    reason: str | None = None


def _task_target_section(task: Task) -> str | None:
    """The explicit ``task.target.section`` when the task carries one (shared ``Task``
    stores ``target`` as a free dict; ``TaskSpec.target.section`` lands here)."""
    section = task.target.get("section")
    return section if isinstance(section, str) else None


def _task_gc_target(task: Task) -> GcTarget | None:
    """The task's own ``target.gc_target`` when it declares one, else ``None``."""
    gc = task.target.get("gc_target")
    return gc if gc in ("cloud", "selfhost") else None


def _effective_gc_target(task: Task, section: Section) -> GcTarget | None:
    """The task's own gc_target when set, else the section's default."""
    return _task_gc_target(task) or section.gc_target


def _candidate_order(
    sections: Sequence[Section], from_section: Section | None
) -> list[Section]:
    """Order candidate sections: a spawning section's ``routes_to`` targets first (in
    that order), then the remaining sections in their declared order."""
    if from_section is None or not from_section.routes_to:
        return list(sections)
    by_id = {s.id: s for s in sections}
    ordered: list[Section] = []
    seen: set[str] = set()
    for sid in from_section.routes_to:
        s = by_id.get(sid)
        if s is not None and s.id not in seen:
            ordered.append(s)
            seen.add(s.id)
    for s in sections:
        if s.id not in seen:
            ordered.append(s)
            seen.add(s.id)
    return ordered


def _assign_into(task: Task, section: Section) -> RouteResult:
    """Assign ``task`` into ``section`` (a free ghost or the sub-queue) and describe it."""
    gid = section.assign(task)
    return RouteResult(
        routed=True,
        section=section.id,
        ghost_id=gid,
        queued=gid is None,
        gc_target=_effective_gc_target(task, section),
    )


def route_task(
    task: Task,
    sections: Sequence[Section],
    from_section: Section | None = None,
) -> RouteResult:
    """Route ``task`` to a section BY ROLE (``accepts``/``routes_to``) or to its explicit
    ``target.section``; capacity-aware (assign a free ghost or enqueue). Returns an
    explicit :class:`RouteResult` — an unroutable kind is ``routed=False`` with a reason.
    """
    by_id = {s.id: s for s in sections}

    # 1. Explicit target.section — must exist AND accept the kind.
    explicit = _task_target_section(task)
    if explicit is not None:
        target = by_id.get(explicit)
        if target is None:
            return RouteResult(routed=False, reason=f"target section {explicit!r} not found")
        if not target.accepts_kind(task.kind):
            return RouteResult(
                routed=False,
                reason=f"section {explicit!r} does not accept kind {task.kind!r}",
            )
        return _assign_into(task, target)

    # 2. By role: the first section whose accepts includes the kind, honoring the
    #    spawning section's routes_to preference order.
    for section in _candidate_order(sections, from_section):
        if section.accepts_kind(task.kind):
            return _assign_into(task, section)

    return RouteResult(routed=False, reason=f"no section accepts kind {task.kind!r}")


def fan_out_mission(
    mission: Mission,
    tasks: Iterable[Task],
    sections: Sequence[Section],
    from_section: Section | None = None,
) -> list[RouteResult]:
    """Fan a mission's initial ``tasks`` out to sections BY ROLE.

    Enters the mission at its entry section(s) by routing each initial task through
    :func:`route_task` over the SHARED section runtimes (so capacity/queueing accumulate
    across the batch). Returns the per-task :class:`RouteResult` list. The orchestrator
    + task-manager reuse :func:`route_task` as the per-event callback
    for each subsequent ``task.spawned`` / ``task.assign``.
    """
    return [route_task(task, sections, from_section=from_section) for task in tasks]
