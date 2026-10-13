"""AIMD adaptive concurrency for ghostopia's dispatch engine (smart-orchestration).

ghostopia's dispatch cap was a STATIC tier semaphore. This adds an AIMD
(additive-increase / multiplicative-decrease) limiter that self-tunes the EFFECTIVE
concurrency BELOW the tier ceiling using GhostCrawl's honest backpressure — a
``rate_limited`` / ``pool_exhausted`` / ``429`` / ``503`` / ``retry_after`` outcome — as
the ONLY saturation signal. It deliberately does NOT drive on session wall-time: a long
visit is the intended human roam budget, not saturation (the wedge lesson from a
prior adaptive-concurrency controller). The tier ``max_concurrency`` is a HARD ceiling (a paid
entitlement) the limit never exceeds.

Ramp-down drains gracefully: in-flight work is never aborted, and a vacated slot is not
refilled until ``in_flight`` drops to the (lowered) limit. A manual target — the env
``GHOSTOPIA_ORCH_TARGET`` or the authed control route — pins the limit within the ceiling
both ways; ``auto`` hands control back to AIMD.

Both ``set_limit``/``release`` and the controller's ``record_outcome``/``set_target`` are
SYNC-safe (no event-loop lock) so they can be called from the WorkQueue's synchronous
settle path; only ``acquire`` is a coroutine.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque

__all__ = ["AdaptiveConcurrency", "AIMDController"]


class AdaptiveConcurrency:
    """An async concurrency gate with a MUTABLE limit + graceful ramp-down.

    Unlike :class:`asyncio.Semaphore` the limit can change at runtime. Lowering the limit
    never aborts in-flight work — ``acquire`` simply blocks while ``in_flight >= limit``,
    so vacated slots are not refilled until ``in_flight`` reaches the new (lower) target.
    Raising the limit wakes waiting acquirers. FIFO-fair.
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._limit = int(limit)
        self._in_flight = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def set_limit(self, limit: int) -> None:
        """Change the limit (sync-safe). A raise admits waiters; a lower just drains."""
        self._limit = max(1, int(limit))
        self._wake()

    def _wake(self) -> None:
        while self._waiters and self._in_flight < self._limit:
            fut = self._waiters.popleft()
            if not fut.done():
                self._in_flight += 1  # hand the slot to the waiter as we resolve it
                fut.set_result(None)

    async def acquire(self) -> None:
        # Fast path only when a slot is free AND nobody is queued (FIFO fairness).
        if self._in_flight < self._limit and not self._waiters:
            self._in_flight += 1
            return
        fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # We were granted the slot (in_flight already incremented) then cancelled
                # before returning — hand the slot back so it is not leaked.
                self.release()
            else:
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        """Free one slot (sync-safe) and admit the next waiter if room."""
        if self._in_flight > 0:
            self._in_flight -= 1
        self._wake()

    async def __aenter__(self) -> AdaptiveConcurrency:
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.release()


class AIMDController:
    """Drives an :class:`AdaptiveConcurrency` limit by AIMD on honest backpressure.

    * ``record_outcome(pressure=True)`` → multiplicative-decrease (``limit *= factor``,
      floored) — the API asked us to back off.
    * ``record_outcome(pressure=False)`` → additive-increase by 1 after
      ``increase_after`` consecutive healthy outcomes, up to the ceiling — probe toward
      the tier cap while it's clean.
    A manual target (``set_target`` / the env override) supersedes AIMD both ways.
    """

    def __init__(
        self,
        *,
        ceiling: int,
        floor: int = 1,
        start: int | None = None,
        increase_after: int = 3,
        decrease_factor: float = 0.5,
        env_var: str = "GHOSTOPIA_ORCH_TARGET",
    ) -> None:
        self._floor = max(1, int(floor))
        self._ceiling = max(self._floor, int(ceiling))
        start_limit = self._ceiling if start is None else max(self._floor, min(int(start), self._ceiling))
        self._limiter = AdaptiveConcurrency(start_limit)
        self._increase_after = max(1, int(increase_after))
        self._decrease_factor = decrease_factor
        self._healthy_streak = 0
        self._manual: int | None = None  # None == auto (AIMD active)
        self._env_var = env_var

    @property
    def limiter(self) -> AdaptiveConcurrency:
        return self._limiter

    @property
    def target(self) -> int:
        return self._limiter.limit

    @property
    def ceiling(self) -> int:
        return self._ceiling

    def snapshot(self) -> dict[str, object]:
        return {
            "limit": self._limiter.limit,
            "in_flight": self._limiter.in_flight,
            "ceiling": self._ceiling,
            "floor": self._floor,
            "mode": "manual" if self._manual is not None else "auto",
        }

    def set_ceiling(self, ceiling: int) -> None:
        """Update the hard tier ceiling (entitlement change) and re-clamp the live limit."""
        self._ceiling = max(self._floor, int(ceiling))
        self._reclamp()

    def set_target(self, target: object) -> None:
        """Pin a manual target within [floor, ceiling], or ``auto``/``None`` for AIMD."""
        if target is None or (isinstance(target, str) and target.strip().lower() == "auto"):
            self._manual = None
        else:
            self._manual = max(self._floor, min(int(target), self._ceiling))
        self._reclamp()

    def _apply_env(self) -> None:
        raw = os.environ.get(self._env_var, "").strip().lower()
        if not raw:
            return
        if raw == "auto":
            self._manual = None
        else:
            try:
                self._manual = max(self._floor, min(int(raw), self._ceiling))
            except ValueError:
                pass  # ignore a malformed override; stay in the current mode

    def _reclamp(self) -> None:
        if self._manual is not None:
            self._limiter.set_limit(self._manual)
        else:
            self._limiter.set_limit(max(self._floor, min(self._limiter.limit, self._ceiling)))

    def record_outcome(self, *, pressure: bool) -> None:
        """Feed one dispatch outcome; adjust the limit by AIMD (sync-safe)."""
        self._apply_env()
        if self._manual is not None:
            self._limiter.set_limit(self._manual)  # manual pins; AIMD paused
            return
        if pressure:
            self._healthy_streak = 0
            new = max(self._floor, int(self._limiter.limit * self._decrease_factor))
            self._limiter.set_limit(new)
        else:
            self._healthy_streak += 1
            if self._healthy_streak >= self._increase_after and self._limiter.limit < self._ceiling:
                self._healthy_streak = 0
                self._limiter.set_limit(min(self._ceiling, self._limiter.limit + 1))
