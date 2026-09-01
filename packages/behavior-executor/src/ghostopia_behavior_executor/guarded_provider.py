"""SSRF gate ENFORCED ON the BrowserProvider handle.

ghostopia already validates a user-submitted mission target at submit time
(``validate_mission_url``). But a behavior drives navigation dynamically — it can ``goto`` a
URL it discovered mid-run, open a fresh session, or scrape a link. So the SSRF gate must
also sit AT the handle: every URL-taking entry point of the ``BrowserProvider`` passes an
injected validator BEFORE the wrapped provider is touched. A blocked URL never reaches the
real provider (and thus never reaches GhostCrawl); an allowed URL delegates unchanged.

The validator is INJECTED as ``is_url_allowed(url) -> bool | str`` so this package stays
pure — it never imports ``apps/server``'s ``ssrf`` module or the SDK. The server wires the
real gate (``validate_mission_url`` adapted to this shape); tests wire a fake. A validator
returning ``True`` allows; ANYTHING else (``False`` or a reason string) blocks, raising
:class:`SsrfBlockedUrlError` with the reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ghostopia_browser_provider import (
    BrowserProvider,
    BrowserSessionHandle,
    NavApi,
    ScrapeResult,
)

__all__ = ["SsrfBlockedUrlError", "UrlValidator", "guard_browser_provider"]

# ``True`` => allowed; ``False`` or a reason string => blocked.
UrlValidator = Any  # Callable[[str], bool | str]; kept loose so a fake/lambda fits.


class SsrfBlockedUrlError(ValueError):
    """Raised when a behavior-navigated URL is refused by the injected SSRF gate."""


def _reject(url: str, verdict: bool | str) -> None:
    """Raise unless ``verdict`` is exactly ``True`` (an allow)."""
    if verdict is True:
        return
    reason = verdict if isinstance(verdict, str) else "SSRF-blocked"
    raise SsrfBlockedUrlError(f"URL {url!r} rejected by SSRF gate: {reason}")


class _GuardedNav:
    """Wraps ``nav`` so ``goto`` validates before delegating; ``current_url`` passes through."""

    def __init__(self, inner: NavApi, is_url_allowed: UrlValidator) -> None:
        self._inner = inner
        self._is_url_allowed = is_url_allowed

    async def goto(self, url: str) -> None:
        _reject(url, self._is_url_allowed(url))
        await self._inner.goto(url)

    async def current_url(self) -> str:
        return await self._inner.current_url()


class _GuardedProvider:
    """A ``BrowserProvider`` decorator: every URL-taking entry crosses the SSRF gate first.

    URL-taking members guarded: ``nav.goto``, ``open``, ``create_session``, ``scrape``,
    ``scrape_rendered``, ``extract_products``. All other members (``mouse``/``keyboard``/``page``/
    ``session``/``extract``/``search``/``screenshot``/``live_frames``/``transport``/``release``)
    delegate straight through — they carry no navigable URL.

    NOTE: this decorator has NO ``__getattr__`` fallback — every method a behavior may call MUST be
    listed explicitly, or it silently disappears from the behavior's view of the provider. That is
    exactly what hid ``extract_products``/``scrape_rendered`` (the real-retail full-grid path) and
    made the advanced departments degrade to a keyless single-record read.
    """

    def __init__(self, inner: BrowserProvider, is_url_allowed: UrlValidator) -> None:
        self._inner = inner
        self._is_url_allowed = is_url_allowed
        self._nav = _GuardedNav(inner.nav, is_url_allowed)

    # --- guarded (URL-taking) entry points --------------------------------------------
    @property
    def nav(self) -> _GuardedNav:
        return self._nav

    async def open(self, target: str, profile: str | None = None) -> BrowserSessionHandle:
        _reject(target, self._is_url_allowed(target))
        return await self._inner.open(target, profile)

    async def create_session(
        self, target: str, profile_name: str | None = None
    ) -> BrowserSessionHandle:
        _reject(target, self._is_url_allowed(target))
        return await self._inner.create_session(target, profile_name)

    async def scrape(
        self,
        handle: BrowserSessionHandle,
        url: str,
        extract_schema: dict[str, Any] | None = None,
    ) -> ScrapeResult:
        _reject(url, self._is_url_allowed(url))
        return await self._inner.scrape(handle, url, extract_schema)

    async def scrape_rendered(
        self, handle: BrowserSessionHandle | None, url: str
    ) -> ScrapeResult:
        # URL-taking: guard before the keyless server scrape. Proxied so the pipeline's
        # session_extract path can reach the rendered read (it degrades to keyless scrape here
        # only if this is absent — the cause of the advanced departments falling to the keyless
        # single-record read instead of the full grid).
        _reject(url, self._is_url_allowed(url))
        return await self._inner.scrape_rendered(handle, url)

    async def extract_products(
        self,
        handle: BrowserSessionHandle | None,
        url: str,
        schema: dict[str, Any] | None = None,
    ) -> ScrapeResult:
        # URL-taking: guard before GhostCrawl's /v1/extract (native structured-data grid, or the
        # optional connected model). MUST be proxied — without it the pipeline's session_extract
        # path never reaches the full priced-grid extraction and degrades to a keyless read.
        _reject(url, self._is_url_allowed(url))
        return await self._inner.extract_products(handle, url, schema)

    # --- pass-through primitive sub-surfaces ------------------------------------------
    @property
    def mouse(self) -> Any:
        return self._inner.mouse

    @property
    def keyboard(self) -> Any:
        return self._inner.keyboard

    @property
    def page(self) -> Any:
        return self._inner.page

    @property
    def session(self) -> BrowserSessionHandle | None:
        return self._inner.session

    @property
    def transport(self) -> Any:
        return self._inner.transport

    # --- pass-through verbs (no navigable URL) ----------------------------------------
    async def release(self) -> None:
        await self._inner.release()

    async def extract(self, schema: dict[str, Any]) -> dict[str, Any]:
        return await self._inner.extract(schema)

    async def search(self, opts: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._inner.search(opts)

    async def screenshot(self) -> bytes:
        return await self._inner.screenshot()

    def live_frames(self, handle: BrowserSessionHandle, signal: Any) -> AsyncIterator[str]:
        return self._inner.live_frames(handle, signal)


def guard_browser_provider(
    provider: BrowserProvider, is_url_allowed: UrlValidator
) -> BrowserProvider:
    """Wrap ``provider`` so every navigated URL passes ``is_url_allowed`` before dispatch.

    Returns a structural ``BrowserProvider``: identical surface, but ``nav.goto`` / ``open`` /
    ``create_session`` / ``scrape`` reject an SSRF-blocked URL (``SsrfBlockedUrlError``) BEFORE
    the wrapped provider — never a silent pass. Every non-URL member delegates unchanged.
    """
    return _GuardedProvider(provider, is_url_allowed)
