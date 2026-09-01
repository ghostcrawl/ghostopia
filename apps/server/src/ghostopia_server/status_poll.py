"""Lightweight per-ghost status poll — the frame-FREE cadence for the roster HUD.

Every ghost's roster row must stay fresh, but only ONE
ghost — the SELECTED one — ever gets the expensive ``recordings.visual`` frame stream (that
lives in :mod:`frame_fanout`). This poll closes the gap: on a modest ``asyncio`` cadence it
broadcasts each NON-selected ghost's lightweight status (state / current task / behavior name
/ progress / record count) as a ``ghost.status_changed`` envelope. It NEVER opens a frame
stream for a roster ghost — that would fan out N concurrent recordings and hammer the
proxy/governor concurrency budget (a known anti-pattern).

The SELECTED ghost is skipped here on purpose: it already receives rich ``browser.frame`` +
``browser.status`` from :meth:`FrameFanout.select_ghost_frames`, so re-broadcasting its coarse
status would be redundant. Every other ghost gets status ONLY.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ghostopia_ghost_runtime.surface_vocab import sanitize_code
from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope

from .ghost_pool import GhostPool

#: An async fan-out sink (``WsGateway.broadcast`` in production; a collector in tests).
Broadcast = Callable[[Envelope], Awaitable[None]]

_STATUS_TYPE = "ghost.status_changed"
_DIAGNOSTICS_TYPE = "diagnostics.system"


def start_status_poll(
    pool: GhostPool,
    broadcast: Broadcast,
    *,
    fanout: Any | None = None,
    interval: float = 1.0,
) -> asyncio.Task[None]:
    """Start the frame-free status cadence; returns the poll ``asyncio.Task``.

    ``pool`` is the :class:`GhostPool` whose :meth:`~GhostPool.snapshot` gives each ghost's
    lightweight status; ``broadcast`` is the authed WS fan-out. When a ``fanout``
    (:class:`~ghostopia_server.frame_fanout.FrameFanout`) is supplied, the ghost it is
    currently streaming frames for (``fanout.selected_ghost_id``) is SKIPPED — it already
    gets rich frames + status via ``select_ghost_frames`` — so roster ghosts get status only
    and the single-active-frame-stream invariant is preserved. Cancel the returned task to
    stop the poll (server shutdown / Live toggle-off).
    """

    async def _loop() -> None:
        while True:
            await poll_once(pool, broadcast, fanout=fanout)
            await asyncio.sleep(interval)

    return asyncio.ensure_future(_loop())


async def poll_once(
    pool: GhostPool,
    broadcast: Broadcast,
    *,
    fanout: Any | None = None,
) -> None:
    """Broadcast ONE round of ``ghost.status_changed`` for every non-selected ghost.

    Kept as a separate free function so a test can drive a single deterministic round
    without a running loop (mirroring the ``select_ghost_frames`` free-function shape)."""
    selected = getattr(fanout, "selected_ghost_id", None) if fanout is not None else None
    snapshot = pool.snapshot()
    for status in snapshot:
        if status.ghost_id == selected:
            # the selected ghost streams rich frames + status via the frame fan-out; do NOT
            # also open (or duplicate) status for it here — roster ghosts get status ONLY.
            continue
        payload: dict[str, Any] = {
            "name": status.name,
            "section": status.section_id,
            "behavior": status.behavior_name,
            "state": status.state,
            "task": status.task,
            "current_url": status.current_url,
            "progress": status.progress,
            "records": status.record_count,
        }
        # liveness: attach the operator-attention flag ONLY when the server actually
        # has it — an unknown metric is OMITTED, never fabricated (REAL-NOT-MOCK).
        if status.attention is not None:
            payload["attention"] = status.attention.model_dump()
        await broadcast(
            serialize_envelope(
                type=_STATUS_TYPE,
                ts=time.time(),
                ghost_id=status.ghost_id,
                payload=payload,
            )
        )

    # Diagnostics: a frame-free SYSTEM health snapshot from REAL pool state — the
    # concurrency governor (the pool's hard semaphore cap) headroom + per-section occupancy —
    # so the DiagnosticsPanel renders only real values (unknown metrics are simply omitted).
    # Only a full GhostPool exposes these; a minimal pool double omits the snapshot entirely.
    if hasattr(pool, "active_count") and hasattr(pool, "max_concurrent"):
        active = pool.active_count
        pool_max = pool.max_concurrent
        by_section = pool.ghosts_by_section() if hasattr(pool, "ghosts_by_section") else {}
        # Back-pressure visibility. The concurrency cap is the tier/self-host
        # entitlement; when demand exceeds it, tasks DEFER in each section's queue
        # (WorkQueue back-pressure) instead of erroring. Surface the queue depth + saturation
        # alongside the cap so the operator SEES honest queuing — with the curated, surface-safe
        # "Waiting for a free lantern…" notice (never a raw internal state string).
        sections = getattr(pool, "sections", []) or []
        queue_depth = sum(len(getattr(s, "queue", []) or []) for s in sections)
        saturated = active >= pool_max
        payload: dict[str, Any] = {
            "pool_active": active,
            "pool_max": pool_max,
            "headroom": max(0, pool_max - active),
            "queue_depth": queue_depth,
            "saturated": saturated,
            "ghost_count": len(snapshot),
            "sections": {sid: len(gids) for sid, gids in by_section.items()},
        }
        if saturated or queue_depth > 0:
            # curated copy for the DiagnosticsPanel back-pressure banner (reused verbatim).
            payload["notice"] = sanitize_code("pool_exhausted")
        await broadcast(
            serialize_envelope(
                type=_DIAGNOSTICS_TYPE,
                ts=time.time(),
                payload=payload,
            )
        )


async def stop_status_poll(task: asyncio.Task[None]) -> None:
    """Cancel + await a status-poll task (idempotent; swallows the cancellation)."""
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


__all__ = ["poll_once", "start_status_poll", "stop_status_poll"]
