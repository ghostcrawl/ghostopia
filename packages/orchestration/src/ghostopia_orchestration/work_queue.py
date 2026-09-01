"""The bounded, backoff-aware ``WorkQueue`` — ghostopia's EXECUTION ENGINE (STAGE 6).

GhostCrawl is proxy-exit-count-bound and
governor-gated, so a mission that fans into hundreds of tasks MUST NOT be dispatched at
once. The :class:`WorkQueue` is the SINGLE choke point every task flows through:

* **bounded** — at most ``max_concurrent`` dispatches are in flight at any instant
  (an :class:`asyncio.Semaphore`), and each task is routed to a section BY ROLE via the
  :func:`~ghostopia_sections.route_task` so a section never exceeds its own
  ``capacity`` (a full section back-pressures instead of over-assigning);
* **backoff-aware** — a retryable failure (``rate_limited`` / ``pool_exhausted`` /
  retryable ``5xx``) is re-enqueued only AFTER :func:`compute_backoff` seconds (honouring
  the SDK ``retry_after`` floor), so the next attempt never spin-hammers the API; a
  non-retryable failure fails the task (→ ghost ERROR) and a per-task attempt cap stops an
  infinite retry;
* **fan-out** — a ``task.spawned`` (e.g. a scout discovering an extract url) is re-routed
  to the accepting section through the same :func:`route_task`.

The queue exposes a clean ``enqueue`` / ``run`` / ``outcomes`` lifecycle surface — the seam
the Task/mission management API (``task.assign`` → ``enqueue``; ``task.retarget`` /
``cancel``) composes over. NOTHING bypasses it (governor safety).

The queue is transport-agnostic: it drives an injected ``dispatch`` coroutine (which runs
the task on a pool ghost against a real GhostCrawl session in production, or a fake in
tests) and reads the injected clock via ``sleep`` — so the whole engine is provable
network-free with a mock clock + fake sections.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from ghostopia_ghostcrawl_provider import MappedError, map_sdk_error
from ghostopia_sections import RouteResult, Section, route_task
from ghostopia_shared.types import Task

from ghostopia_orchestration.backoff import compute_backoff

__all__ = ["DispatchResult", "TaskOutcome", "WorkQueue"]

#: The base delay before re-trying to route a task a full section back-pressured (capacity).
_CAPACITY_BACKPRESSURE_S = 0.05
#: The ceiling the exponential capacity back-pressure delay grows toward (seconds).
_CAPACITY_BACKPRESSURE_CEIL_S = 2.0


@dataclass
class DispatchResult:
    """The result of one dispatch attempt the injected runner returns.

    * ``ok`` — True when the task's real session completed successfully.
    * ``spawned`` — child tasks the run discovered (e.g. a scout's ``task.spawned`` urls),
      re-routed to their accepting section by the queue.
    * ``error`` — a :class:`MappedError` when the run failed (drives retry vs fail). A
      dispatch MAY instead raise; the queue maps a raw SDK error itself.
    """

    ok: bool = True
    spawned: list[Task] = field(default_factory=list)
    error: MappedError | None = None


@dataclass
class TaskOutcome:
    """The terminal record for one task the queue produced (for the orchestrator/HUD)."""

    task_id: str
    kind: str
    status: str  # "completed" | "failed"
    attempts: int
    section: str | None = None
    error_code: str | None = None
    reason: str | None = None


#: The runner the queue drives: given a task + its route (which section/ghost it landed on),
#: run the real work and return a :class:`DispatchResult` (or raise an SDK error).
Dispatch = Callable[[Task, RouteResult], Awaitable[DispatchResult]]


class WorkQueue:
    """A bounded, backoff-aware async work queue — the single dispatch choke point."""

    def __init__(
        self,
        sections: Sequence[Section],
        dispatch: Dispatch,
        *,
        max_concurrent: int = 5,
        max_attempts: int = 3,
        max_requeue_seconds: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        backoff: Callable[[int, int | float | None], float] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._sections = list(sections)
        self._by_id = {s.id: s for s in self._sections}
        self._dispatch = dispatch
        self._max_concurrent = max_concurrent
        self._max_attempts = max_attempts
        #: how long a task may sit in capacity back-pressure before it is failed. A
        #: real-time budget (not a cycle count) is the only reliable discriminator between a
        #: task legitimately waiting for a busy slot to free and a permanently-stuck task —
        #: cycle count decouples from time under a mocked clock.
        self._max_requeue_seconds = max_requeue_seconds
        self._sema = asyncio.Semaphore(max_concurrent)
        self._sleep = sleep or asyncio.sleep
        self._backoff = backoff or compute_backoff
        #: the real-time clock the requeue budget measures against (injectable for tests so a
        #: saturated-section case is provable without waiting the wall-clock budget).
        self._now = now or time.monotonic
        self._attempts: dict[str, int] = {}
        #: per-task count of capacity back-pressure re-routes (drives the exponential delay).
        self._requeues: dict[str, int] = {}
        #: the wall-clock deadline after which a still-queued task is failed.
        self._requeue_deadline: dict[str, float] = {}
        self._outcomes: list[TaskOutcome] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._inflight = 0
        self._peak_inflight = 0
        #: every delay the queue slept before a retry (observability + the no-hammer proof).
        self._backoff_delays: list[float] = []

    # -- introspection ----------------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def outcomes(self) -> list[TaskOutcome]:
        """The terminal outcome per settled task."""
        return list(self._outcomes)

    @property
    def peak_inflight(self) -> int:
        """The maximum number of concurrent dispatches observed (never > max_concurrent)."""
        return self._peak_inflight

    @property
    def backoff_delays(self) -> list[float]:
        """Every retry delay slept (each ``>= retry_after`` — the no-hammering proof)."""
        return list(self._backoff_delays)

    # -- lifecycle surface (the management API composes over) -------------------

    def enqueue(
        self, task: Task, *, from_section: Section | None = None, delay: float = 0.0
    ) -> None:
        """Enqueue ``task`` for routing + dispatch (optionally after ``delay`` seconds).

        This is the ONE entry point — ``task.assign`` / a mission fan-out / a spawned child
        / a retry all land here. Nothing dispatches without passing through the bounded run.
        """
        fut = asyncio.ensure_future(self._process(task, from_section, delay))
        self._tasks.add(fut)

    def enqueue_all(
        self, tasks: Sequence[Task], *, from_section: Section | None = None
    ) -> None:
        """Enqueue a whole mission fan-out (each task routed independently by role)."""
        for task in tasks:
            self.enqueue(task, from_section=from_section)

    async def run(self) -> list[TaskOutcome]:
        """Drive every enqueued task (incl. retries + spawned children) to a terminal state.

        Returns when the queue has fully drained — all dispatches settled, all retries
        exhausted-or-succeeded, all spawned children routed + run. Returns the outcomes.
        """
        try:
            while self._tasks:
                # Wake as soon as ANY dispatch settles; drop the finished ones and re-check.
                # Retries + spawned children enqueued mid-run land in ``self._tasks`` and are
                # awaited on the next pass — so the loop drains the whole fan-out (incl. delayed
                # retries) without a fragile done-callback race.
                done, _pending = await asyncio.wait(
                    set(self._tasks), return_when=asyncio.FIRST_COMPLETED
                )
                self._tasks.difference_update(done)
        except asyncio.CancelledError:
            # ``asyncio.wait`` does NOT cancel its child futures when the awaiting task
            # is cancelled — so a cancelled ``run()`` would ORPHAN its in-flight ``_process``
            # tasks, which keep spawning ghosts (e.g. the workforce relay's ``stage-*`` ghosts)
            # AFTER a teardown believed the queue stopped. Cancel + await every in-flight task
            # so no dispatch survives cancellation, then propagate. This is what makes
            # ``WorkforceRelay.stop()`` a real, immediate stop with no late spawns.
            pending = list(self._tasks)
            self._tasks.clear()
            for fut in pending:
                fut.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        return self.outcomes

    # -- the bounded, routed, backoff-aware run per task ------------------------------

    async def _process(
        self, task: Task, from_section: Section | None, delay: float
    ) -> None:
        if delay > 0:
            await self._sleep(delay)
        async with self._sema:  # global concurrency cap
            route = route_task(task, self._sections, from_section=from_section)
            if not route.routed:
                self._record_failed(task, route=route, reason=route.reason)
                return
            section = self._by_id.get(route.section) if route.section else None

            if route.queued:
                # The section is at capacity / has no free roster ghost: it accepted the
                # task into its own sub-queue. Pull it back out (avoid double-accounting) and
                # re-enqueue with a back-pressure delay so we retry routing once a slot frees —
                # NEVER over-assign past section capacity.
                if section is not None and task in section.queue:
                    section.queue.remove(task)
                # Bound the capacity re-route by REAL TIME. Without a bound a permanently
                # -saturated section (stuck roster ghosts / misconfigured capacity) re-enqueues
                # the same task forever — ``self._tasks`` never empties, so ``run()`` (and a
                # workforce relay's ``queue.run()``) hangs indefinitely on a hot re-enqueue loop.
                # A task gets a wall-clock budget on its first back-pressure; each re-route grows
                # the delay exponentially toward a ceiling; once the budget elapses the task is
                # FAILED so the queue always drains. A task legitimately waiting for a busy slot
                # to free routes well inside the budget and is unaffected.
                now = self._now()
                deadline = self._requeue_deadline.get(task.id)
                if deadline is None:
                    deadline = now + self._max_requeue_seconds
                    self._requeue_deadline[task.id] = deadline
                if now >= deadline:
                    self._record_failed(
                        task, route=route, reason="section_capacity_exhausted"
                    )
                    return
                n = self._requeues.get(task.id, 0) + 1
                self._requeues[task.id] = n
                delay = min(
                    _CAPACITY_BACKPRESSURE_S * (2 ** (n - 1)),
                    _CAPACITY_BACKPRESSURE_CEIL_S,
                )
                self.enqueue(task, from_section=from_section, delay=delay)
                return

            self._inflight += 1
            self._peak_inflight = max(self._peak_inflight, self._inflight)
            try:
                try:
                    result = await self._dispatch(task, route)
                except Exception as exc:  # a raw SDK raise the dispatch did not normalize
                    result = DispatchResult(ok=False, error=map_sdk_error(exc))
            finally:
                self._inflight -= 1
                # free the section slot the route reserved (drains nothing extra — the
                # section sub-queue is kept empty by the back-pressure branch above).
                if section is not None and route.ghost_id is not None:
                    section.release(route.ghost_id)

            self._settle(task, route, section, result, from_section)

    def _settle(
        self,
        task: Task,
        route: RouteResult,
        section: Section | None,
        result: DispatchResult,
        from_section: Section | None,
    ) -> None:
        prior = self._attempts.get(task.id, 0)
        if result.ok:
            self._outcomes.append(
                TaskOutcome(
                    task_id=task.id,
                    kind=task.kind,
                    status="completed",
                    attempts=prior + 1,
                    section=route.section,
                )
            )
            # fan spawned children (e.g. scout -> extract) out to their accepting section.
            for child in result.spawned:
                self.enqueue(child, from_section=section)
            return

        attempts = prior + 1
        self._attempts[task.id] = attempts
        err = result.error
        if err is not None and err.retryable and attempts < self._max_attempts:
            wait = self._backoff(attempts, err.retry_after)
            self._backoff_delays.append(wait)
            self.enqueue(task, from_section=from_section, delay=wait)
            return

        self._record_failed(
            task, route=route, error_code=err.code if err is not None else None
        )

    def _record_failed(
        self,
        task: Task,
        *,
        route: RouteResult | None = None,
        error_code: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._outcomes.append(
            TaskOutcome(
                task_id=task.id,
                kind=task.kind,
                status="failed",
                attempts=self._attempts.get(task.id, 0),
                section=route.section if route is not None else None,
                error_code=error_code,
                reason=reason,
            )
        )
