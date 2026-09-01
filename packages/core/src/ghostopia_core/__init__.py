"""ghostopia-core — the composition layer that makes the world come alive.

Wires the pieces built in isolation into the STAGE-2 living world behind the
:class:`~ghostopia_core.event_source.EventSource` seam: an
``EventSource`` produces the normalized event stream → :func:`~ghostopia_core.wire_world`
feeds it through the :class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver` (coarse
state + presentation) and mounts ONE ``Behavior`` per ghost BY NAME through the
registry → visual commands fan out to a ``broadcast`` sink (the server WS). In stages 1-2 the
source is the :class:`~ghostopia_core.sim_event_source.SimEventSource` (scripted, over the
``FakeBrowserProvider``, zero SDK); stage 3 swaps in real GhostCrawl events with no world
change.
"""

from __future__ import annotations

from ghostopia_core.event_source import EventHandler, EventSource
from ghostopia_core.sim_event_source import SimEventSource, SimGhost
from ghostopia_core.wire_world import BroadcastSink, WiredWorld, wire_world

__all__ = [
    "EventSource",
    "EventHandler",
    "SimEventSource",
    "SimGhost",
    "wire_world",
    "WiredWorld",
    "BroadcastSink",
]
