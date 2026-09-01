"""Authenticated WebSocket gateway — the secure inbound/outbound boundary.

This is the ONE place the thin TS renderer talks to the Python server. On the WS upgrade
the gateway verifies the operator's HS256 token BEFORE accepting the connection:
an unauthenticated or invalid-token upgrade is closed pre-accept, never
promoted to a live socket. Once accepted, every inbound frame is parsed into the shared
``Envelope``, version-gated, and validated against the strict ``schemas`` allow-list
— an unknown ``type`` or a malformed payload is rejected and NEVER
fanned out or ``eval``'d. Outbound ``broadcast`` re-validates against the ``Envelope``
model before sending, so only well-formed envelopes ever leave the server.

Dispatch of validated verbs (task/mission orchestration, GhostCrawl sessions, live-frame
fan-out) attaches BEHIND this boundary in later plans; here a valid verb is acknowledged
and a ``client.ping`` is answered with a fanned-out ``server.pong`` — proving the accept +
validated-broadcast path end to end.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import jwt
from fastapi import FastAPI, WebSocket
from ghostopia_shared.envelope import (
    Envelope,
    is_supported_version,
    parse_envelope,
    serialize_envelope,
)
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from . import schemas
from .auth import verify_token

# WS close codes.
_CLOSE_POLICY_VIOLATION = 1008

# A registered control handler for an authed operator verb (e.g. ``sim.start``). It runs
# ONLY AFTER the frame passed JWT-accept + version-gate + the strict inbound allow-list, so a
# handler never sees unauthenticated or unvalidated input.
ControlHandler = Callable[["Envelope"], Awaitable[None]]

# A per-client "replay" hook: invoked once, right after a client is accepted, with a
# ``send`` bound to THAT client only. The live app registers a hook that replays the current
# world (a positioned ``ghost.spawned`` per existing ghost) so a late-joining / refreshing
# client renders ghosts that spawned before it connected — instead of a roster-full-but-empty
# canvas. It sends ONLY to the new socket (never a fan-out) so existing clients don't re-spawn.
OnConnect = Callable[[Callable[["Envelope"], Awaitable[None]]], Awaitable[None]]


class WsGateway:
    """Server-authoritative WS hub: JWT-gated accept + validated fan-out."""

    def __init__(self, secret: str) -> None:
        self._secret = secret
        self._clients: set[WebSocket] = set()
        self._control_handlers: dict[str, ControlHandler] = {}
        self._on_connect: OnConnect | None = None
        self._on_disconnect: Callable[[], Awaitable[None]] | None = None
        self._on_first_connect: Callable[[], Awaitable[None]] | None = None

    def set_on_first_connect(self, hook: Callable[[], Awaitable[None]] | None) -> None:
        """Register a hook run when the FIRST client connects (client_count becomes 1), BEFORE the
        per-client replay hook. The live app uses it for INTENT-BASED RESUME: if the
        operator's persistent "workforce should be running" intent is set but the world was torn
        down while every client was gone (the idle-teardown fired after the grace), the first
        reconnect attaches-or-restarts the workforce — so a reload longer than the grace rejoins a
        live world instead of the onboarding CTA. A hook fault is swallowed, never fatal."""
        self._on_first_connect = hook

    def set_on_disconnect(self, hook: Callable[[], Awaitable[None]] | None) -> None:
        """Register a hook run AFTER a client disconnects (the gateway has already removed it).

        The live production app leaves this unset (no behavior change); the keyless harness uses it
        to reset the shared in-memory pool once the LAST client leaves, so each fresh connection
        starts from a clean, freshly-seeded server — the looping workforce ghosts a prior session
        spawned do not persist into the next."""
        self._on_disconnect = hook

    def set_on_connect(self, hook: OnConnect | None) -> None:
        """Register a per-client replay hook run once when a client is accepted.

        The hook receives a ``send`` coroutine bound to the freshly-connected socket ONLY, so
        it can replay the current world (positioned ``ghost.spawned`` envelopes) to a
        late-joiner without re-broadcasting to already-connected clients (idempotent upsert on
        the client keeps a re-sent id from double-counting)."""
        self._on_connect = hook

    def register_control(self, msg_type: str, handler: ControlHandler) -> None:
        """Mount a handler for a VALIDATED, ALLOW-LISTED control verb (e.g. ``sim.start``).

        The verb must already be in ``schemas.INBOUND_MODELS`` (so its payload is validated
        before dispatch); this only routes the accepted frame to a server-side action. The
        composition layer (``sim_runtime``) uses it to mount the simulation behind the
        authed WS without weakening the inbound boundary."""
        self._control_handlers[msg_type] = handler

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _authenticate(self, websocket: WebSocket) -> bool:
        """Verify the ``?token=`` HS256 JWT. Any failure -> reject (no accept)."""
        token = websocket.query_params.get("token")
        if not token:
            return False
        try:
            verify_token(token, secret=self._secret)
        except jwt.InvalidTokenError:
            return False
        return True

    async def handle(self, websocket: WebSocket) -> None:
        """Handle one WS connection lifecycle: authenticate -> accept -> receive loop."""
        if not self._authenticate(websocket):
            # Close BEFORE accept so an unauthenticated upgrade never becomes live.
            await websocket.close(code=_CLOSE_POLICY_VIOLATION)
            return
        await websocket.accept()
        self._clients.add(websocket)
        # If this is the FIRST client, reset the shared pool to a clean slate BEFORE the replay
        # below, so every fresh connection starts clean regardless of the prior client's disconnect
        # timing (wired only on the keyless harness; unset on the live app).
        if len(self._clients) == 1 and self._on_first_connect is not None:
            try:
                await self._on_first_connect()
            except Exception:  # noqa: BLE001 — first-connect cleanup is best-effort, never fatal
                pass
        # Replay the current world to THIS client only (a late-joiner/refresh sees the
        # ghosts that spawned before it connected). Best-effort — a hook fault never drops the
        # connection; the recurring status poll still keeps the roster fresh.
        if self._on_connect is not None:
            async def _send_one(env: Envelope | dict[str, Any]) -> None:
                validated = env if isinstance(env, Envelope) else Envelope.model_validate(env)
                await websocket.send_text(validated.model_dump_json())

            try:
                await self._on_connect(_send_one)
            except Exception:  # noqa: BLE001 — replay is best-effort, never fatal
                pass
        try:
            while True:
                raw = await websocket.receive_text()
                await self._on_message(websocket, raw)
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(websocket)
            # Fire the last-disconnect hook if one is installed (a generic gateway seam; the
            # operator workforce leaves it unset so the world survives viewer reconnects).
            # Best-effort — a hook fault never affects the connection.
            if self._on_disconnect is not None:
                try:
                    await self._on_disconnect()
                except Exception:  # noqa: BLE001 — disconnect cleanup is best-effort, never fatal
                    pass

    async def _reject(self, websocket: WebSocket, reason: str) -> None:
        """Send a validated rejection envelope to the offending client only."""
        env = serialize_envelope(
            type="error.rejected", ts=time.time(), payload={"reason": reason}
        )
        await websocket.send_text(env.model_dump_json())

    async def _on_message(self, websocket: WebSocket, raw: str) -> None:
        """Parse, version-gate, and validate one inbound frame."""
        try:
            envelope = parse_envelope(raw)
        except (ValidationError, ValueError):
            await self._reject(websocket, "malformed envelope")
            return
        if not is_supported_version(envelope):
            await self._reject(websocket, "unsupported protocol_version")
            return
        try:
            schemas.validate_inbound(envelope.type, envelope.payload)
        except schemas.UnknownMessageTypeError:
            await self._reject(websocket, f"unknown type: {envelope.type}")
            return
        except ValidationError:
            await self._reject(websocket, f"invalid payload for {envelope.type}")
            return

        # Validated + allow-listed. A registered control verb (e.g. sim.start) runs its
        # server-side action; everything else follows the ping/ack proof path.
        control = self._control_handlers.get(envelope.type)
        if control is not None:
            await control(envelope)
            return
        if envelope.type == "client.ping":
            await self.broadcast(
                serialize_envelope(type="server.pong", ts=time.time(), payload={})
            )
            return
        ack = serialize_envelope(
            type="server.ack", ts=time.time(), payload={"type": envelope.type}
        )
        await websocket.send_text(ack.model_dump_json())

    async def broadcast(self, envelope: Envelope | dict[str, Any]) -> None:
        """Fan out an envelope to every connected client, VALIDATED before send.

        A dict is coerced through the ``Envelope`` model (raises on invalid); an
        ``Envelope`` is used as-is. Only well-formed envelopes ever leave the server.
        """
        validated = envelope if isinstance(envelope, Envelope) else Envelope.model_validate(envelope)
        text = validated.model_dump_json()
        for client in list(self._clients):
            await client.send_text(text)


def start_ws_gateway(app: FastAPI, *, secret: str) -> WsGateway:
    """Create a ``WsGateway`` and register the ``/ws`` route on ``app``.

    Returns the gateway so the composition layer can drive ``broadcast`` (wired to the
    EventBus fan-out in later plans). Exposed on ``app.state.ws_gateway`` for callers.
    """
    gateway = WsGateway(secret=secret)
    app.state.ws_gateway = gateway

    @app.websocket("/ws")
    async def _ws_route(websocket: WebSocket) -> None:
        await gateway.handle(websocket)

    return gateway
