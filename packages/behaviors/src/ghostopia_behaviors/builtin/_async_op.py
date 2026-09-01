"""A single in-flight awaited browser op, started once and polled across ticks.

The behavior executor watchdogs every ``on_tick``: a tick that overruns its deadline is
CANCELLED and DETACHED (``in_process_executor._drive_tick``). A live GhostCrawl op — opening a
session, navigating, scraping — legitimately takes longer than one tick, so awaiting it INLINE
inside ``on_tick`` would be cancelled mid-flight EVERY tick and the step would never advance
(the ghost stalls forever, holds its pool slot, and no ``result.scraped`` is ever recorded).

:class:`AsyncOp` breaks that: a behavior starts the op ONCE as a background asyncio task, then
each tick returns immediately after checking :meth:`done`. The op runs to completion in the
background REGARDLESS of the tick deadline (it is a sibling task, not a child of the tick task,
so cancelling an overrunning tick never cancels it), and its result/exception is read EXACTLY
once via :meth:`result` when it finishes — a success then advances the step immediately, an
error takes the behavior's normal (retry / raise) path.

Dependency-free ON PURPOSE (only ``asyncio``): the behaviors package stays decoupled from the
SDK and the executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

__all__ = ["AsyncOp"]


class AsyncOp:
    """One awaited op started once and polled across ticks (survives tick-deadline cancels)."""

    __slots__ = ("_task",)

    def __init__(self, coro: Coroutine[Any, Any, Any]) -> None:
        # ensure_future schedules the coroutine as an INDEPENDENT task on the running loop; it
        # is NOT a child of the on_tick task, so the executor cancelling an overrunning tick
        # leaves this op running to completion.
        self._task: asyncio.Task[Any] = asyncio.ensure_future(coro)

    def done(self) -> bool:
        """True once the op has finished (successfully, with an error, or cancelled)."""
        return self._task.done()

    def result(self) -> Any:
        """The op's value; re-raises its exception. Only call after :meth:`done` is True."""
        return self._task.result()

    def cancel(self) -> None:
        """Best-effort cancel of a still-pending op (called on terminal teardown)."""
        if not self._task.done():
            self._task.cancel()
