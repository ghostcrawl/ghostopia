""": defense-in-depth security headers for the ghostopia server.

Pure-ASGI (only the ``http.response.start`` headers are touched, so WebSockets, SSE and
static file streaming are unaffected). ``setdefault`` semantics: a route-set header wins.

* every HTTP response: ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``,
  ``Referrer-Policy: no-referrer``;
* ``text/html`` (the Vite-built UI): :data:`HTML_CSP` — scraped data renders as React text,
  scraped image URLs are allowed only from ``https:``/``data:``/``blob:``;
* every other body (JSON API): a locked-down ``default-src 'none'`` CSP.

Kept local to ghostopia (harness independence: ghostopia never imports ghostcrawl.api).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTML_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "connect-src 'self' ws: wss: https: http://localhost:* http://127.0.0.1:*",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
JSON_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, html_csp: str = HTML_CSP, json_csp: str = JSON_CSP) -> None:
        self.app = app
        self._html_csp = html_csp
        self._json_csp = json_csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in STATIC_HEADERS.items():
                    headers.setdefault(k, v)
                is_html = headers.get("content-type", "").startswith("text/html")
                headers.setdefault("Content-Security-Policy", self._html_csp if is_html else self._json_csp)
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["HTML_CSP", "JSON_CSP", "SecurityHeadersMiddleware"]
