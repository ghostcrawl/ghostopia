"""The documented ONE-FILE Behavior contract.

A ``Behavior`` is the decision unit a ghost runs: an original, modular, hot-registrable
module with a documented ``on_start`` / ``on_tick`` / ``on_event`` / ``on_end`` lifecycle
over a :class:`BehaviorContext`. It drives the visible ghost ONLY through the narrow
``GhostHandle`` and reaches GhostCrawl ONLY through the full-primitive ``ctx.browser``
(``BrowserProvider``) — never the SDK, never a secret.

``on_tick`` MUST be NON-BLOCKING: a behavior kicks off at most one awaited op per tick and
reacts to its completion on ``on_event`` (anti-pattern: blocking the tick on a long
GhostCrawl call). The context carries a seeded ``rng`` so behaviors are deterministic under
test, and a ``log`` sink for structured breadcrumbs.

``ctx.emit`` is the raw normalized-``Envelope`` sink (matches ``EventBus.publish`` and the
``AgentProvider`` emit shape exactly, so ``AgentBehavior`` can pass ``ctx.emit`` straight
through). Use the ergonomic :meth:`BehaviorContext.emit_event` to build+publish a
``ghost.*``/``browser.*``/``task.*``/``result.*`` event in one call.
"""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ghostopia_browser_provider import BrowserProvider
from ghostopia_shared import (
    EndReason,
    Envelope,
    GhostEvent,
    GhostHandle,
    SectionRef,
    Task,
    WorldQuery,
    serialize_envelope,
)
from ghostopia_shared.envelope import PROTOCOL_VERSION

__all__ = ["Behavior", "BehaviorContext", "EndReason", "Emit", "PROTOCOL_VERSION"]

# The async normalized-Envelope sink. Wired to ``EventBus.publish`` by the composition
# layer; tests wire it to a list collector. Identical to ``agent_runtime.Emit``
# so ``AgentBehavior`` forwards ``ctx.emit`` to ``AgentProvider.run_task`` unchanged.
Emit = Callable[[Envelope], Awaitable[None]]


def _never_watched() -> bool:  # default: the operator is not watching this ghost
    return False


def _noop_log(_msg: str) -> None:  # default log sink
    return None


@dataclass
class BehaviorContext:
    """Everything a Behavior receives — the capability-scoped seam.

    A behavior gets ONLY these; never ``fs``/``net``/``child_process``/keys/raw SDK. It is
    a runtime carrier (Protocols + callables), NOT a wire model.

    Fields:
      * ``ghost``   — the narrow :class:`GhostHandle` command surface (walk/play/say/…).
      * ``browser`` — the FULL-primitive :class:`BrowserProvider` (session/nav/mouse/
        keyboard/page/extract/scrape/search/screenshot); the ONLY path to GhostCrawl.
      * ``world``   — the read-only :class:`WorldQuery` (free workstations, section bounds,
        random reachable/workstation tiles).
      * ``emit``    — the async normalized-``Envelope`` sink (see :meth:`emit_event`).
      * ``task``    — the assigned :class:`Task` (``None`` for ambient behaviors).
      * ``section`` — the ghost's :class:`SectionRef` (role + optional bounds/roster).
      * ``rng``     — a seeded :class:`random.Random` for deterministic decisions.
      * ``log``     — a structured breadcrumb sink ``Callable[[str], None]``.
      * ``watched`` — a ``() -> bool`` predicate: True while the operator is CURRENTLY watching
        this ghost in the live inspector. A behavior may use it to keep its live browser session
        open + moving while watched (P1 watched-hold). Capability-safe — a plain bool predicate,
        no host/SDK reach. Defaults to "never watched" so a behavior is unaffected off the
        operator app.
    """

    ghost: GhostHandle
    browser: BrowserProvider
    world: WorldQuery
    emit: Emit
    task: Task | None = None
    section: SectionRef | None = None
    rng: random.Random = field(default_factory=random.Random)
    log: Callable[[str], None] = _noop_log
    watched: Callable[[], bool] = _never_watched

    async def emit_event(
        self, type: str, payload: dict[str, Any], *, ghost_id: str | None = None
    ) -> None:
        """Build a normalized ``Envelope`` (stamped with ``PROTOCOL_VERSION``) and publish
        it through :attr:`emit`.

        ``ghost_id`` defaults to ``task.params['ghost_id']`` when a task is bound, so a
        behavior rarely passes it explicitly.
        """
        gid = ghost_id
        if gid is None and self.task is not None:
            raw = self.task.params.get("ghost_id")
            gid = raw if isinstance(raw, str) else None
        await self.emit(
            serialize_envelope(type=type, ts=time.time(), payload=payload, ghost_id=gid)
        )


@runtime_checkable
class Behavior(Protocol):
    """The pluggable decision unit. ONE active Behavior per ghost fills the WORK state of
    the lifecycle; the ghost stays dumb (motion/anim only).

    Lifecycle (all async):
      * ``on_start(ctx)``         — set up (queue urls, walk to workstation, …).
      * ``on_tick(ctx, dt_ms)``   — advance one step; NON-BLOCKING (≤1 awaited op).
      * ``on_event(ctx, event)``  — react to a normalized ``GhostEvent`` (op completion).
      * ``on_end(ctx, reason)``   — tear down (release session, walk home) on
        completed/failed/cancelled/retargeted.
    """

    name: str

    async def on_start(self, ctx: BehaviorContext) -> None: ...
    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None: ...
    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None: ...
    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None: ...
