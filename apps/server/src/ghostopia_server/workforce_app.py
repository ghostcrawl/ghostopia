"""Operator WORKFORCE entrypoint — the ghostly workforce on REAL GhostCrawl.

ghostopia is a workforce of GhostCrawl: every scrape goes THROUGH GhostCrawl. This
entrypoint boots the REAL live app (:func:`create_live_app` with NO mock ``provider_factory`` —
the default is the real :class:`~ghostopia_ghostcrawl_provider.GhostCrawlProvider`). It is a
plug-and-play GhostCrawl plugin: point it at a key + endpoint via ``GHOSTOPIA_GC_TOKEN`` +
``GHOSTOPIA_GC_BASE_URL`` (cloud OR a self-host instance via the SDK ``base_url`` switch) and go.

It composes the real app with the ambient :class:`~ghostopia_server.sim_runtime.SimRuntime`, so
BOTH front-end modes are alive against one server:

* **Simulated** — the toggle sends ``sim.start``; ambient ghosts spawn, drift through their
  home sections, and cycle the drift -> work -> return loop continuously.
* **Live** — submit a mission in the form; ghosts fan out per section and animate one real
  GhostCrawl session each, with the live inspector on the selected ghost.

The registry is built LAZILY, so the app boots fine with NO key set — it rests as a persistent
empty graveyard and, when unconfigured, onboards a submitted mission gracefully ("Connect your
GhostCrawl key and endpoint to summon the workforce") instead of crashing.

This is the REAL app. ``GhostCrawlProvider`` is the ONLY provider in the shipped tree:
there is no mock — every scrape goes THROUGH GhostCrawl. Server unit tests that need a
provider without a live GhostCrawl use a lean in-test double (``conftest.FakeProvider``), never a
shipped surface.

Run it (bound to all interfaces so another device on the network can reach it)::

    GHOSTOPIA_JWT_SECRET=<secret> GHOSTOPIA_DB_PATH=<tmp> \\
    GHOSTOPIA_GC_TOKEN=<key> GHOSTOPIA_GC_BASE_URL=<cloud-or-selfhost> \\
        uv run uvicorn ghostopia_server.workforce_app:create_workforce_app \\
        --factory --host 0.0.0.0 --port 8000

The browser points its WS at this host via ``VITE_GHOSTOPIA_WS_URL`` and authenticates with an
operator token (``VITE_GHOSTOPIA_WS_TOKEN`` or ``?token=``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

from fastapi import FastAPI

from .gc_event_source import create_live_app
from .sim_runtime import SimRuntime

__all__ = ["create_workforce_app"]

#: Grace window (seconds) after the LAST operator disconnects before the workforce is torn
#: down + every live GhostCrawl session released. Small so a closed tab frees the account's
#: concurrency budget promptly, but long enough that a page REFRESH (disconnect→reconnect)
#: never tears down and rebuilds. Env-overridable (``0`` = tear down immediately).
_IDLE_TEARDOWN_GRACE_S = 6.0


def _install_idle_teardown(app: FastAPI, gateway: Any, sim: SimRuntime) -> None:
    """Auto-kill the whole workforce when the LAST operator disconnects.

    The workforce spawns LOOPING department ghosts, each holding ONE live GhostCrawl session; a
    disconnected operator (closed tab / dropped connection) would otherwise leave that fleet
    looping forever, saturating the account's concurrent-live-session cap so nothing else can
    open a session (the 429 storm). This wires the gateway's last-disconnect seam to, after a
    short refresh-tolerant grace, despawn EVERY pool ghost — each despawn aborts its run and
    RELEASES its session — and stop the ambient sim. If a client reconnects during the grace
    the teardown is skipped (re-checked after the sleep), so a refresh never churns the world.
    If a reload takes LONGER than the grace and the world IS erased, the workforce re-materializes
    on the next reconnect via the durable intent + the gateway's first-connect resume seam
    (``create_live_app._resume_workforce``) — not by the client re-sending ``workforce.start``.
    """
    try:
        grace_s = float(os.environ.get("GHOSTOPIA_IDLE_TEARDOWN_GRACE_S", _IDLE_TEARDOWN_GRACE_S))
    except (TypeError, ValueError):
        grace_s = _IDLE_TEARDOWN_GRACE_S
    state: dict[str, asyncio.Task[None] | None] = {"task": None}

    async def _teardown_after_grace() -> None:
        try:
            if grace_s > 0:
                await asyncio.sleep(grace_s)
        except asyncio.CancelledError:
            return
        if gateway.client_count > 0:
            return  # an operator reconnected during the grace window — keep the world alive.
        # Stop the ambient simulated world first (no live sessions, but keep it tidy).
        with contextlib.suppress(Exception):
            await sim.stop()
        # Cancel the background baton relay's sustainer BEFORE despawning, so it cannot
        # respawn stage ghosts we are about to tear down (the intent stays set so the first
        # reconnect restarts it — a fresh relay).
        relay = getattr(app.state, "workforce_relay", None)
        if relay is not None:
            with contextlib.suppress(Exception):
                await relay.stop()
            app.state.workforce_relay = None
        # Despawn every pool ghost → aborts each run + RELEASES its live GhostCrawl session, so a
        # disconnected operator never leaves sessions open against the account's concurrency cap.
        # DRAIN until stable — a relay queue.run() cancelled mid-flight can land a late
        # ``stage-*`` spawn AFTER a one-shot snapshot, leaving a straggler that survives the
        # teardown (a race the extra departments make more likely). Re-snapshotting in a bounded
        # loop guarantees the world is fully erased.
        pool = getattr(app.state, "ghost_pool", None)
        if pool is not None:
            for _ in range(8):
                remaining = list(getattr(pool, "_records", {}).keys())
                if not remaining:
                    break
                for gid in remaining:
                    with contextlib.suppress(Exception):
                        await pool.despawn(gid)
                # yield so any in-flight relay spawn coroutine completes before the re-snapshot.
                await asyncio.sleep(0)

    async def _on_disconnect() -> None:
        # The gateway removed the socket BEFORE calling this, so client_count is authoritative.
        if gateway.client_count > 0:
            return
        prev = state["task"]
        if prev is not None and not prev.done():
            prev.cancel()
        state["task"] = asyncio.ensure_future(_teardown_after_grace())

    gateway.set_on_disconnect(_on_disconnect)
    app.state.idle_teardown_grace_s = grace_s

    @app.on_event("shutdown")
    async def _cancel_pending_teardown() -> None:
        task = state["task"]
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def create_workforce_app() -> FastAPI:
    """Build the operator workforce: the REAL GhostCrawl live app + ambient SimRuntime.

    The provider is the real :class:`GhostCrawlProvider` (``create_live_app`` called with NO
    mock ``provider_factory``), resolved from the server-side key + endpoint. The app rests as a
    PERSISTENT empty graveyard (no ambient caretakers, no destructive auto-clean, no auto-run
    workforce) and boots even with NO key configured (lazy registry).
    """
    # The workforce is the PERSISTENT operator app: it rests as an empty graveyard (no ambient
    # caretakers), survives viewer reconnects (no destructive auto-clean), and does NOT auto-run
    # the workforce — the web app auto-CONNECTS (VITE_GHOSTOPIA_AUTOLIVE) and the operator clicks
    # "Run workforce" to materialize a working wave.
    # NO provider_factory => the real GhostCrawlProvider default. Every scrape goes THROUGH
    # GhostCrawl; there is no mock on this operator-facing surface.
    app = create_live_app()
    # The live app publishes its gateway on app.state; mounting the SimRuntime there registers
    # the sim.start / sim.stop control verbs the Simulated toggle needs.
    gateway = app.state.ws_gateway

    # mutual exclusion: the sim world and the workforce world cannot both run at once.
    # Starting the sim preempts (stops) the workforce — the reverse of ``_start_workforce`` stopping
    # the sim (which reads ``app.state.sim_runtime``). Both directions are best-effort.
    async def _preempt_workforce() -> None:
        stop = getattr(app.state, "stop_workforce", None)
        if stop is not None:
            await stop()

    sim = SimRuntime(gateway, on_before_start=_preempt_workforce)
    sim.install()
    app.state.sim_runtime = sim
    # Clean lifecycle: when the LAST operator disconnects, tear the whole workforce down + release
    # every live GhostCrawl session (after a short refresh-tolerant grace) so a closed tab never
    # leaves looping ghosts saturating the account's concurrency cap.
    _install_idle_teardown(app, gateway, sim)
    # Expose ONLY whether a key is configured (never the key itself) so the client can render an
    # onboarding empty-state ("connect your GhostCrawl key + endpoint"). The banner UI is W7.
    app.state.ghostcrawl_configured = bool(os.environ.get("GHOSTOPIA_GC_TOKEN"))
    return app
