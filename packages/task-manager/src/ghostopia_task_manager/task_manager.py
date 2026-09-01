"""The :class:`TaskManager` — server-authoritative Task/mission verbs.

A **Task** is declarative DATA (a validated :class:`~ghostopia_shared.task.TaskSpec` that
NAMES a vetted behavior — the WHAT), distinct from a **Behavior** (the executable HOW).
This is the COMMAND surface: the nine verbs
``create``/``assign``/``run``/``update``/``pause``/``resume``/``retarget``/``cancel``/
``monitor``, each Pydantic-validated and applied to AUTHORITATIVE state
(the injectable :class:`~ghostopia_task_manager.task_store.TaskStore`) — invalid/unknown is
rejected, NEVER silently applied.

It **composes** with the execution engine rather than re-implementing it:

* ``assign``/``run`` hand a task off to the bounded
  :class:`~ghostopia_orchestration.WorkQueue` — the SINGLE dispatch choke point that routes
  each task to a section BY ROLE via :func:`~ghostopia_sections.route_task` (``accepts``/
  ``routes_to``/capacity) and never exceeds ``max_concurrent`` (governor safety);
* ``retarget``/``cancel`` drive the lifecycle-clean ``on_end(reason)`` + release via
  the injected ``cancel_run`` seam (mirrors the management surface's cancel-and-rehome);
* a mission runs only AFTER a wallet/quota check (``me()``/``usage()``), so it can't silently
  exhaust quota;
* status/telemetry stream back over the injected ``emit`` (the WS event bus / SSE monitor).

Two AI-safety properties are load-bearing: a spec's unknown key is rejected
(``TaskSpec`` is ``extra='forbid'``), its ``behavior`` is validated against the LIVE
:class:`~ghostopia_behaviors.BehaviorRegistry`, and its ``params`` against that behavior's
``param_schema`` — a spec is DATA, never ``eval``'d; and ``concurrency`` is capped ≤50.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ghostopia_sections import RouteResult, Section, route_task
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_shared.task import MissionSpec, TaskSpec, TaskTarget
from ghostopia_shared.types import Task
from pydantic import ValidationError

from ghostopia_task_manager.task_store import (
    TERMINAL_TASK_STATES,
    MissionRecord,
    TaskRecord,
    TaskStore,
)

__all__ = [
    "TaskManager",
    "TaskManagerError",
    "TaskRoutingError",
    "TaskStateError",
    "TaskValidationError",
    "preview_route",
]


class TaskManagerError(ValueError):
    """Base for a rejected task/mission verb (never a silent apply)."""


class TaskValidationError(TaskManagerError):
    """A spec failed validation: unknown key, over-cap concurrency, unknown behavior, or
    params that do not match the behavior's ``param_schema``."""


class TaskRoutingError(TaskManagerError):
    """A task could not be routed (unknown/incompatible section, or no section accepts it)."""


class TaskStateError(TaskManagerError):
    """A verb was applied to a task in an incompatible state (e.g. a terminal task)."""


#: A behavior name → routing kind: a TaskSpec has no explicit ``kind`` (the section fan-out
#: routes on ``Task.kind`` via ``accepts``), so the routing kind is taken from an explicit
#: ``params['kind']`` override, else the behavior NAME (a section that consumes a behavior
#: lists it in ``accepts``). One convention, no per-site branch.
def _routing_kind(spec: TaskSpec) -> str:
    kind = spec.params.get("kind")
    return kind if isinstance(kind, str) and kind else spec.behavior


def preview_route(
    task: Task, sections: Sequence[Section], from_section: Section | None = None
) -> str | None:
    """READ-ONLY mirror of :func:`~ghostopia_sections.route_task`'s SELECTION logic.

    Returns the section id ``route_task`` WOULD pick for ``task`` (its explicit
    ``target.section`` if it accepts the kind, else the first role-accepting section honoring
    a spawning section's ``routes_to`` order) — WITHOUT the mutating ``section.assign`` the
    real router performs. The bounded :class:`~ghostopia_orchestration.WorkQueue` performs the
    single real routing at dispatch; this is purely for the record's displayed section + a
    fail-fast routability check. Returns ``None`` when nothing would accept the task.
    """
    by_id = {s.id: s for s in sections}
    explicit = task.target.get("section")
    if isinstance(explicit, str) and explicit:
        target = by_id.get(explicit)
        if target is not None and target.accepts_kind(task.kind):
            return target.id
        return None
    # role order: a spawning section's routes_to first, then declared order.
    ordered: list[Section] = []
    seen: set[str] = set()
    if from_section is not None:
        for sid in from_section.routes_to:
            s = by_id.get(sid)
            if s is not None and s.id not in seen:
                ordered.append(s)
                seen.add(s.id)
    for s in sections:
        if s.id not in seen:
            ordered.append(s)
            seen.add(s.id)
    for section in ordered:
        if section.accepts_kind(task.kind):
            return section.id
    return None


