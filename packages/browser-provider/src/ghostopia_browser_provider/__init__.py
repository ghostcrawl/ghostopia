"""ghostopia-browser-provider — the full-primitive, capability-scoped browser seam.

Defines the ONE ``BrowserProvider`` Protocol the whole world depends on, an in-memory
``FakeBrowserProvider`` that fulfils it with zero GhostCrawl calls,
and a REUSABLE ``describe_provider_contract`` suite. The concrete ``GhostCrawlProvider``
and the raw CDP-WS full-primitive impl fulfil the same Protocol behind
this seam — no SDK import lives here.
"""

from __future__ import annotations

from ghostopia_browser_provider.contract import describe_provider_contract
from ghostopia_browser_provider.fake_provider import FakeBrowserProvider
from ghostopia_browser_provider.provider import (
    BrowserProvider,
    BrowserSessionHandle,
    CdpTransport,
    KeyboardApi,
    MouseApi,
    NavApi,
    PageApi,
    ScrapeResult,
)

__all__ = [
    "BrowserProvider",
    "BrowserSessionHandle",
    "CdpTransport",
    "FakeBrowserProvider",
    "KeyboardApi",
    "MouseApi",
    "NavApi",
    "PageApi",
    "ScrapeResult",
    "describe_provider_contract",
]
