"""``SimEventSource`` — the scripted stage-1/2 producer of the normalized event stream.

An :class:`~ghostopia_core.event_source.EventSource` that drives REAL behaviors over
a per-ghost :class:`~ghostopia_browser_provider.FakeBrowserProvider` (zero GhostCrawl, zero
SDK) and emits the normalized ``ghost.*`` / ``browser.*`` / ``task.*`` / ``result.*`` stream
the :class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver` reacts to. It provides the
LIFECYCLE bracket events the coarse FSM needs (``task.assigned`` → ``ghost.walking`` →
``ghost.arrived`` … ``ghost.returning_home`` → ``ghost.arrived``) around the WORK-phase events
the mounted behavior itself emits through ``ctx.emit`` (``browser.session_opened`` /
``browser.navigate`` / ``result.scraped`` / ``task.completed``). The behavior does the real
work (navigate/read/extract over the fake browser); the source only simulates the passage of
time (walk completion / arrival) so the transitions are VISIBLE.

Dual control: a TASKED ghost runs a work behavior
(``navigate_and_extract`` / ``agent``) through the full receive→walk→work→finish→return loop;
an IDLE ghost runs ``idle_wander`` so the graveyard is never dead between tasks. Both are
mounted BY NAME via :func:`wire_world` through the registry — the source branches on neither.

Timing is tick-based + injectable so tests run instantly (``tick_delay_s=0``) while the live
server paces it in real time for the operator's eyeball.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any

# Importing the builtin package runs the auto-discovery loader so the four example behaviors
# (navigate_and_extract / idle_wander / scout_urls / verify) + the ``agent`` adapter are
# registered by name before ``wire_world`` resolves them.
import ghostopia_behaviors.builtin  # noqa: F401
from ghostopia_behaviors.behavior import Behavior, BehaviorContext
from ghostopia_browser_provider import FakeBrowserProvider
from ghostopia_ghost_runtime.ghost_handle import _GhostHandle
from ghostopia_shared import Envelope, SectionRef, Task
from ghostopia_shared.envelope import serialize_envelope

from ghostopia_core.event_source import EventHandler


@dataclass(frozen=True)
class SimGhost:
    """One ghost the sim animates: its id, the registry name of the behavior it runs, and
    whether it is TASKED (full work loop) or ambient IDLE. A tasked ghost carries a
    :class:`Task`; an idle ghost carries a :class:`SectionRef` (bounds) to wander within."""

    ghost_id: str
    behavior: str
    tasked: bool
    task: Task | None = None
    section: SectionRef | None = None


@dataclass
class _Actor:
    """A mounted behavior + its context (bound by ``wire_world``)."""

    behavior: Behavior
    ctx: BehaviorContext


class SimEventSource:
    """Scripted :class:`EventSource`: ticks real behaviors + emits the lifecycle brackets."""

    def __init__(
        self,
        ghosts: list[SimGhost],
        *,
        tick_ms: float = 200.0,
        tick_delay_s: float = 0.0,
        walk_ticks: int = 4,
        work_ticks_max: int = 600,
        idle_ticks: int = 40,
        engine: str = "chromium",
    ) -> None:
        self.ghosts = list(ghosts)
        self._tick_ms = tick_ms
        self._tick_delay_s = tick_delay_s
        self._walk_ticks = walk_ticks
        self._work_ticks_max = work_ticks_max
        self._idle_ticks = idle_ticks
        self._subs: list[EventHandler] = []
        self._actors: dict[str, _Actor] = {}
        self._providers: dict[str, FakeBrowserProvider] = {
            g.ghost_id: FakeBrowserProvider(engine=engine) for g in self.ghosts
        }
        self._running = False

    # -- EventSource Protocol --------------------------------------------------

    def subscribe(self, handler: EventHandler) -> None:
        """Register a subscriber of the produced envelope stream (e.g. ``driver.dispatch``)."""
        self._subs.append(handler)

    async def emit(self, envelope: Envelope) -> None:
        """Fan an envelope to every subscriber in registration order (awaiting async ones).

        This is ALSO the ``ctx.emit`` the mounted behaviors publish through, so behavior
        events re-enter the exact same stream the driver consumes — one path for scripted
        lifecycle events and behavior work events alike.
        """
        for handler in list(self._subs):
            result = handler(envelope)
            if inspect.isawaitable(result):
                await result

    async def start(self) -> None:
        """Run the simulation: drive every ghost's scripted loop concurrently to completion."""
        if self._running:
            return
        self._running = True
        await asyncio.gather(*(self._run_ghost(g) for g in self.ghosts))

    async def stop(self) -> None:
        """Halt production; in-flight ghost loops observe ``_running`` and unwind."""
        self._running = False

    # -- composition hook ------------------------------------------------------

    def provider_for(self, ghost_id: str) -> FakeBrowserProvider:
        """The per-ghost ``FakeBrowserProvider`` (``wire_world`` puts it on ``ctx.browser``)."""
        return self._providers[ghost_id]

    def mount(self, ghost_id: str, behavior: Behavior, ctx: BehaviorContext) -> None:
        """Bind the behavior + its context for ``ghost_id`` (called by ``wire_world``)."""
        self._actors[ghost_id] = _Actor(behavior=behavior, ctx=ctx)

    # -- scripted timelines ----------------------------------------------------

    async def _run_ghost(self, g: SimGhost) -> None:
        actor = self._actors.get(g.ghost_id)
        if actor is None:
            return  # not mounted (wire_world builds every ghost's context) — nothing to run.
        if g.tasked:
            await self._run_tasked(g, actor)
        else:
            await self._run_idle(g, actor)

    async def _run_tasked(self, g: SimGhost, actor: _Actor) -> None:
        """A full receive → walk → work → finish → return loop via a REAL work behavior."""
        gid = g.ghost_id
        task_id = g.task.id if g.task is not None else None

        # 1. task picked up (IDLE -> RECEIVING_TASK).
        await self.emit(self._ev("task.assigned", gid, {"task_id": task_id, "ghost_id": gid}))

        # 2. the behavior sets up (walks toward its workstation); announce the walk
        #    (RECEIVING_TASK -> WALKING).
        await actor.behavior.on_start(actor.ctx)
        await self.emit(self._ev("ghost.walking", gid, {}))

        # 3. simulate the walk completing, then arrive AT the workstation
        #    (WALKING -> AT_WORKSTATION). Marking the shared handle lets the behavior's
        #    ``on_tick`` see ``at_workstation()`` and begin the work phase.
        await self._pace(self._walk_ticks)
        self._arrive(actor.ctx, at_workstation=True)
        await self.emit(self._ev("ghost.arrived", gid, {"where": "workstation"}))

        # 4. tick the REAL behavior to completion — it emits the work-phase events
        #    (browser.session_opened / browser.navigate / result.scraped / task.completed)
        #    through ``ctx.emit`` (== ``self.emit``), driving OPENING_BROWSER..COMPLETED.
        for _ in range(self._work_ticks_max):
            if not self._running or self._behavior_done(actor.behavior):
                break
            await actor.behavior.on_tick(actor.ctx, self._tick_ms)
            # The DONE->DELIVERING beat: the behavior walks its finished result INTO its
            # department ("deliver" walk) and then waits on ``is_idle()``. Pace + land that
            # mid-work walk so the arrival clock resolves it (mode != workstation -> idle),
            # letting the behavior finish (celebrate + walk home + task.completed).
            handle = actor.ctx.ghost
            if isinstance(handle, _GhostHandle) and handle.is_walking():
                if handle.last_walk_mode() == "deliver":
                    await self.emit(self._ev("ghost.delivering", gid, {}))
                await self._pace(self._walk_ticks)
                self._arrive(actor.ctx, at_workstation=False)
                await self.emit(
                    self._ev("ghost.arrived", gid, {"where": handle.last_walk_mode()})
                )
            await self._pace(1)

        # 5. the behavior finished (it walked itself home on DONE); announce the return
        #    (COMPLETED -> RETURNING_HOME -> IDLE).
        await self.emit(self._ev("ghost.returning_home", gid, {}))
        await self._pace(self._walk_ticks)
        self._arrive(actor.ctx, at_workstation=False)
        await self.emit(self._ev("ghost.arrived", gid, {"where": "home"}))

    async def _run_idle(self, g: SimGhost, actor: _Actor) -> None:
        """Ambient IdleWander: the ghost drifts within its section so the yard feels alive."""
        gid = g.ghost_id
        await actor.behavior.on_start(actor.ctx)
        for _ in range(self._idle_ticks):
            if not self._running:
                break
            await actor.behavior.on_tick(actor.ctx, self._tick_ms)
            # If the behavior started a wander, simulate its completion so the ghost returns
            # to idle and can wander again (IDLE self-loops on ``ghost.wander``).
            handle = actor.ctx.ghost
            if isinstance(handle, _GhostHandle) and handle.is_walking():
                await self._pace(self._walk_ticks)
                self._arrive(actor.ctx, at_workstation=False)
                await self.emit(self._ev("ghost.arrived", gid, {"where": "wander"}))
            await self._pace(1)

    # -- helpers ---------------------------------------------------------------

    def _ev(self, type: str, ghost_id: str, payload: dict[str, Any]) -> Envelope:
        return serialize_envelope(
            type=type, ts=time.time(), payload=payload, ghost_id=ghost_id
        )

    def _arrive(self, ctx: BehaviorContext, *, at_workstation: bool) -> None:
        """Mark the shared concrete handle as having completed its walk (server-side clock)."""
        handle = ctx.ghost
        if isinstance(handle, _GhostHandle):
            handle.arrive(at_workstation=at_workstation)

    @staticmethod
    def _behavior_done(behavior: Behavior) -> bool:
        done = getattr(behavior, "is_done", False)
        return bool(done)

    async def _pace(self, ticks: int) -> None:
        """Yield to the loop ``ticks`` times, sleeping ``tick_delay_s`` for visible pacing."""
        for _ in range(max(0, ticks)):
            if self._tick_delay_s > 0.0:
                await asyncio.sleep(self._tick_delay_s)
            else:
                await asyncio.sleep(0)
