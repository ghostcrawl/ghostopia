"""The REUSABLE ``BrowserProvider`` contract suite.

``describe_provider_contract(make_provider)`` exercises the FULL provider surface and is
consumed by:
- ``tests/test_provider_contract.py`` here (against ``FakeBrowserProvider``), and
- the ``GhostCrawlProvider`` + the CDP-WS full-primitive impl,

so every provider proves the same behavior with ONE set of assertions — no drift between
the fake and the real handle. ``make_provider`` is a zero-arg factory returning a FRESH
provider instance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ghostopia_shared import Button, Point

from ghostopia_browser_provider.provider import (
    BrowserProvider,
    BrowserSessionHandle,
    ScrapeResult,
)

# Names a capability-scoped browser handle must NOT expose: no host
# access, no raw sockets/HTTP, no SDK client. Only the bounded browser API is reachable.
_FORBIDDEN_MEMBERS = ("os", "sys", "subprocess", "socket", "httpx", "ghostcrawl", "client", "sdk")


async def describe_provider_contract(make_provider: Callable[[], BrowserProvider]) -> None:
    """Run the full-surface contract against a fresh provider from ``make_provider``."""
    provider = make_provider()

    # --- structural: it IS a BrowserProvider -----------------------------------------
    assert isinstance(provider, BrowserProvider)

    # --- capability scoping: no host/SDK escape --------------------------------------
    for banned in _FORBIDDEN_MEMBERS:
        assert not hasattr(provider, banned), f"capability escape: exposes {banned!r}"

    # --- session lifecycle: bound to ONE session -------------------------------------
    assert provider.session is None
    handle = await provider.open("https://example.com", profile="milo")
    assert isinstance(handle, BrowserSessionHandle)
    assert handle.session_id and handle.engine and handle.target == "https://example.com"
    assert provider.session is not None
    assert provider.session.session_id == handle.session_id

    # --- nav -------------------------------------------------------------------------
    await provider.nav.goto("https://example.com/pricing")
    assert await provider.nav.current_url() == "https://example.com/pricing"

    # --- mouse: a drag records down -> moves -> up -----------------------------------
    if hasattr(provider, "input_log"):
        await provider.mouse.drag(Point(x=0, y=0), Point(x=32, y=32), button=Button.LEFT)
        log = provider.input_log
        assert log[0][0] == "down", "a drag must begin with a press"
        assert log[-1][0] == "up", "a drag must end with a release"
        assert any(e[0] == "move" for e in log[1:-1]), "a drag must glide between down/up"
        # click and hold are also expressible
        await provider.mouse.click(Point(x=5, y=5))
        await provider.mouse.hold(Point(x=9, y=9))

    # --- keyboard --------------------------------------------------------------------
    await provider.keyboard.type("hello")
    await provider.keyboard.press("Enter")

    # --- page primitives -------------------------------------------------------------
    assert await provider.page.eval("document.title") is not None
    await provider.page.scroll(0, 400)
    assert isinstance(await provider.page.dom_snapshot(), dict)
    assert await provider.page.wait_for("networkidle") is True
    assert isinstance(await provider.page.cookies(), list)
    await provider.page.upload("#file", "/tmp/x")
    assert isinstance(await provider.page.download("https://example.com/x.csv"), bytes)
    assert isinstance(await provider.page.har(), dict)

    # --- extract / scrape / search / screenshot --------------------------------------
    extracted = await provider.extract({"title": "str", "price": "str"})
    assert set(extracted) == {"title", "price"}

    result = await provider.scrape(handle, "https://example.com", extract_schema={"title": "str"})
    assert isinstance(result, ScrapeResult)
    assert result.records
    assert result.discovered_urls is not None  # NavigateAndExtract consumes these

    search_results = await provider.search({"query": "saas pricing"})
    assert isinstance(search_results, list) and search_results

    assert isinstance(await provider.screenshot(), bytes)

    # --- live_frames: yields then terminates on the cancel signal --------------------
    signal = asyncio.Event()
    frames: list[str] = []
    async for frame_ref in provider.live_frames(handle, signal):
        assert isinstance(frame_ref, str)
        frames.append(frame_ref)
        if len(frames) >= 2:
            signal.set()  # cancel — the generator must stop
    assert len(frames) >= 2

    # --- the CDP-WS transport seam is declared (impl arrives later) --------------
    # ``transport`` may be None on the fake; the property must exist on the seam.
    assert hasattr(provider, "transport")

    # --- release invalidates the bound session ---------------------------------------
    await provider.release()
    assert provider.session is None


async def describe_load_bearing_contract(make_provider: Callable[[], BrowserProvider]) -> None:
    """Run the LOAD-BEARING subset of the contract against a fresh provider.

    The load-bearing surface is the session/scrape/current_url/live_frames/release core
    that ``GhostCrawlProvider`` wires over the real SDK. The remaining
    full-primitive members (mouse/keyboard/page.*/extract/search/screenshot + the
    CDP-WS relay) are exercised by :func:`describe_provider_contract` once they land
    — this subset is the same shared helper, minus the deferred members, so a
    real provider proves the load-bearing behavior with the SAME assertions the Fake does.
    """
    provider = make_provider()

    # --- structural: it IS a BrowserProvider (all Protocol members present) -----------
    assert isinstance(provider, BrowserProvider)

    # --- capability scoping: no host/SDK escape -------------------------
    for banned in _FORBIDDEN_MEMBERS:
        assert not hasattr(provider, banned), f"capability escape: exposes {banned!r}"

    # --- session lifecycle: bound to ONE session, handle carries engine --------------
    assert provider.session is None
    handle = await provider.open("https://example.com", profile="milo")
    assert isinstance(handle, BrowserSessionHandle)
    assert handle.session_id and handle.engine and handle.target == "https://example.com"
    assert provider.session is not None
    assert provider.session.session_id == handle.session_id

    # --- nav: goto + current_url over the SDK ----------------------------------------
    await provider.nav.goto("https://example.com/pricing")
    assert await provider.nav.current_url() == "https://example.com/pricing"

    # --- scrape returns records ------------------------------------------------------
    result = await provider.scrape(handle, "https://example.com", extract_schema={"title": "str"})
    assert isinstance(result, ScrapeResult)
    assert result.records

    # --- live_frames: yields then terminates on the cancel signal --------------------
    signal = asyncio.Event()
    frames: list[str] = []
    async for frame_ref in provider.live_frames(handle, signal):
        assert isinstance(frame_ref, str)
        frames.append(frame_ref)
        if len(frames) >= 2:
            signal.set()  # cancel — the generator must stop
    assert len(frames) >= 2

    # --- the CDP-WS transport seam is declared (impl arrives later) --------------
    assert hasattr(provider, "transport")

    # --- release invalidates the bound session ---------------------------------------
    await provider.release()
    assert provider.session is None
