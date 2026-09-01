"""The ``EventSource`` seam — one interface, simulated OR real events.

The whole ghostopia world reacts to a single normalized event stream: ``ghost.*`` /
``browser.*`` / ``task.*`` / ``result.*`` :class:`~ghostopia_shared.Envelope` messages. An
``EventSource`` is whatever PRODUCES that stream. In stages 1-2 the producer is the
:class:`~ghostopia_core.sim_event_source.SimEventSource` (scripted, over a
``FakeBrowserProvider``); in stage 3+ it is the real GhostCrawl orchestrator — and because
BOTH satisfy this one Protocol, swapping them never touches the world (``wire_world``,
``GhostDriver``, the renderer). This is the seam that makes "the sim proves the whole
pipeline; then real events drop in with no world change" true.

A source fans every produced envelope to its subscribers (the ``GhostDriver.dispatch`` is
the primary subscriber). ``start()`` begins producing; ``stop()`` halts it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from ghostopia_shared import Envelope

# A subscriber of the normalized event stream. Sync (e.g. ``GhostDriver.dispatch``) or
# async — the source awaits an awaitable result.
EventHandler = Callable[[Envelope], Awaitable[None] | None]


@runtime_checkable
class EventSource(Protocol):
    """Produces the normalized ``Envelope`` stream the world reacts to.

    Implementations: :class:`SimEventSource` (stages 1-2, scripted over the
    ``FakeBrowserProvider``) and the real GhostCrawl orchestrator (stage 3+). Both fan
    every envelope to their subscribers so the SAME ``GhostDriver`` + renderer path drives
    the world regardless of who produced the event.
    """

    def subscribe(self, handler: EventHandler) -> None:
        """Register ``handler`` to receive every produced envelope (in produce order)."""
        ...

    async def start(self) -> None:
        """Begin producing the event stream (idempotent; a second call is a no-op)."""
        ...

    async def stop(self) -> None:
        """Halt production (idempotent)."""
        ...
