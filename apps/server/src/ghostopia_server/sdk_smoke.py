"""ghostcrawl SDK smoke — proves the in-repo editable ghostcrawl (2.3.6) surface.

Constructs a client with an explicit ``base_url`` + dummy ``token`` and touches the
surface ghostopia's backend depends on. NO network call is issued: construction and
attribute access only (the sub-clients are lazy properties; ``scrape`` is a bound
method). This is the server-side proof that the harness talks to GhostCrawl through
the Python SDK, and that the editable link resolves the 2.3.6 surface (recordings.
visual / cdp) rather than an older PyPI build.
"""

from __future__ import annotations

from typing import Any

import ghostcrawl
from ghostcrawl import AsyncGhostCrawl, GhostCrawl

# Any harmless non-routable base_url + placeholder token. Construction does not open
# a connection, so no traffic leaves the process.
SMOKE_BASE_URL = "http://ghostcrawl.invalid:8080"
SMOKE_TOKEN = "gck_live_ghostopia_smoke_dummy"  # noqa: S105 - not a real secret


def build_async_client() -> AsyncGhostCrawl:
    """Construct the async client ghostopia's async server will actually use."""
    return AsyncGhostCrawl(token=SMOKE_TOKEN, base_url=SMOKE_BASE_URL)


def build_sync_client() -> GhostCrawl:
    """Construct the sync client (used by scripts / synchronous call sites)."""
    return GhostCrawl(token=SMOKE_TOKEN, base_url=SMOKE_BASE_URL)


def probe_surface(client: AsyncGhostCrawl) -> dict[str, Any]:
    """Touch the SDK surface the harness relies on — no network I/O.

    Returns a small map proving each affordance resolved AND is callable, so
    callers (and the smoke test) can assert the 2.3.6 surface is present.
    Presence alone is not enough: the facade methods (page.mouse /
    page.arrival) lazily import Kiota-generated body models — a wheel built
    before the regen ships the methods but they raise ModuleNotFoundError at
    call time (caught 2026-09-24). The generated_models keys prove the bodies
    import — the CALLABILITY half of the contract.
    """
    # The bodies (lazy imports inside the facade methods). ImportError here
    # means the vendored wheel predates the Kiota regen — the facade methods
    # exist but every call would fail.
    from ghostcrawl._generated.models.arrival_body import ArrivalBody  # noqa: F401
    from ghostcrawl._generated.models.mouse_body import MouseBody  # noqa: F401

    surface: dict[str, Any] = {
        "version": ghostcrawl.__version__,
        # Live view of a session (selected-ghost inspector).
        "recordings_visual": client.recordings.visual,
        # Raw CDP relay URL / input (mouse + keyboard behaviors).
        "cdp": client.cdp,
        # Session lifecycle (a ghost opening/closing a browser).
        "sessions": client.sessions,
        # In-page primitives (eval / scroll / dom snapshot / wait).
        "page": client.page,
        # Engine-agnostic in-page actions (page.mouse ops + page.arrival,
        #). Additive to the cdp.input drive seam. Callable: the
        # generated body models imported above.
        "page_mouse": getattr(client.page, "mouse", None),
        "page_arrival": getattr(client.page, "arrival", None),
        "generated_models": {"mouse_body": MouseBody, "arrival_body": ArrivalBody},
        # Identity selection for multi-identity swarms.
        "profiles": client.profiles,
        # One-shot extraction + search (deterministic task-runner behaviors).
        "scrape": client.scrape,
        "search": client.search,
    }
    return surface
