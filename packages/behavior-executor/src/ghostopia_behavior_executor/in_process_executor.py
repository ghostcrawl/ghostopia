"""v0 :class:`InProcessExecutor` — the in-process, capability-scoped asyncio executor.

This is the honest local-first Layer 2: operator-authored behaviors run
as asyncio tasks in this process, against the capability-scoped ctx. It enforces two limits
so no behavior can wedge the loop:

* a **wall-clock budget** — the whole run (``on_start`` → ticks → ``on_end``) is wrapped in
  an :func:`asyncio.timeout`; a hook that hangs past the budget ends the run ``timed_out``;
* a **tick deadline** — each ``on_tick`` runs as its own task under
  :func:`asyncio.wait(timeout=...)`; a tick that overruns is CANCELLED, FLAGGED, and NOT
  awaited (the loop moves on) so a slow/greedy tick never blocks the executor.

``on_end`` is guaranteed to fire EXACTLY once (via an idempotent guard) with the ctx's
browser session released, on every terminal path. The behavior-facing reason is the shared
``EndReason`` (``timed_out`` maps to ``"failed"``); the caller gets the richer
:class:`RunOutcome` in the returned :class:`RunResult`.

This executor uses no ``eval``/``exec`` and adds no native-isolate dependency — hardening is
the documented subprocess/separate-interpreter swap behind the :class:`Executor` seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ghostopia_behaviors.behavior import Behavior, BehaviorContext
from ghostopia_shared import EndReason

from ghostopia_behavior_executor.executor import RunLimits, RunOutcome, RunResult

__all__ = ["InProcessExecutor"]

# Map the run outcome to the behavior-facing EndReason (which has no timed-out member).
_END_REASON: dict[RunOutcome, EndReason] = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
}


async def _release_session(ctx: BehaviorContext) -> None:
    """Best-effort release of the ctx's browser session — never raises."""
    browser: Any = getattr(ctx, "browser", None)
    release = getattr(browser, "release", None)
    if release is None:
        return
    try:
        await release()
    except Exception:  # release must not mask the run's terminal reason
        pass


class InProcessExecutor:
    """The v0 in-process asyncio executor. Conforms to the :class:`Executor` Protocol."""

    def __init__(self, *, tick_interval_ms: float = 10.0) -> None:
        # The pacing between ticks. Kept small; a test can shrink it for speed.
        self._tick_interval_ms = tick_interval_ms

    async def run(
        self,
        behavior: Behavior,
        ctx: BehaviorContext,
        limits: RunLimits,
        *,
        paused: Callable[[], bool] | None = None,
        abort: asyncio.Event | None = None,
    ) -> RunResult:
        """Run ``behavior`` under ``limits``.

        ``paused`` (optional) is consulted before every ``on_tick``: while it returns True the
        tick is SKIPPED (no provider calls) and the wall-clock budget is FROZEN, so a resumed
        ghost still gets its full working time — a real halt, not a cosmetic flag.
        ``abort`` (optional) is an :class:`asyncio.Event` the loop checks each iteration: once
        set, the run stops cooperatively and ends ``cancelled`` (``on_end('cancelled')`` +
        session release, exactly once). External ``asyncio`` cancellation still works too.
        """
        ticks = 0
        tick_overruns = 0
        ended = False

        async def _end_once(outcome: RunOutcome) -> None:
            nonlocal ended
            if ended:
                return
            ended = True
            try:
                await behavior.on_end(ctx, _END_REASON[outcome])
            finally:
                await _release_session(ctx)

        outcome: RunOutcome = "completed"
        budget_s = limits.wall_clock_ms / 1000.0
        tick_deadline_s = limits.tick_deadline_ms / 1000.0
        tick_interval_s = self._tick_interval_ms / 1000.0
        try:
            loop = asyncio.get_running_loop()
            start = loop.time()
            # on_start is the one hook that can hang the whole run before ticking begins;
            # bound it by the remaining budget. A hang here ends the run ``timed_out``.
            await asyncio.wait_for(behavior.on_start(ctx), timeout=budget_s)
            # Cooperative tick loop: each tick is watchdog'd, so the loop always regains
            # control and breaks cleanly at the budget → ``completed`` (running the full
            # allotted time is normal completion, NOT a timeout).
            while (remaining := budget_s - (loop.time() - start)) > 0:
                # cooperative cancel: a set abort event stops the run cleanly (cancelled).
                if abort is not None and abort.is_set():
                    outcome = "cancelled"
                    break
                # a task behavior that declares itself DONE stops the run immediately (completed)
                # instead of idling out the whole wall-clock budget — so a finished ghost frees its
                # slot + returns home promptly (an AMBIENT behavior has no ``is_done`` and roams the
                # full budget, unchanged). 195: this is what lets a workforce ghost dematerialize.
                if getattr(behavior, "is_done", False):
                    break
                # real pause: skip the tick (NO provider calls) and freeze the budget so the
                # paused interval is not charged against the ghost's working time.
                if paused is not None and paused():
                    nap = min(tick_interval_s, remaining)
                    await asyncio.sleep(nap)
                    start += nap
                    continue
                overran = await self._drive_tick(
                    behavior, ctx, self._tick_interval_ms, min(tick_deadline_s, remaining)
                )  # deadline passed in SECONDS
                ticks += 1
                if overran:
                    tick_overruns += 1
                await asyncio.sleep(min(tick_interval_s, max(0.0, remaining)))
        except TimeoutError:  # asyncio.TimeoutError is an alias of the builtin (3.11+)
            outcome = "timed_out"
        except asyncio.CancelledError:
            # External cancellation: still fire on_end + release, then propagate.
            await _end_once("cancelled")
            raise
        except Exception:
            outcome = "failed"

        await _end_once(outcome)
        return RunResult(outcome=outcome, ticks=ticks, tick_overruns=tick_overruns)

    async def _drive_tick(
        self,
        behavior: Behavior,
        ctx: BehaviorContext,
        dt_ms: float,
        tick_deadline_s: float,
    ) -> bool:
        """Run one ``on_tick`` under its deadline (SECONDS). Returns True if it overran.

        An overrunning tick is cancelled and DETACHED — the loop is never blocked on it, and
        its result is drained by a done-callback so no "exception never retrieved" warning
        leaks. A tick that RAISES is re-raised so the run ends ``failed``.
        """
        task: asyncio.Task[None] = asyncio.ensure_future(behavior.on_tick(ctx, dt_ms))
        done, pending = await asyncio.wait({task}, timeout=max(0.0, tick_deadline_s))
        if task in pending:
            task.cancel()
            task.add_done_callback(_drain)  # detach; never await it
            return True
        exc = task.exception()
        if exc is not None:
            raise exc
        return False


def _drain(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached task's outcome so no warning leaks; swallow cancellation/error."""
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        pass
