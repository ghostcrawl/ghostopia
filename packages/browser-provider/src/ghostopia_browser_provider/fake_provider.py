"""``FakeBrowserProvider`` — an in-memory fulfilment of the full ``BrowserProvider``.

Zero GhostCrawl calls, zero SDK import. It powers the simulated stages 1–2 (the renderer cannot tell simulated from real) and every contract/unit test.
It records an ordered ``input_log`` for mouse/keyboard (so a drag is provably
down→moves→up) and yields synthetic live-view frame refs from an async generator that
stops on the cancel signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ghostopia_shared import Button, Point

from ghostopia_browser_provider.provider import (
    BrowserSessionHandle,
    CdpTransport,
    ScrapeResult,
)

# One (op, *args) entry per recorded input action. ``op`` ∈ {move,down,up,click,type,press}.
InputLogEntry = tuple[str, Any]


class _FakeNav:
    def __init__(self, owner: FakeBrowserProvider) -> None:
        self._owner = owner
        self._url = "about:blank"

    async def goto(self, url: str) -> None:
        self._owner._require_session()
        self._url = url

    async def current_url(self) -> str:
        return self._url


class _FakeMouse:
    def __init__(self, owner: FakeBrowserProvider) -> None:
        self._owner = owner

    async def move(self, to: Point) -> None:
        self._owner._log.append(("move", to))

    async def down(self, button: Button = Button.LEFT) -> None:
        self._owner._log.append(("down", button))

    async def up(self, button: Button = Button.LEFT) -> None:
        self._owner._log.append(("up", button))

    async def click(self, at: Point, button: Button = Button.LEFT) -> None:
        await self.move(at)
        await self.down(button)
        await self.up(button)

    async def drag(self, frm: Point, to: Point, button: Button = Button.LEFT) -> None:
        # A held stroke, observable as down→moves→up (the whiteboard recipe). Press at the
        # start point, glide through an intermediate point to the end, then release.
        self._owner._log.append(("down", frm))
        mid = Point(x=(frm.x + to.x) / 2, y=(frm.y + to.y) / 2)
        await self.move(mid)
        await self.move(to)
        await self.up(button)

    async def hold(self, at: Point, button: Button = Button.LEFT) -> None:
        # Press and HOLD (no up) — the stroke stays down until a later up/drag.
        await self.move(at)
        await self.down(button)


class _FakeKeyboard:
    def __init__(self, owner: FakeBrowserProvider) -> None:
        self._owner = owner

    async def type(self, text: str) -> None:
        self._owner._log.append(("type", text))

    async def press(self, key: str) -> None:
        self._owner._log.append(("press", key))


class _FakePage:
    def __init__(self, owner: FakeBrowserProvider) -> None:
        self._owner = owner

    async def eval(self, expr: str) -> Any:
        return f"fake-eval:{expr}"

    async def scroll(self, dx: float = 0.0, dy: float = 0.0) -> None:
        self._owner._log.append(("scroll", (dx, dy)))

    async def dom_snapshot(self) -> dict[str, Any]:
        return {"nodes": [], "url": await self._owner.nav.current_url()}

    async def wait_for(self, condition: str, *, timeout_ms: float = 30_000.0) -> bool:
        return True

    async def cookies(self) -> list[dict[str, Any]]:
        return [{"name": "fake", "value": "1"}]

    async def upload(self, selector: str, path: str) -> None:
        self._owner._log.append(("upload", (selector, path)))

    async def download(self, url: str) -> bytes:
        return b"fake-download"

    async def har(self) -> dict[str, Any]:
        return {"log": {"entries": []}}


class FakeBrowserProvider:
    """In-memory ``BrowserProvider`` — bound to one synthetic session, no SDK, no keys."""

    def __init__(self, *, engine: str = "chromium", max_frames: int = 5) -> None:
        self._engine = engine
        self._max_frames = max_frames
        self._session: BrowserSessionHandle | None = None
        self._seq = 0
        self._log: list[InputLogEntry] = []
        self._nav = _FakeNav(self)
        self._mouse = _FakeMouse(self)
        self._keyboard = _FakeKeyboard(self)
        self._page = _FakePage(self)

    # --- namespaced primitive surfaces ------------------------------------------------
    @property
    def nav(self) -> _FakeNav:
        return self._nav

    @property
    def mouse(self) -> _FakeMouse:
        return self._mouse

    @property
    def keyboard(self) -> _FakeKeyboard:
        return self._keyboard

    @property
    def page(self) -> _FakePage:
        return self._page

    @property
    def input_log(self) -> list[InputLogEntry]:
        """Ordered mouse/keyboard actions (a drag reads down→moves→up)."""
        return self._log

    # --- session lifecycle ------------------------------------------------------------
    @property
    def session(self) -> BrowserSessionHandle | None:
        return self._session

    async def create_session(
        self, target: str, profile_name: str | None = None
    ) -> BrowserSessionHandle:
        self._seq += 1
        self._session = BrowserSessionHandle(
            session_id=f"fake-sess-{self._seq}", target=target, engine=self._engine
        )
        return self._session

    async def open(self, target: str, profile: str | None = None) -> BrowserSessionHandle:
        # ``open`` is the ergonomic alias for ``create_session`` (profile == profile_name).
        return await self.create_session(target, profile_name=profile)

    async def release(self) -> None:
        self._session = None

    def _require_session(self) -> BrowserSessionHandle:
        if self._session is None:
            raise RuntimeError("no bound session — call open()/create_session() first")
        return self._session

    # --- top-level work verbs ---------------------------------------------------------
    async def extract(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {key: f"fake-{key}" for key in schema}

    async def scrape(
        self,
        handle: BrowserSessionHandle,
        url: str,
        extract_schema: dict[str, Any] | None = None,
    ) -> ScrapeResult:
        record = (
            {key: f"fake-{key}" for key in extract_schema}
            if extract_schema
            else {"url": url, "title": "fake-title"}
        )
        return ScrapeResult(
            records=[record],
            discovered_urls=[f"{url}/a", f"{url}/b"],
        )

    async def scrape_rendered(
        self, handle: BrowserSessionHandle | None, url: str
    ) -> ScrapeResult:
        # The in-session (CAPTCHA-solved) read — the fake returns the same shape as ``scrape`` so
        # a session_extract behavior exercises the identical downstream path.
        return ScrapeResult(
            records=[{"url": url, "title": "fake-rendered"}],
            discovered_urls=[f"{url}/a", f"{url}/b"],
        )

    async def search(self, opts: dict[str, Any]) -> list[dict[str, Any]]:
        query = opts.get("query", "")
        return [{"url": f"https://result-{i}.example", "title": f"{query} #{i}"} for i in range(3)]

    async def screenshot(self) -> bytes:
        return b"fake-png-bytes"

    async def live_frames(
        self, handle: BrowserSessionHandle, signal: Any
    ) -> AsyncIterator[str]:
        i = 0
        while i < self._max_frames and not signal.is_set():
            yield f"fake-frame://{handle.session_id}/{i}"
            i += 1
            # Yield control so a consumer can set the cancel signal between frames.
            await asyncio.sleep(0)

    # --- the raw-input transport seam (None until the real driver) -------------
    @property
    def transport(self) -> CdpTransport | None:
        return None
