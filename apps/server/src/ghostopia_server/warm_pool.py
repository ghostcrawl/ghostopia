"""``WarmSessionPool`` — pre-opened GhostCrawl browser sessions, so a browser is ALREADY warm.

The operator's live inspector is fast only if a featured ghost's browser session is ready the
instant it needs one. Cold-opening a session on demand (``sessions.create`` → the ghost then
navigates) adds a visible "waking a browser…" beat every cycle. This pool keeps a small set of
chrome sessions OPEN ahead of time, right up to the account's live-session cap, and hands one to
a ghost the moment it opens its browser — turning the cold open into an instant adopt.

Budget is the ONE shared live-session semaphore the :class:`~ghostopia_server.ghost_pool.GhostPool`
already enforces (``me().max_live_sessions``): each warm session HOLDS one semaphore slot, and
:meth:`acquire` TRANSFERS that slot to the caller (which releases it when the ghost is done). The
warmer only tops up while slots are genuinely free (a non-blocking check), so warm sessions never
crowd out a ghost that needs to open its own — the total (warm + in-use) can never exceed the cap.

Freshness: a warm session that has sat idle past ``max_age_s`` is retired (terminated + slot
freed) and replaced, so an adopted session is always recent and unlikely to have been TTL-reaped
by the cloud. If the pool is empty (or disabled), the caller simply cold-opens as before — the
pool is a pure latency optimization with a graceful fallback, never a correctness dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = ["WarmSession", "WarmSessionPool"]

#: Opens one warm session; returns ``(session_id, engine)``.
CreateFn = Callable[[], Awaitable[tuple[str, str]]]
#: Terminates a session by id (frees the cloud lease + live-session slot).
TerminateFn = Callable[[str], Awaitable[None]]
#: Returns the CURRENT live-session semaphore (the pool's cap is set at startup, so the semaphore
#: object is resolved lazily rather than captured before the entitlement cap is applied).
SemaGetter = Callable[[], asyncio.Semaphore]
#: A monotonic clock (``loop.time``) — injected so the warmer's age math is testable/deterministic.
Clock = Callable[[], float]


@dataclass
class WarmSession:
    """One pre-opened session holding exactly one live-session semaphore slot."""

    session_id: str
    engine: str
    created_at: float


class WarmSessionPool:
    """Keeps up to ``target`` warm sessions open, each holding one live-session slot."""

    def __init__(
        self,
        *,
        create: CreateFn,
        terminate: TerminateFn,
        sema: SemaGetter,
        clock: Clock,
        target: int = 1,
        max_age_s: float = 45.0,
        interval_s: float = 1.0,
    ) -> None:
        self._create = create
        self._terminate = terminate
        self._sema = sema
        self._clock = clock
        self._target = max(0, target)
        self._max_age_s = max_age_s
        self._interval_s = interval_s
        self._warm: list[WarmSession] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def warm_count(self) -> int:
        """Warm sessions currently ready to hand out (test/introspection)."""
        return len(self._warm)

    def acquire(self) -> tuple[str, str] | None:
        """Hand out the freshest warm session as ``(session_id, engine)``, or ``None`` if empty.

        The caller INHERITS the semaphore slot the warm session was holding — it must release
        that slot (terminate the session) when done, exactly as if it had acquired it itself.
        ``None`` means "no warm session ready" → the caller cold-opens (acquiring its own slot)."""
        if not self._warm:
            return None
        # freshest first (LIFO) — the least likely to have been TTL-reaped.
        w = self._warm.pop()
        return (w.session_id, w.engine)

    async def start(self) -> None:
        """Launch the background warmer (idempotent)."""
        if self._running or self._target <= 0:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._warm_loop())

    async def stop(self) -> None:
        """Stop warming and terminate every still-warm session (freeing its slot)."""
        self._running = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        warm, self._warm = self._warm, []
        for w in warm:
            await self._retire(w)

    async def _warm_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            await self._retire_stale()
            await self._top_up()

    async def _retire_stale(self) -> None:
        now = self._clock()
        keep: list[WarmSession] = []
        stale: list[WarmSession] = []
        for w in self._warm:
            (stale if now - w.created_at > self._max_age_s else keep).append(w)
        self._warm = keep
        for w in stale:
            await self._retire(w)

    async def _top_up(self) -> None:
        sema = self._sema()
        while len(self._warm) < self._target and not sema.locked():
            # value > 0 → acquire completes immediately without yielding (asyncio is
            # single-threaded, so the locked() check and this acquire are atomic together).
            await sema.acquire()
            try:
                session_id, engine = await self._create()
            except Exception:  # noqa: BLE001 - a failed pre-warm just gives the slot back
                sema.release()
                return
            self._warm.append(WarmSession(session_id, engine, self._clock()))

    async def _retire(self, w: WarmSession) -> None:
        """Terminate a warm session and release the live-session slot it held."""
        try:
            await self._terminate(w.session_id)
        except Exception:  # noqa: BLE001 - best-effort; free the slot regardless
            pass
        finally:
            self._sema().release()
