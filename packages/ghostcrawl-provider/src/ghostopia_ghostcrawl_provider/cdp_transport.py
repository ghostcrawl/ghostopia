"""The ONE documented non-SDK path: a thin Python-``websockets`` CDP transport.

``client.cdp.url(session_id=...)`` mints a signed ``wss`` relay (120s jwt, chromium-only,
paid tier + ``cdp_passthrough_enabled``) that speaks full ``Input.dispatchMouseEvent`` /
``Input.dispatchKeyEvent`` — the ONLY way to express a HELD mouse drag (the whiteboard
recipe), which ``cdp.input``'s ``{navigate,click,type}`` steps cannot. The ghostcrawl
Python SDK is a Kiota REST client and ships NO persistent CDP-WS transport, so ghostopia
builds this one here (disposition (a)); the proposed SDK ``cdp.connect(session_id)``
live-driver that would absorb it is tracked as a known follow-up.

Design notes:
- ``mint_url()`` is an injected async callable returning ``{url, expires_in_seconds,
  engine}`` (the provider wires it to ``client.cdp.url``); the transport NEVER imports the
  SDK — it stays a pure transport so the SDK boundary is unbroken.
- ``ws_connect`` is injectable (default: real ``websockets.connect``) so tests drive a fake
  in-memory socket with ZERO network.
- Re-mint is bounded to token expiry (a monotonic-clock deadline from
  ``expires_in_seconds``) AND an auth-close during a frame — an in-flight long stroke
  survives a re-mint (the token is held server-side, re-mint is not
  per-frame).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

# ``mint_url()`` -> {url, expires_in_seconds, engine}; the provider binds it to cdp.url().
MintResult = dict[str, Any]
MintFn = Callable[[], Awaitable[MintResult]]
# ``ws_connect(url)`` -> an open socket with async ``send``/``recv``/``close`` (websockets).
WsConnectFn = Callable[[str], Awaitable[Any]]
ClockFn = Callable[[], float]

# WebSocket close codes / markers that mean "the 120s relay token is no longer valid".
_AUTH_CLOSE_CODES = frozenset({1008, 4001, 4003})
# Re-mint this many seconds BEFORE the token's stated expiry (clock-skew safety margin).
_REMINT_MARGIN_S = 1.0


def _is_auth_close(err: Exception) -> bool:
    """True when a socket error signals an expired/invalid relay token (→ re-mint)."""
    code = getattr(err, "code", None)
    if code in _AUTH_CLOSE_CODES:
        return True
    text = str(err).lower()
    return "auth" in text or "token" in text or "expired" in text


async def _default_ws_connect(url: str) -> Any:
    """Dial the real signed relay ``wss`` with the ``websockets`` client (prod path)."""
    import websockets

    return await websockets.connect(url)


class CdpWsTransport:
    """A persistent CDP-over-WebSocket transport bound to ONE session's signed relay.

    ``send(method, params)`` writes a JSON-RPC frame with an incrementing id and awaits the
    ack; a raw ``mousePressed → mouseMoved×N → mouseReleased`` sequence lands on the socket
    in order. The socket is dialed lazily on the first ``send`` and re-dialed transparently
    when the 120s token is at/near expiry or the socket closes with an auth code.
    """

    def __init__(
        self,
        mint_url: MintFn,
        ws_connect: WsConnectFn | None = None,
        *,
        clock: ClockFn = time.monotonic,
    ) -> None:
        self._mint_url = mint_url
        self._ws_connect = ws_connect or _default_ws_connect
        self._clock = clock
        self._ws: Any | None = None
        self._id = 0
        self._engine: str | None = None
        self._url: str | None = None
        self._deadline: float = 0.0

    @property
    def engine(self) -> str | None:
        """The engine reported by the last mint (``chromium`` for the raw-input relay)."""
        return self._engine

    # --- dialing / re-minting ---------------------------------------------------------
    async def _dial(self) -> None:
        minted = await self._mint_url()
        self._url = str(minted["url"])
        self._engine = minted.get("engine")
        expires_in = float(minted.get("expires_in_seconds", 120))
        self._deadline = self._clock() + expires_in
        self._ws = await self._ws_connect(self._url)

    async def remint(self) -> None:
        """Re-mint the 120s relay token and re-dial (drops the stale socket)."""
        await self._close_ws()
        await self._dial()

    async def _ensure_fresh(self) -> None:
        if self._ws is None:
            await self._dial()
        elif self._clock() >= self._deadline - _REMINT_MARGIN_S:
            # The token is about to expire mid-stroke — re-mint BEFORE sending the frame.
            await self.remint()

    # --- framing ----------------------------------------------------------------------
    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one raw CDP frame (e.g. ``Input.dispatchMouseEvent``) and await its ack."""
        await self._ensure_fresh()
        self._id += 1
        frame = {"id": self._id, "method": method, "params": params or {}}
        try:
            return await self._write(frame)
        except Exception as err:  # noqa: BLE001 — narrowed by _is_auth_close
            if _is_auth_close(err):
                # Auth-close during the frame: re-mint + re-dial, resend once so a long
                # stroke completes across the re-mint.
                await self.remint()
                return await self._write(frame)
            raise

    async def _write(self, frame: dict[str, Any]) -> dict[str, Any]:
        assert self._ws is not None
        await self._ws.send(json.dumps(frame))
        raw = await self._ws.recv()
        if isinstance(raw, (str, bytes, bytearray)):
            return json.loads(raw)
        return dict(raw)

    # --- teardown ---------------------------------------------------------------------
    async def _close_ws(self) -> None:
        if self._ws is not None:
            close = getattr(self._ws, "close", None)
            if close is not None:
                await close()
            self._ws = None

    async def close(self) -> None:
        """Tear the socket down (idempotent)."""
        await self._close_ws()


def create_cdp_ws_transport(
    mint_url: MintFn,
    ws_connect: WsConnectFn | None = None,
    *,
    clock: ClockFn = time.monotonic,
) -> CdpWsTransport:
    """Build a :class:`CdpWsTransport` over the ``mint_url`` relay minter.

    ``mint_url()`` returns ``{url, expires_in_seconds, engine}`` (the provider binds it to
    ``client.cdp.url(session_id=...)``). ``ws_connect`` defaults to the real
    ``websockets.connect`` and is injectable so tests use a fake socket (no network).
    """
    return CdpWsTransport(mint_url, ws_connect, clock=clock)


def interpolate_stroke(frm: Any, to: Any, *, step_px: float = 12.0) -> list[Any]:
    """Intermediate points between ``frm`` and ``to`` at ~8-16px steps.

    Returns the interior points only (excludes the endpoints); a HELD drag glides through
    these with ``buttons:1`` between the ``down`` and the ``up``. ``frm``/``to`` are
    ``Point``-like (``.x``/``.y``); the return values are the same type as ``frm``.
    """
    point_cls = type(frm)
    dx = to.x - frm.x
    dy = to.y - frm.y
    dist = math.hypot(dx, dy)
    n = max(1, int(dist // step_px))
    return [point_cls(x=frm.x + dx * (i / n), y=frm.y + dy * (i / n)) for i in range(1, n)]


__all__ = [
    "CdpWsTransport",
    "create_cdp_ws_transport",
    "interpolate_stroke",
    "MintFn",
    "MintResult",
    "WsConnectFn",
]
