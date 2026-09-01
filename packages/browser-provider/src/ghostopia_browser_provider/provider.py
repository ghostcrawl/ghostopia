"""The full-primitive, capability-scoped ``BrowserProvider`` seam.

This is the SINGLE handle the whole ghostopia world depends on. It is bound to ONE
ghost's session and exposes the COMPLETE GhostCrawl action surface so a Behavior can do
anything a browser can — but NOTHING else: no ``os``/``sys``/``subprocess``/``socket``/
``httpx``/SDK member is reachable through it (capability-scoping).
Only the concrete ``GhostCrawlProvider`` crosses to the network/SDK; behaviors
and the world hold only this Protocol.

Surface (all async): session lifecycle (``create_session``/``open``/``release`` +
``session``), namespaced primitives ``nav`` / ``mouse`` / ``keyboard`` / ``page``, and the
top-level work verbs ``extract`` / ``scrape`` / ``search`` / ``screenshot`` / ``live_frames``.

The raw held mouse-drag (the whiteboard recipe) rides a signed CDP-WebSocket relay; that
relay is declared here as the typed ``CdpTransport`` Protocol — DECLARATION ONLY. Its
implementation (Python ``websockets``, raw ``Input.dispatchMouseEvent``, ~120s token
re-mint) lands later, behind this same seam, with no Protocol change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from ghostopia_shared import Button, Point
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------------------
# Concrete value types the seam returns
# --------------------------------------------------------------------------------------


class BrowserSessionHandle(BaseModel):
    """An opaque handle to ONE bound GhostCrawl browser session.

    Callers never juggle a raw ``session_id`` string — they hold this handle. ``engine``
    is carried so a behavior can honor the engine-matches-claim rule (e.g. raw mouse
    control is chromium-only) without reaching into the SDK.
    """

    session_id: str
    target: str
    engine: str


class ScrapeResult(BaseModel):
    """The result of a ``scrape``: extracted ``records`` plus an optional list of
    ``discovered_urls`` that ``NavigateAndExtract`` consumes to walk a site."""

    records: list[dict[str, Any]] = Field(default_factory=list)
    discovered_urls: list[str] | None = None


# --------------------------------------------------------------------------------------
# CDP-WebSocket transport seam (DECLARATION ONLY — impl later)
# --------------------------------------------------------------------------------------


@runtime_checkable
class CdpTransport(Protocol):
    """The typed seam for the signed CDP-WebSocket relay the raw mouse/keyboard helpers
    ride. ``send`` dispatches a raw CDP method
    (``Input.dispatchMouseEvent``/``dispatchKeyEvent``, …); ``remint`` refreshes the
    ~120s-lived signed relay token before it expires; ``close`` tears the socket down.

    DECLARATION ONLY here — the concrete ``websockets`` driver lands later. Declaring
    it now means the mouse/keyboard ergonomic helpers are written against a stable seam
    and the real transport drops in with no Protocol change.
    """

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def remint(self) -> None: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------------------------------
# Namespaced primitive sub-surfaces
# --------------------------------------------------------------------------------------


@runtime_checkable
class NavApi(Protocol):
    """``nav.*`` — page navigation on the bound session."""

    async def goto(self, url: str) -> None: ...
    async def current_url(self) -> str: ...


@runtime_checkable
class MouseApi(Protocol):
    """``mouse.*`` — raw pointer control (rides ``CdpTransport`` in the real impl).

    ``drag``/``hold`` express a HELD stroke (down without an immediate up) — the
    whiteboard-drawing recipe the step route cannot express."""

    async def move(self, to: Point) -> None: ...
    async def down(self, button: Button = Button.LEFT) -> None: ...
    async def up(self, button: Button = Button.LEFT) -> None: ...
    async def click(self, at: Point, button: Button = Button.LEFT) -> None: ...
    async def drag(self, frm: Point, to: Point, button: Button = Button.LEFT) -> None: ...
    async def hold(self, at: Point, button: Button = Button.LEFT) -> None: ...


@runtime_checkable
class KeyboardApi(Protocol):
    """``keyboard.*`` — raw key input on the bound session."""

    async def type(self, text: str) -> None: ...
    async def press(self, key: str) -> None: ...


@runtime_checkable
class PageApi(Protocol):
    """``page.*`` — the in-page primitive surface (eval/scroll/dom/wait/io/har)."""

    async def eval(self, expr: str) -> Any: ...
    async def scroll(self, dx: float = 0.0, dy: float = 0.0) -> None: ...
    async def dom_snapshot(self) -> dict[str, Any]: ...
    async def wait_for(self, condition: str, *, timeout_ms: float = 30_000.0) -> bool: ...
    async def cookies(self) -> list[dict[str, Any]]: ...
    async def upload(self, selector: str, path: str) -> None: ...
    async def download(self, url: str) -> bytes: ...
    async def har(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------------------
# The full-primitive capability-scoped provider
# --------------------------------------------------------------------------------------


@runtime_checkable
class BrowserProvider(Protocol):
    """The ONE full-primitive, capability-scoped browser handle.

    Bound to ONE ghost's session. Exposes the FULL surface names
    (session/nav/mouse/keyboard/page/extract/scrape/search/screenshot/live_frames) and
    NOTHING else — no os/sys/subprocess/socket/httpx/SDK member. ``FakeBrowserProvider``
    fulfils it in-memory (stages 1–2 + tests); ``GhostCrawlProvider`` fulfils it
    over the Python SDK; the raw-input impl lands later. All share this Protocol.
    """

    # --- namespaced primitive surfaces ------------------------------------------------
    @property
    def nav(self) -> NavApi: ...
    @property
    def mouse(self) -> MouseApi: ...
    @property
    def keyboard(self) -> KeyboardApi: ...
    @property
    def page(self) -> PageApi: ...

    # --- session lifecycle (bound to ONE session) -------------------------------------
    @property
    def session(self) -> BrowserSessionHandle | None: ...
    async def create_session(
        self, target: str, profile_name: str | None = None
    ) -> BrowserSessionHandle: ...
    async def open(self, target: str, profile: str | None = None) -> BrowserSessionHandle: ...
    async def release(self) -> None: ...

    # --- top-level work verbs ---------------------------------------------------------
    async def extract(self, schema: dict[str, Any]) -> dict[str, Any]: ...
    async def scrape(
        self, handle: BrowserSessionHandle, url: str, extract_schema: dict[str, Any] | None = None
    ) -> ScrapeResult: ...
    async def search(self, opts: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def screenshot(self) -> bytes: ...
    def live_frames(
        self, handle: BrowserSessionHandle, signal: Any
    ) -> AsyncIterator[str]: ...

    # --- the raw-input transport seam (declaration only; None until later) -----------
    @property
    def transport(self) -> CdpTransport | None: ...
