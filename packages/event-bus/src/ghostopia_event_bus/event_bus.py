"""The normalized in-proc async event bus.

One validated boundary the world, runtime, orchestrator, and server all share. Every
publish is validated against the shared ``Envelope`` model (incl. ``protocol_version``)
BEFORE fan-out — an invalid envelope raises and no subscriber sees it. Delivery is
ordered and in-proc: for a single event, matching handlers run in registration order;
across events, a subscriber sees them in publish order (``publish`` is awaited).

Patterns:
- exact:    ``ghost.status_changed`` matches only that type
- wildcard: ``ghost.*`` matches every ``ghost.`` type; ``*`` matches all
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from itertools import count
from typing import Any

from ghostopia_shared import Envelope

Handler = Callable[[Envelope], Awaitable[None] | None]


class Subscription:
    """An opaque handle returned by :meth:`EventBus.subscribe`; pass to
    :meth:`EventBus.unsubscribe` (or call :meth:`unsubscribe`) to stop delivery."""

    __slots__ = ("id", "pattern", "handler", "_bus")

    def __init__(self, sub_id: int, pattern: str, handler: Handler, bus: EventBus) -> None:
        self.id = sub_id
        self.pattern = pattern
        self.handler = handler
        self._bus = bus

    def unsubscribe(self) -> None:
        self._bus.unsubscribe(self)


def _matches(pattern: str, event_type: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-1]  # "ghost.*" -> "ghost."
        return event_type.startswith(prefix)
    return pattern == event_type


class EventBus:
    """A typed async pub/sub over ``ghost.*``/``browser.*``/``task.*``/``result.*``."""

    def __init__(self) -> None:
        # Single ordered list preserves global registration order for deterministic
        # fan-out; matching is evaluated per event.
        self._subs: list[Subscription] = []
        self._ids = count(1)

    def subscribe(self, pattern: str, handler: Handler) -> Subscription:
        """Register ``handler`` for events whose ``type`` matches ``pattern``. Returns a
        :class:`Subscription` handle for :meth:`unsubscribe`."""
        if not pattern:
            raise ValueError("subscription pattern must be non-empty")
        sub = Subscription(next(self._ids), pattern, handler, self)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Stop delivery to a subscription (idempotent)."""
        self._subs = [s for s in self._subs if s.id != sub.id]

    async def publish(self, envelope: Envelope | dict[str, Any]) -> None:
        """Validate ``envelope`` against the shared ``Envelope`` model, then fan out to
        matching handlers in registration order. Raises ``pydantic.ValidationError`` on
        an invalid envelope (before any handler runs)."""
        env = envelope if isinstance(envelope, Envelope) else Envelope.model_validate(envelope)
        # Snapshot so a handler that (un)subscribes during dispatch doesn't mutate the
        # in-flight iteration.
        for sub in list(self._subs):
            if _matches(sub.pattern, env.type):
                result = sub.handler(env)
                if inspect.isawaitable(result):
                    await result

    def publish_sync(self, envelope: Envelope | dict[str, Any]) -> None:
        """Synchronous convenience for sync-only handlers. Validates the envelope, then
        invokes matching handlers in registration order. Raises if a matched handler is
        a coroutine function (use :meth:`publish` for async handlers)."""
        env = envelope if isinstance(envelope, Envelope) else Envelope.model_validate(envelope)
        for sub in list(self._subs):
            if _matches(sub.pattern, env.type):
                result = sub.handler(env)
                if inspect.isawaitable(result):
                    # Don't silently drop a coroutine — surface the misuse.
                    if inspect.iscoroutine(result):
                        result.close()
                    raise RuntimeError(
                        f"handler for {sub.pattern!r} is async; use `await bus.publish(...)`"
                    )
