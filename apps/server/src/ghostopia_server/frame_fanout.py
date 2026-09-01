"""Selected-ghost live frame fan-out — the STAGE-4 inspector's server engine.

The load-bearing "real, not a mockup" seam: when the operator clicks a working ghost, the
Python server opens ``recordings.visual(session_id).watch()`` for THAT ghost's REAL session
(via :meth:`GhostCrawlProvider.live_frames`) and relays each JPEG frame reference over the
authed WS as a ``browser.frame`` envelope; the thin TS inspector draws it into an ``<img>``.
The client never calls GhostCrawl and holds no key.

Performance invariant: only ONE frame stream is ever
active — the SELECTED ghost. Selecting a different ghost CANCELS the prior ``watch()`` and
starts the new one; a deselect stops the stream entirely. No ``watch()`` is ever opened for a
non-selected ghost (that would fan out N concurrent recordings and hammer proxy/governor
concurrency — a known anti-pattern).

Threat model: frames ride only the authenticated WS; a single active
cancel scope enforces the one-stream ceiling; a ghost only ever streams its own
``session_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ghostopia_ghost_runtime.surface_vocab import (
    ACTIVITY_VIEW_MESSAGE,
    GENERIC_WORKING,
    sanitize_text,
)
from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope

#: An async fan-out sink (``WsGateway.broadcast`` in production; a collector in tests).
Broadcast = Callable[[Envelope], Awaitable[None]]

# Outbound envelope types the thin inspector consumes (server → client only; not inbound
# verbs, so they need no ``schemas`` allow-list entry — the client validates the generic
# Envelope contract).
_FRAME_TYPE = "browser.frame"
_STATUS_TYPE = "browser.status"
# The type-aware mode envelope: announces whether the inspector should show the live
# browser frame (``view="live"``, browser-nav) or an activity view (``view="activity"``,
# api-only), plus the sanitized session persona when available.
_VIEW_TYPE = "browser.view"

# The honest "the session isn't open yet" reason shown when the selected ghost is momentarily
# between cycles (unregistered) so the inspector shows WHY the live frame can't stream instead of
# an eternal blank. Sanitized before broadcast like every other reason (customer-safe copy).
_AWAITING_SESSION = "Waking a browser for this ghost — the live view opens in a moment…"


class SessionRegistry:
    """Maps a ghost id → the live :class:`GhostCrawlProvider` driving its real session.

    :func:`~ghostopia_server.gc_event_source.run_real_task` registers a provider when a
    mission starts and unregisters it in its ``finally`` (one session per ghost). The
    :class:`FrameFanout` reads the provider here to stream the SELECTED ghost's frames — it
    never holds a session itself, so a released session can never leak frames.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}

    def register(self, ghost_id: str, provider: Any) -> None:
        self._providers[ghost_id] = provider

    def unregister(self, ghost_id: str) -> None:
        self._providers.pop(ghost_id, None)

    def get(self, ghost_id: str) -> Any | None:
        return self._providers.get(ghost_id)


