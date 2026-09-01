"""Filter web-search result urls down to VISITABLE destination pages.

Keyless web search (DuckDuckGo default) returns a mix of organic result urls AND
sponsored / tracking redirect urls — e.g. ``https://duckduckgo.com/y.js?ad_domain=...``
(an ad click-through), ``https://duckduckgo.com/l/?uddg=...`` (a DDG link redirector), or a
``https://www.bing.com/aclick?...`` sponsored redirect. Those are NOT real product/content
pages: navigating one lands on an ad-network redirect, so scraping it yields junk records
instead of the title/price a research department is after.

A human researching prices skips the ad links and clicks the organic results; this is the
same candidate-selection judgement, applied before a department spends a session visiting a
url. Choosing WHICH search results are worth visiting is squarely the consumer's call — the
search API faithfully returns what the engine served — exactly like the SSRF gate the
behaviors already apply to every candidate before opening a session.
"""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["is_visitable_result_url", "filter_visitable_urls"]

# Host + path fragments that identify a search-engine redirect / ad click-through rather than
# a real destination page. Matched case-insensitively against the url.
_REDIRECT_HOST_PATHS: tuple[tuple[str, str], ...] = (
    ("duckduckgo.com", "/y.js"),      # DDG sponsored / ad click-through
    ("duckduckgo.com", "/l/"),        # DDG organic link redirector (?uddg=<real-url>)
    ("bing.com", "/aclick"),          # Bing sponsored redirect
    ("bing.com", "/aclk"),            # Bing sponsored redirect (alt)
    ("google.com", "/aclk"),          # Google sponsored redirect
    ("google.com", "/url"),           # Google link redirector
)

# Query-param keys that only ever appear on ad / tracking redirect urls.
_AD_QUERY_MARKERS: tuple[str, ...] = ("ad_domain=", "ad_provider=", "click_metadata=")


def is_visitable_result_url(url: str) -> bool:
    """True when ``url`` is a real destination worth visiting, False for an ad/tracking redirect.

    Rejects a url that is not http(s), that matches a known search-engine redirect
    host+path, or that carries an ad/tracking query marker. Everything else — the organic
    results a research department actually wants — passes.
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if not host:
        return False
    for redirect_host, redirect_path in _REDIRECT_HOST_PATHS:
        if (host == redirect_host or host.endswith("." + redirect_host)) and path.startswith(
            redirect_path
        ):
            return False
    lowered = url.lower()
    return not any(marker in lowered for marker in _AD_QUERY_MARKERS)


def filter_visitable_urls(urls: list[str]) -> list[str]:
    """Keep only the visitable (non-ad-redirect) urls, order preserved."""
    return [u for u in urls if is_visitable_result_url(u)]