#: An async fan-out sink for task.*/mission.* status envelopes (WS broadcast / SSE / bus).
Emit = Callable[[Any], Awaitable[None]]
#: The wallet/quota probe run before a mission dispatches (``me()``/``usage()``), best-effort.
Quota = Callable[[], Awaitable[dict[str, Any]]]
#: The lifecycle-clean cancel: fire ``on_end(reason)`` + release the in-flight run.
CancelRun = Callable[[str, str], Awaitable[None]]


class TaskManager:
    """Holds the injectable store + composes over the bounded execution engine.

    Injected deps: ``store`` (persistence), ``work_queue`` (the bounded
    execution engine the verbs compose over), ``sections`` (the role fan-out targets +
    :func:`route_task` the queue routes through), ``behaviors`` (the live registry a spec's
    ``behavior``/``params`` are validated against), ``emit`` (status/telemetry fan-out),
    and the optional ``quota`` (pre-mission wallet check) + ``cancel_run`` (on_end+release).
    """

    def __init__(
        self,
        store: TaskStore,
        *,
        work_queue: Any,
        sections: Sequence[Section],
        behaviors: Any,
        emit: Emit | None = None,
        quota: Quota | None = None,
        cancel_run: CancelRun | None = None,
    ) -> None:
        self._store = store
        self._work_queue = work_queue
        self._sections = list(sections)
        self._by_id = {s.id: s for s in self._sections}
        self._behaviors = behaviors
        self._emit = emit
        self._quota = quota
        self._cancel_run = cancel_run
        self._seq = 0

    # -- helpers -----------------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{int(time.time() * 1000)}-{self._seq}"

    def _coerce_task_spec(self, spec: TaskSpec | dict[str, Any]) -> TaskSpec:
        if isinstance(spec, TaskSpec):
            return spec
        try:
            return TaskSpec.model_validate(spec)  # extra='forbid' + concurrency ≤50 cap.
        except ValidationError as err:
            raise TaskValidationError(f"invalid TaskSpec: {err}") from None

    def _coerce_mission_spec(self, spec: MissionSpec | dict[str, Any]) -> MissionSpec:
        if isinstance(spec, MissionSpec):
            return spec
        try:
            return MissionSpec.model_validate(spec)
        except ValidationError as err:
            raise TaskValidationError(f"invalid MissionSpec: {err}") from None

    def _validate_behavior(self, spec: TaskSpec) -> None:
        """The ``behavior`` MUST exist in the LIVE registry, and ``params`` MUST match its
        ``param_schema`` — a non-coder/AI cannot name a behavior that does not exist, nor
        pass params it does not accept."""
        if spec.behavior not in self._behaviors.names():
            raise TaskValidationError(f"unknown behavior {spec.behavior!r}")
        param_schema = self._behaviors.get(spec.behavior).meta.param_schema
        try:
            param_schema.model_validate(spec.params)
        except ValidationError as err:
            raise TaskValidationError(
                f"params invalid for behavior {spec.behavior!r}: {err}"
            ) from None

    def _spec_to_task(
        self, record: TaskRecord, *, section_override: str | None = None
    ) -> Task:
        spec = record.spec
        target: dict[str, Any] = {"gc_target": spec.target.gc_target}
        section = section_override or spec.target.section
        if section:
            target["section"] = section
        if spec.target.profile:
            target["profile"] = spec.target.profile
        # carry the first seed url so the section fan-out + dispatch have a target (parity
        # with the orchestrator's per-url tasks); the full inputs ride ``inputs``.
        if spec.inputs.urls:
            target["url"] = spec.inputs.urls[0]
        params = dict(spec.params)
        params.setdefault("kind", _routing_kind(spec))
        return Task(
            id=record.id,
            kind=_routing_kind(spec),
            mission_id=record.mission_id,
            behavior_hint=spec.behavior,
            target=target,
            params=params,
            inputs=spec.inputs.model_dump(),
        )

    def _section(self, section_id: str | None) -> Section | None:
        return self._by_id.get(section_id) if section_id else None

    def _ensure_active(self, record: TaskRecord) -> None:
        if record.status in TERMINAL_TASK_STATES:
            raise TaskStateError(
                f"task {record.id!r} is {record.status} (terminal); verb rejected"
            )

    async def _emit_event(
        self, msg_type: str, task_id: str | None, payload: dict[str, Any]
    ) -> None:
        if self._emit is None:
            return
        body = {"task_id": task_id, **payload} if task_id else dict(payload)
        await self._emit(serialize_envelope(type=msg_type, ts=time.time(), payload=body))

    # -- verb: create ------------------------------------------------------------------

    async def create(self, spec: TaskSpec | dict[str, Any]) -> str:
        """Validate + persist a task record; return its id. Rejects an unknown key /
        over-cap concurrency (via ``TaskSpec``), an unknown behavior, or params that do not
        match the behavior's ``param_schema`` — NONE is persisted."""
        parsed = self._coerce_task_spec(spec)
        self._validate_behavior(parsed)
        task_id = parsed.id or self._next_id("task")
        record = TaskRecord(
            id=task_id,
            spec=parsed,
            status="created",
            section=parsed.target.section,
        )
        self._store.create_task(record)
        await self._emit_event(
            "task.created",
            task_id,
            {"title": parsed.title, "behavior": parsed.behavior},
        )
        return task_id

    # -- verb: assign ------------------------------------------------------------------

    async def assign(
        self,
        task_id: str,
        *,
        section: str | None = None,
        ghost_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Route the task BY ROLE (or to explicit ghosts) + enqueue it onto the bounded
        WorkQueue. An explicit ``section`` must exist AND accept the task's kind (fail-fast);
        otherwise the first role-accepting section is previewed. The queue performs the single
        real routing (via :func:`route_task`) at dispatch — nothing bypasses it."""
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        task = self._spec_to_task(record, section_override=section)
        from_section = self._section(section)

        resolved: str | None
        if section is not None:
            target = self._by_id.get(section)
            if target is None:
                raise TaskRoutingError(f"unknown section {section!r}")
            if not target.accepts_kind(task.kind):
                raise TaskRoutingError(
                    f"section {section!r} does not accept kind {task.kind!r}"
                )
            resolved = section
        elif ghost_ids:
            resolved = record.section  # explicit ghosts: keep any prior section for display.
        else:
            resolved = preview_route(task, self._sections)
            if resolved is None:
                raise TaskRoutingError(f"no section accepts kind {task.kind!r}")

        # Enqueue onto the bounded execution engine — the queue routes via route_task +
        # honors max_concurrent / capacity back-pressure (governor safety).
        self._work_queue.enqueue(task, from_section=from_section)
        self._store.patch_task(
            task_id, section=resolved, ghost_ids=ghost_ids, status="queued"
        )
        await self._emit_event(
            "task.assigned", task_id, {"section": resolved, "ghost_ids": ghost_ids}
        )
        return {"ok": True, "task_id": task_id, "section": resolved, "status": "queued"}

    # -- verb: run ---------------------------------------------------------------------

    async def run(self, task_id: str) -> dict[str, Any]:
        """Mount + drive the task to a terminal state through the bounded queue.

        Assigns it first if it was never queued, emits ``task.started``, drains the bounded
        queue (``max_concurrent`` respected by the queue's semaphore), then records the
        terminal outcome (``completed``/``failed``) and emits it."""
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        if record.status not in ("queued", "running"):
            await self.assign(task_id)
        self._store.set_task_status(task_id, "running")
        await self._emit_event("task.started", task_id, {"behavior": record.spec.behavior})

        outcomes = await self._work_queue.run()
        return await self._settle_from_outcomes(task_id, outcomes)

    async def _settle_from_outcomes(
        self, task_id: str, outcomes: Sequence[Any]
    ) -> dict[str, Any]:
        outcome = next((o for o in outcomes if getattr(o, "task_id", None) == task_id), None)
        if outcome is None:
            # nothing settled for this id (e.g. a fake queue that only records enqueue) —
            # leave it running; the queue will settle it on its drain.
            return {"ok": True, "task_id": task_id, "status": "running"}
        status = "completed" if outcome.status == "completed" else "failed"
        self._store.patch_task(
            task_id,
            status=status,
            attempts=getattr(outcome, "attempts", 0),
            error_code=getattr(outcome, "error_code", None),
        )
        await self._emit_event(
            f"task.{status}",
            task_id,
            {"attempts": getattr(outcome, "attempts", 0),
             "error_code": getattr(outcome, "error_code", None)},
        )
        return {"ok": True, "task_id": task_id, "status": status}

    # -- verb: update ------------------------------------------------------------------

    async def update(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Patch ``params``/``concurrency``/``target`` on a queued (running-safe) task.

        The patch is merged into the spec and RE-VALIDATED (``extra='forbid'`` + behavior
        ``param_schema``) — a hallucinated field or a params-schema break is rejected and
        nothing changes. A terminal task rejects an update."""
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        merged = record.spec.model_dump()
        for key in ("params", "concurrency", "target", "title", "inputs", "retry",
                    "identities"):
            if key in patch:
                merged[key] = patch[key]
        unknown = set(patch) - {
            "params", "concurrency", "target", "title", "inputs", "retry", "identities",
        }
        if unknown:
            raise TaskValidationError(f"unpatchable field(s): {sorted(unknown)}")
        parsed = self._coerce_task_spec(merged)
        self._validate_behavior(parsed)
        self._store.patch_task(task_id, spec=parsed)
        await self._emit_event("task.updated", task_id, {"patch": sorted(patch)})
        return {"ok": True, "task_id": task_id, "status": record.status}

    # -- verb: pause / resume ----------------------------------------------------------

    async def pause(self, task_id: str) -> dict[str, Any]:
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        self._store.patch_task(task_id, paused=True, status="paused")
        await self._emit_event("task.paused", task_id, {"paused": True})
        return {"ok": True, "task_id": task_id, "paused": True}

    async def resume(self, task_id: str) -> dict[str, Any]:
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        # resume back to queued (it re-enters the bounded queue on the next run).
        self._store.patch_task(task_id, paused=False, status="queued")
        await self._emit_event("task.resumed", task_id, {"paused": False})
        return {"ok": True, "task_id": task_id, "paused": False}

    # -- verb: retarget ----------------------------------------------------------------

    async def retarget(
        self, task_id: str, target: TaskTarget | dict[str, Any]
    ) -> dict[str, Any]:
        """Cancel the in-flight run (``on_end('retargeted')`` + release) and re-enqueue on the
        new ``gc_target``/``section`` (lifecycle-clean, reused)."""
        record = self._store.get_task(task_id)
        self._ensure_active(record)
        new_target = target if isinstance(target, TaskTarget) else TaskTarget.model_validate(
            target
        )
        if self._cancel_run is not None:
            await self._cancel_run(task_id, "retargeted")
        new_spec = record.spec.model_copy(update={"target": new_target})
        self._store.patch_task(
            task_id, spec=new_spec, section=new_target.section, status="retargeted"
        )
        await self._emit_event(
            "task.retargeted",
            task_id,
            {"gc_target": new_target.gc_target, "section": new_target.section},
        )
        # re-home on the new target through the bounded queue.
        return await self.assign(task_id, section=new_target.section)

    # -- verb: cancel ------------------------------------------------------------------

    async def cancel(self, task_id: str) -> dict[str, Any]:
        """Cancel the in-flight run (``on_end('cancelled')`` + release) and mark terminal."""
        record = self._store.get_task(task_id)
        if record.status in TERMINAL_TASK_STATES:
            return {"ok": True, "task_id": task_id, "status": record.status}
        if self._cancel_run is not None:
            await self._cancel_run(task_id, "cancelled")
        self._store.set_task_status(task_id, "cancelled")
        await self._emit_event("task.cancelled", task_id, {"status": "cancelled"})
        return {"ok": True, "task_id": task_id, "status": "cancelled"}

    # -- verb: monitor -----------------------------------------------------------------

    def monitor(self, task_id: str) -> dict[str, Any]:
        """A status SNAPSHOT for a task (the live telemetry stream rides ``emit`` / the SSE
        monitor server-side). Raises ``RecordNotFoundError`` for an unknown id."""
        record = self._store.get_task(task_id)
        return {
            "task_id": record.id,
            "title": record.spec.title,
            "behavior": record.spec.behavior,
            "status": record.status,
            "section": record.section,
            "ghost_ids": record.ghost_ids,
            "attempts": record.attempts,
            "error_code": record.error_code,
            "paused": record.paused,
            "mission_id": record.mission_id,
        }

    # -- mission scope: the same verbs over a named collection of tasks -----------------

    async def create_mission(self, spec: MissionSpec | dict[str, Any]) -> str:
        """Split a mission into N task records (one per member ``TaskSpec``) + persist the
        mission record. Each member spec is validated (behavior + params) — one bad member
        rejects the WHOLE mission (nothing persisted)."""
        parsed = self._coerce_mission_spec(spec)
        for member in parsed.tasks:
            self._validate_behavior(member)
        mission_id = parsed.id or self._next_id("mission")
        task_ids: list[str] = []
        for member in parsed.tasks:
            tid = member.id or self._next_id("task")
            self._store.create_task(
                TaskRecord(id=tid, spec=member, status="created", mission_id=mission_id)
            )
            task_ids.append(tid)
        self._store.create_mission(
            MissionRecord(id=mission_id, spec=parsed, task_ids=task_ids, status="created")
        )
        await self._emit_event(
            "mission.created", None,
            {"mission_id": mission_id, "title": parsed.title, "total": len(task_ids)},
        )
        return mission_id

    async def assign_mission(
        self, mission_id: str, *, section: str | None = None
    ) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        for tid in record.task_ids:
            await self.assign(tid, section=section)
        self._store.set_mission_status(mission_id, "queued")
        await self._emit_event("mission.assigned", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id, "tasks": len(record.task_ids)}

    async def run_mission(self, mission_id: str) -> dict[str, Any]:
        """Wallet/quota-check (``me()``/``usage()``) BEFORE dispatch, enqueue
        every member task, then drive the bounded queue once + settle each member."""
        record = self._store.get_mission(mission_id)
        await self._check_quota(mission_id)
        for tid in record.task_ids:
            tr = self._store.get_task(tid)
            if tr.status not in ("queued", "running"):
                await self.assign(tid)
            self._store.set_task_status(tid, "running")
        await self._emit_event("mission.started", None, {"mission_id": mission_id})
        outcomes = await self._work_queue.run()
        for tid in record.task_ids:
            await self._settle_from_outcomes(tid, outcomes)
        self._store.set_mission_status(mission_id, "completed")
        await self._emit_event("mission.completed", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id, "tasks": len(record.task_ids)}

    async def pause_mission(self, mission_id: str) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        for tid in record.task_ids:
            await self.pause(tid)
        self._store.set_mission_status(mission_id, "paused")
        await self._emit_event("mission.paused", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id}

    async def resume_mission(self, mission_id: str) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        for tid in record.task_ids:
            await self.resume(tid)
        self._store.set_mission_status(mission_id, "queued")
        await self._emit_event("mission.resumed", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id}

    async def retarget_mission(
        self, mission_id: str, target: TaskTarget | dict[str, Any]
    ) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        for tid in record.task_ids:
            await self.retarget(tid, target)
        await self._emit_event("mission.retargeted", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id}

    async def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        for tid in record.task_ids:
            await self.cancel(tid)
        self._store.set_mission_status(mission_id, "cancelled")
        await self._emit_event("mission.cancelled", None, {"mission_id": mission_id})
        return {"ok": True, "mission_id": mission_id}

    def monitor_mission(self, mission_id: str) -> dict[str, Any]:
        record = self._store.get_mission(mission_id)
        tasks = [self.monitor(tid) for tid in record.task_ids]
        done = sum(1 for t in tasks if t["status"] in TERMINAL_TASK_STATES)
        return {
            "mission_id": record.id,
            "title": record.spec.title,
            "status": record.status,
            "total": len(tasks),
            "done": done,
            "tasks": tasks,
        }

    async def _check_quota(self, mission_id: str) -> None:
        """Surface remaining wallet/quota before a mission runs (best-effort, non-fatal)."""
        if self._quota is None:
            return
        try:
            snapshot = await self._quota()
        except Exception:
            return
        await self._emit_event(
            "result.mission_progress", None,
            {"mission_id": mission_id, "quota": snapshot},
        )


# Re-export the real router so the seam is discoverable from this module (the WorkQueue
# performs the mutating route at dispatch; :func:`preview_route` mirrors its selection).
_ROUTE_TASK = route_task
_ROUTE_RESULT = RouteResult