class FrameFanout:
    """A single-active-stream fan-out of the selected ghost's real ``recordings.visual`` frames.

    Holds ONE ``asyncio`` task at a time. :meth:`select_ghost_frames` cancels the prior stream
    (if any) and — when a ghost is selected and has a live session — starts a new one that
    broadcasts ``browser.frame`` envelopes plus periodic ``browser.status`` (current_url/title).
    Selecting ``None`` (a deselect / panel close) just stops the active stream.
    """

    def __init__(
        self,
        registry: SessionRegistry,
        broadcast: Broadcast,
        *,
        status_interval: float = 1.0,
        watch_interval: float = 0.5,
    ) -> None:
        self._registry = registry
        self._broadcast = broadcast
        self._status_interval = status_interval
        # How often the stream re-evaluates provider.session/work_kind while a ghost is selected
        # but not yet live (R7): a session opening AFTER select upgrades activity→frames without
        # a re-select. The watch path only READS .session — it never opens one (no live-budget
        # regression), so this poll is free of the 2-slot live-session ceiling.
        self._watch_interval = watch_interval
        self._task: asyncio.Task[None] | None = None
        self._selected: str | None = None
        self._signal: asyncio.Event | None = None

    @property
    def selected_ghost_id(self) -> str | None:
        """The ghost currently streaming (``None`` when no stream is active)."""
        return self._selected

    @property
    def active(self) -> bool:
        """True while exactly one frame stream is running."""
        return self._task is not None and not self._task.done()

    async def select_ghost_frames(self, ghost_id: str | None) -> None:
        """Stream the selected ghost's real frames — cancelling any prior stream first.

        The one-stream invariant: whatever was streaming is cancelled BEFORE a new
        stream starts, so at most one ``watch()`` is ever open. A ``None`` ghost id (deselect)
        stops streaming and returns. A ghost with no live session (not yet spawned / already
        released) is a no-op after the stop — no phantom stream.
        """
        # 1. Cancel the prior stream (enforces single-active-stream).
        await self._cancel_current()

        if ghost_id is None:
            self._selected = None
            return

        provider = self._registry.get(ghost_id)
        if provider is None:
            # Selecting a ghost with no live session: nothing to stream (the prior was already
            # stopped above). The client still shows lightweight status from its own store.
            self._selected = None
            return

        # 2. Start the new (single) stream. The stream RE-FETCHES the provider from the registry
        # each tick (a looping pool ghost gets a BRAND-NEW provider object every cycle — see
        # ghost_pool `_provider_factory()` — so a captured reference goes stale the moment the
        # cycle-1 session is released and never observes cycle-2's fresh session).
        self._selected = ghost_id
        self._signal = asyncio.Event()
        self._task = asyncio.ensure_future(self._stream(ghost_id, self._signal))

    async def stop(self) -> None:
        """Stop any active stream (server shutdown / final deselect)."""
        await self._cancel_current()
        self._selected = None

    async def _cancel_current(self) -> None:
        task, signal = self._task, self._signal
        self._task = None
        self._signal = None
        if signal is not None:
            signal.set()  # let a cooperative watcher break at its next yield
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _stream(self, ghost_id: str, signal: asyncio.Event) -> None:
        """DYNAMICALLY stream the ghost's real frames/activity until cancelled/deselected (R7).

        The mode is re-evaluated on an interval instead of snapshotted once at select time
        a session opening AFTER select UPGRADES activity→frames, and a
        live view that can't stream (session not open yet, or the workspace's live-view
        capability is off) shows an HONEST reason rather than an eternal "No live view yet"
        placeholder. ``browser.view`` is re-emitted whenever the mode/reason FLIPS so the
        inspector's tab and body agree. The single-active cancel scope is UNCHANGED — one task,
        cancelled on select/deselect/stop. The watch path only READS ``provider.session``; it
        never opens one, so the 2-slot live-session budget is never spent here.

        The provider is RE-FETCHED from the registry each tick (not captured at select time): a
        looping pool ghost is handed a fresh provider + fresh session every cycle, so following
        the registry is the only way the stream can observe the ghost's NEXT cycle after the
        current session is released. If the ghost is momentarily unregistered (between cycles),
        we hold an "awaiting session" activity view and keep polling until it re-registers.
        """
        # The last (work_kind, view, reason) announced — re-emit browser.view only on a flip.
        announced: tuple[str, str, str | None] | None = None
        while not signal.is_set():
            provider = self._registry.get(ghost_id)
            if provider is None:
                # Between cycles (or just despawned): nothing to read yet. Keep the honest
                # "awaiting a session" activity view and re-poll — a new cycle re-registers.
                announced = await self._announce_if_changed(
                    ghost_id, None, "browser-nav", "live", _AWAITING_SESSION, announced
                )
                await asyncio.sleep(self._watch_interval)
                continue
            work_kind = self._resolve_work_kind(provider)
            handle = getattr(provider, "session", None)

            if work_kind == "api-only":
                # An api-only (scrape/extract) ghost has no live browser frame — the activity/
                # records view IS its live view. No session probe, no reason.
                announced = await self._announce_if_changed(
                    ghost_id, provider, work_kind, "activity", None, announced
                )
                await self._activity_tick(ghost_id, provider)
                await asyncio.sleep(self._watch_interval)
                continue

            # A browser-nav ghost: probe whether the live frame can actually stream right now.
            available, reason = await self._live_view_status(provider, handle)
            announced = await self._announce_if_changed(
                ghost_id, provider, work_kind, "live", None if available else reason, announced
            )
            if available and handle is not None:
                # Live view is up → stream frames until the session ends / the stream errors,
                # then loop to re-evaluate (it may drop back to an activity/reason view).
                await self._run_frames(ghost_id, provider, handle, signal)
                continue
            # Not streamable yet (awaiting the session open, or capability off): keep the
            # activity ticks flowing under the honest reason, and re-poll for an upgrade.
            await self._activity_tick(ghost_id, provider)
            await asyncio.sleep(self._watch_interval)

    async def _live_view_status(
        self, provider: Any, handle: Any
    ) -> tuple[bool, str | None]:
        """(available, reason) for the ghost's live view — tolerant of a provider without it.

        A provider exposing ``live_view_status`` returns the HONEST probe (R7); an older/mock
        provider without it defaults to available whenever a session handle is present (the
        prior behavior), so the existing frame-stream contract is preserved.
        """
        fn = getattr(provider, "live_view_status", None)
        if callable(fn):
            with contextlib.suppress(Exception):
                status = await fn(handle)
                if isinstance(status, dict):
                    reason = status.get("reason")
                    return bool(status.get("available")), (
                        reason if isinstance(reason, str) and reason else None
                    )
        return handle is not None, None

    async def _run_frames(
        self, ghost_id: str, provider: Any, handle: Any, signal: asyncio.Event
    ) -> None:
        """Stream ``browser.frame`` envelopes + periodic status for one live session pass."""
        status_task = asyncio.ensure_future(
            self._status_loop(ghost_id, provider, signal)
        )
        frames = None
        try:
            frames = provider.live_frames(handle, signal)
            async for ref in frames:
                await self._broadcast(
                    serialize_envelope(
                        type=_FRAME_TYPE,
                        ts=time.time(),
                        ghost_id=ghost_id,
                        payload={"ref": str(ref)},
                    )
                )
                if signal.is_set():
                    break
        finally:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task
            # Defensively close the frame generator (its finally stops the recording).
            aclose = getattr(frames, "aclose", None) if frames is not None else None
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    async def _status_loop(
        self, ghost_id: str, provider: Any, signal: asyncio.Event
    ) -> None:
        """Periodically push the selected ghost's real current_url + title over the WS."""
        while not signal.is_set():
            current_url, title = await self._read_status(provider)
            if current_url is not None or title is not None:
                await self._broadcast(
                    serialize_envelope(
                        type=_STATUS_TYPE,
                        ts=time.time(),
                        ghost_id=ghost_id,
                        payload={"current_url": current_url, "title": title},
                    )
                )
            await asyncio.sleep(self._status_interval)

    @staticmethod
    def _resolve_work_kind(provider: Any) -> str:
        """The provider's ghost work-kind (``browser-nav``/``api-only``); default browser-nav.

        Tolerant of a provider without the accessor (older/mock) — defaults to ``browser-nav``
        so the existing live-frame behavior (and its graceful placeholder) is preserved.
        """
        fn = getattr(provider, "work_kind", None)
        if callable(fn):
            with contextlib.suppress(Exception):
                kind = fn()
                if isinstance(kind, str) and kind:
                    return kind
        return "browser-nav"

    async def _resolve_persona(self, provider: Any) -> str | None:
        """The provider's sanitized persona sentence over the SDK, or ``None``.

        The provider already returns a whitelist-only sentence (or ``None``); this is a
        belt-and-suspenders re-sanitize before broadcast so a banned token can never surface
        even if a future provider path regresses (floor)."""
        fn = getattr(provider, "persona", None)
        if fn is None:
            return None
        with contextlib.suppress(Exception):
            result = fn()
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, str) and result.strip():
                safe = sanitize_text(result, fallback="")
                return safe or None
        return None

    async def _announce_if_changed(
        self,
        ghost_id: str,
        provider: Any,
        work_kind: str,
        view: str,
        reason: str | None,
        announced: tuple[str, str, str | None] | None,
    ) -> tuple[str, str, str | None]:
        """Re-emit ``browser.view`` only when (work_kind, view, reason) FLIPS (R7 tab/body agree).

        The flip that matters: activity→live when a session opens (reason clears), or a live
        view becoming/ceasing to be available (the honest reason appears/disappears). Returns the
        now-current tuple so the caller tracks it.
        """
        current = (work_kind, view, reason)
        if current != announced:
            await self._broadcast_view(ghost_id, provider, work_kind, view, reason)
        return current

    async def _broadcast_view(
        self,
        ghost_id: str,
        provider: Any,
        work_kind: str,
        view: str,
        reason: str | None = None,
    ) -> None:
        """Emit the ``browser.view`` mode envelope (live vs activity) + sanitized persona.

        Carries an optional ``reason`` (R7): when the live view can't stream right now, the
        inspector shows this honest, customer-safe sentence instead of an eternal placeholder.
        The reason is re-sanitized here as the server-side backstop before broadcast.
        """
        persona = await self._resolve_persona(provider)
        payload: dict[str, Any] = {"work_kind": work_kind, "view": view}
        if persona:
            payload["persona"] = persona
        if reason:
            safe = sanitize_text(reason, fallback="")
            if safe:
                payload["reason"] = safe
        await self._broadcast(
            serialize_envelope(
                type=_VIEW_TYPE,
                ts=time.time(),
                ghost_id=ghost_id,
                payload=payload,
            )
        )

    async def _activity_tick(self, ghost_id: str, provider: Any) -> None:
        """Emit ONE activity status tick (no live frame) — the dynamic re-eval loop paces it."""
        message = sanitize_text(ACTIVITY_VIEW_MESSAGE, fallback=GENERIC_WORKING)
        current_url, title = await self._read_status(provider)
        await self._broadcast(
            serialize_envelope(
                type=_STATUS_TYPE,
                ts=time.time(),
                ghost_id=ghost_id,
                payload={
                    "view": "activity",
                    "message": message,
                    "current_url": current_url,
                    "title": title,
                },
            )
        )

    @staticmethod
    async def _read_status(provider: Any) -> tuple[str | None, str | None]:
        """Read (current_url, title) from the real session — tolerant of a partial provider."""
        current_url: str | None = None
        title: str | None = None
        nav = getattr(provider, "nav", None)
        if nav is not None and hasattr(nav, "current_url"):
            with contextlib.suppress(Exception):
                current_url = await nav.current_url()
        page = getattr(provider, "page", None)
        if page is not None and hasattr(page, "eval"):
            with contextlib.suppress(Exception):
                result = await page.eval("document.title")
                title = str(result) if result is not None else None
        return current_url, title

    def install(self, gateway: Any) -> None:
        """Mount the authed ``ghost.select {ghost_id}`` control verb on the WS gateway.

        The verb is already validated + allow-listed (``schemas.GhostSelect``); this routes an
        accepted frame to :meth:`select_ghost_frames`. A ``null``/absent ghost id deselects.
        """
        gateway.register_control("ghost.select", self._on_select)

    async def _on_select(self, envelope: Envelope) -> None:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        raw = payload.get("ghost_id")
        ghost_id = str(raw) if isinstance(raw, str) and raw else None
        await self.select_ghost_frames(ghost_id)


async def select_ghost_frames(
    fanout: FrameFanout, ghost_id: str | None
) -> None:
    """Module-level convenience: drive :meth:`FrameFanout.select_ghost_frames`.

    Kept as a free function so callers (and the WS wiring) can select the streamed ghost
    without reaching into the fan-out object, mirroring the ``run_real_task`` free-function
    shape used elsewhere in the server.
    """
    await fanout.select_ghost_frames(ghost_id)


__all__ = [
    "Broadcast",
    "FrameFanout",
    "SessionRegistry",
    "select_ghost_frames",
]
