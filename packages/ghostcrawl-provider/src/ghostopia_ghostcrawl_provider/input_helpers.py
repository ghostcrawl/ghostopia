"""Ergonomic ``mouse``/``keyboard`` helpers over the CDP-WS relay.

``make_input_helpers(session_ref, transport, cdp_input)`` returns the ``mouse``/``keyboard``
sub-surfaces a behavior drives. Engine feature-detect + graceful degrade:

- **chromium + a live ``transport``** → raw ``Input.dispatchMouseEvent`` /
  ``dispatchKeyEvent`` frames. This is the ONLY path that expresses a HELD stroke:
  ``down`` (mousePressed, buttons:1) → N× ``move`` (mouseMoved, buttons:1 while held) →
  ``up`` (mouseReleased, buttons:0). ``drag`` interpolates the path (~8-16px steps).
- **FF/WebKit or a non-entitled session (no transport)** → DEGRADE to ``cdp.input``:
  ``click`` → ``cdp.input`` click, ``type`` → ``cdp.input`` type. The raw-only primitives
  (``move``/``down``/``up``/``hold``/``drag``) raise :class:`FeatureUnavailable` — an
  EXPLICIT failure, never a silent no-op.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ghostopia_shared import Button, Point

from ghostopia_ghostcrawl_provider.cdp_transport import CdpWsTransport, interpolate_stroke

# ``cdp_input(step_type, **params)`` -> the degrade path (client.cdp.input); may be None.
CdpInputFn = Callable[..., Awaitable[Any]]

# CDP ``buttons`` bitmask per pressed button (Input.dispatchMouseEvent ``buttons`` field).
_BUTTON_MASK: dict[Button, int] = {Button.LEFT: 1, Button.RIGHT: 2, Button.MIDDLE: 4}


class FeatureUnavailable(RuntimeError):
    """A raw-input primitive was invoked on an engine/tier that cannot express it.

    Raised (never silently ignored) when a chromium-relay-only op (raw ``move``/``down``/
    ``up``/``hold``/``drag`` — the HELD stroke) is called on a FF/WebKit or non-entitled
    session. Cross-engine behaviors should use ``click``/``type`` (which degrade cleanly).
    """


class _Mouse:
    """``mouse.*`` — raw pointer control on the relay; degrades click, refuses raw held ops."""

    def __init__(
        self, session_ref: Any, transport: CdpWsTransport | None, cdp_input: CdpInputFn | None
    ) -> None:
        self._session = session_ref
        self._transport = transport
        self._cdp_input = cdp_input
        self._buttons = 0
        self._x = 0.0
        self._y = 0.0

    def _relay(self) -> bool:
        return self._transport is not None and getattr(self._session, "engine", None) == "chromium"

    async def _dispatch(self, mouse_type: str, button: Button | None = None) -> None:
        assert self._transport is not None
        params: dict[str, Any] = {"type": mouse_type, "x": self._x, "y": self._y, "buttons": self._buttons}
        if button is not None:
            params["button"] = button.value
            params["clickCount"] = 1
        await self._transport.send("Input.dispatchMouseEvent", params)

    async def move(self, to: Point) -> None:
        self._x, self._y = to.x, to.y
        if not self._relay():
            raise FeatureUnavailable(
                "mouse.move is chromium-relay-only — use click()/drag() on this engine"
            )
        await self._dispatch("mouseMoved")

    async def down(self, button: Button = Button.LEFT) -> None:
        if not self._relay():
            raise FeatureUnavailable("mouse.down is chromium-relay-only (a HELD press)")
        self._buttons |= _BUTTON_MASK[button]
        await self._dispatch("mousePressed", button)

    async def up(self, button: Button = Button.LEFT) -> None:
        if not self._relay():
            raise FeatureUnavailable("mouse.up is chromium-relay-only (release of a HELD press)")
        self._buttons &= ~_BUTTON_MASK[button]
        await self._dispatch("mouseReleased", button)

    async def click(self, at: Point, button: Button = Button.LEFT) -> None:
        if self._relay():
            await self.move(at)
            await self.down(button)
            await self.up(button)
            return
        # DEGRADE: cdp.input click (an internal press+release) — the FF/WebKit path.
        if self._cdp_input is None:
            raise FeatureUnavailable("mouse.click: no relay and no cdp.input degrade path")
        await self._cdp_input("click", x=at.x, y=at.y, button=button.value)

    async def drag(self, frm: Point, to: Point, button: Button = Button.LEFT) -> None:
        # A HELD stroke: down at frm → glide (buttons:1) through the interpolated path → up.
        if not self._relay():
            raise FeatureUnavailable(
                "mouse.drag (a HELD stroke) is chromium-relay-only; cdp.input cannot express it"
            )
        # Whiteboard payload: mousePressed(buttons:1) at frm →
        # N× mouseMoved(buttons:1) → mouseReleased(buttons:0). Press AT frm (no pre-move).
        self._x, self._y = frm.x, frm.y
        await self.down(button)
        for point in interpolate_stroke(frm, to):
            await self.move(point)
        await self.move(to)
        await self.up(button)

    async def hold(self, at: Point, button: Button = Button.LEFT) -> None:
        if not self._relay():
            raise FeatureUnavailable("mouse.hold (press without release) is chromium-relay-only")
        # Press AND HOLD at ``at`` (no release) — the stroke stays down until a later up/drag.
        self._x, self._y = at.x, at.y
        await self.down(button)


class _Keyboard:
    """``keyboard.*`` — raw key input on the relay; degrades to ``cdp.input`` type."""

    def __init__(
        self, session_ref: Any, transport: CdpWsTransport | None, cdp_input: CdpInputFn | None
    ) -> None:
        self._session = session_ref
        self._transport = transport
        self._cdp_input = cdp_input

    def _relay(self) -> bool:
        return self._transport is not None and getattr(self._session, "engine", None) == "chromium"

    async def type(self, text: str) -> None:
        if self._relay():
            assert self._transport is not None
            for char in text:
                await self._transport.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": char})
                await self._transport.send("Input.dispatchKeyEvent", {"type": "keyUp", "text": char})
            return
        if self._cdp_input is None:
            raise FeatureUnavailable("keyboard.type: no relay and no cdp.input degrade path")
        await self._cdp_input("type", text=text)

    async def press(self, key: str) -> None:
        if self._relay():
            assert self._transport is not None
            await self._transport.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
            await self._transport.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
            return
        if self._cdp_input is None:
            raise FeatureUnavailable("keyboard.press: no relay and no cdp.input degrade path")
        await self._cdp_input("type", key=key)


@dataclass(frozen=True)
class InputHelpers:
    """The ``mouse``/``keyboard`` pair a provider exposes for one bound session."""

    mouse: _Mouse
    keyboard: _Keyboard


def make_input_helpers(
    session_ref: Any,
    transport: CdpWsTransport | None = None,
    cdp_input: CdpInputFn | None = None,
) -> InputHelpers:
    """Build the ergonomic ``mouse``/``keyboard`` helpers for one bound session.

    ``session_ref`` carries ``.engine`` (feature-detect: chromium → relay). ``transport``
    is the live :class:`CdpWsTransport` for the raw path (``None`` degrades). ``cdp_input``
    is the async ``client.cdp.input`` degrade path for click/type on FF/WebKit.
    """
    return InputHelpers(
        mouse=_Mouse(session_ref, transport, cdp_input),
        keyboard=_Keyboard(session_ref, transport, cdp_input),
    )


__all__ = ["make_input_helpers", "InputHelpers", "FeatureUnavailable", "CdpInputFn"]
