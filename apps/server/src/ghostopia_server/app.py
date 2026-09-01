"""FastAPI application factory — the ghostopia server HTTP + WS host.

``create_app`` builds the FastAPI app the operator runs: a liveness ``/healthz`` probe,
the authenticated WS gateway (``/ws``, JWT handshake + validated fan-out), and a hook to
mount the built TS frontend as static files. The HS256 signing secret is read SERVER-SIDE
(env/``pass``) via ``auth.get_jwt_secret`` when not supplied — never shipped to the client.

The SDK + every GhostCrawl credential live only on this server; the frontend talks
to it exclusively over the authed WS.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .auth import get_jwt_secret, mint_token
from .ws_gateway import start_ws_gateway


def _web_dist_dir() -> Path | None:
    """Resolve the built web UI directory (``apps/web/dist``), or ``None`` if unbuilt.

    ``GHOSTOPIA_WEB_DIST`` overrides the location (used by tests + non-default layouts);
    otherwise the default is ``apps/web/dist`` relative to this source tree. ``make run``
    builds that directory before boot, so the ONE server port serves both UI + API. When no
    build exists (a fresh clone that hasn't run ``make run`` yet) this returns ``None`` and the
    app simply serves its API — it never errors at boot.
    """
    override = os.environ.get("GHOSTOPIA_WEB_DIST")
    if override:
        dist = Path(override)
    else:
        # app.py → ghostopia_server → src → server → apps → <ghostopia root>
        root = Path(__file__).resolve().parents[4]
        dist = root / "apps" / "web" / "dist"
    return dist if (dist / "index.html").is_file() else None


def mount_web_ui(app: FastAPI) -> None:
    """Mount the built web UI as static files on the SAME app (one port).

    Mounted at ``/`` with ``html=True`` (SPA index fallback). The SPA is a greedy catch-all, so
    it MUST be the last route — the server's own endpoints (``/healthz``, ``/token``, ``/ws``,
    and any ``/tasks``/``/missions`` routes a wrapping factory adds) have to match first.

    This is idempotent + self-repositioning: it drops any prior ``web`` mount and re-appends a
    fresh one at the END of the route table. So a factory that wraps :func:`create_app` and adds
    more API routes afterwards (e.g. :func:`ghostopia_server.gc_event_source.create_live_app`)
    just calls this again as its final step, and the SPA catch-all lands last — never shadowing
    the routes registered in between. When there is no build it is a no-op (the API still serves).
    """
    dist = _web_dist_dir()
    if dist is None:
        return
    # Drop a prior web mount so a re-call re-appends the catch-all LAST (after later routes).
    app.router.routes = [r for r in app.router.routes if getattr(r, "name", None) != "web"]
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")


def create_app(*, secret: str | None = None) -> FastAPI:
    """Build the ghostopia server app.

    ``secret`` overrides the env-sourced JWT secret (tests pass one explicitly); in
    production it is ``None`` and resolved from ``GHOSTOPIA_JWT_SECRET`` — a missing
    secret raises rather than falling back to a weak default.
    """
    resolved_secret = secret if secret is not None else get_jwt_secret()

    app = FastAPI(title="ghostopia-server", version="0.0.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/token")
    async def token() -> dict[str, str]:
        """Mint a short-lived operator token so the UI authenticates against THIS backend.

        ghostopia is single-operator, local-first: the signing secret lives server-side,
        and the frontend needs a token to open the authed WS. A fresh tab (or a tab whose baked
        token predates a restart) GETs this route for a freshly-minted token, then opens ``/ws``
        with it. This is the real self-hosted-backend auth path — it replaces the ``the earlier token``
        endpoint the frontend referenced but that was never served.
        """
        return {"token": mint_token(subject="operator", secret=resolved_secret)}

    start_ws_gateway(app, secret=resolved_secret)

    # Serve the built web UI from THIS app so a single port hosts UI + API. Mounted last so
    # the API routes above (and /ws) take precedence over the SPA catch-all. A wrapping factory
    # that adds more routes re-calls mount_web_ui as its final step to keep the catch-all last.
    mount_web_ui(app)
    return app
