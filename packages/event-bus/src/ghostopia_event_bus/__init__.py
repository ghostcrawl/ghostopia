"""ghostopia-event-bus — the normalized in-proc async pub/sub.

Publishes/subscribes validated ``Envelope`` messages over the four namespaces
(``ghost.*``/``browser.*``/``task.*``/``result.*``). Every downstream package (world,
runtime, orchestrator, server) shares this one validated boundary.
"""

from __future__ import annotations

from ghostopia_event_bus.event_bus import EventBus, Subscription

__all__ = ["EventBus", "Subscription"]
