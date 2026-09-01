"""SDK error -> normalized ghostopia event + backoff hint.

The GhostCrawl SDK raises typed errors (``ghostcrawl.facade.GhostCrawlError`` and
subclasses; also result-channel ``ScrapeError``) carrying ``code`` / ``retryable`` /
``retry_after`` / ``status_code`` — the two-channel catalog in
``ghostcrawl.errors.codes``. ``map_sdk_error`` normalizes any such error into a
:class:`MappedError` the orchestrator turns into a ``browser.error`` / ``task.retry``
event (``EVENT_PROTOCOL.md``) with a ghost visual and a backoff hint.

The mapping is 1:1 with the GhostCrawl error codes:
a retryable failure becomes a ``task.retry`` (WAITING/RETRYING honouring
``retry_after``); a non-retryable failure becomes a ``browser.error`` (ghost ERROR ->
RETURNING_HOME -> Error Graveyard). Values (``retryable`` / ``retry_after``) come from
the error itself, falling back to the SDK's own canonical catalog — never invented here.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghostcrawl.errors import ERROR_CODES

# --------------------------------------------------------------------------------------
# code -> ghost visual (the GhostCrawl error codes)
# --------------------------------------------------------------------------------------
#: Every canonical catalog code maps to a concrete ghost visual. Codes not listed here
#: fall through to ``_GENERIC_VISUAL`` so a new/unknown code still renders *something*.
_VISUAL_BY_CODE: dict[str, str] = {
    # result channel (TARGET failed on a 200)
    "captcha_required": "captcha",
    "blocked": "blocked",
    "navigation_failed": "error_grave",
    "target_http_error": "error_grave",
    "empty_content": "no_content",
    # problem channel (OUR failure, non-2xx)
    "rate_limited": "cooldown",
    "quota_backend_unavailable": "cooldown",
    "pool_exhausted": "cooldown",
    "egress_integrity_failed": "timeout",
    "render_hung": "timeout",
    "engine_crashed": "timeout",
    "render_timeout": "timeout",
    "engine_timeout": "timeout",
    "service_unavailable": "timeout",
    "internal_error": "error_grave",
    "payment_required": "quota",
    "bad_request": "error_grave",
    "unauthorized": "error_grave",
    "forbidden": "error_grave",
    "not_found": "error_grave",
    "conflict": "error_grave",
    "byo_proxy_invalid": "error_grave",
    "tier_unavailable": "quota",
}

#: The visual for a code the catalog does not name (defensive; keeps the ghost renderable).
_GENERIC_VISUAL = "error_grave"


@dataclass(frozen=True)
class MappedError:
    """A normalized, render-ready view of an SDK error.

    ``event_type`` is the normalized envelope type the orchestrator emits:
    ``"task.retry"`` for a retryable failure (backoff scheduled), ``"browser.error"``
    for a terminal one. ``retry_after`` is the backoff hint in seconds (``None`` when
    the catalog advertises none). ``visual`` is the ghost overlay the renderer draws.
    """

    code: str | None
    visual: str
    event_type: str
    retryable: bool
    retry_after: int | None


def _catalog_field(code: str | None, attr: str) -> object | None:
    entry = ERROR_CODES.get(code or "")
    return getattr(entry, attr) if entry is not None else None


def map_sdk_error(err: object) -> MappedError:
    """Normalize a GhostCrawl SDK error into a :class:`MappedError`.

    Reads ``code`` / ``retryable`` / ``retry_after`` off the error (duck-typed so a
    thin test stub works too), falling back to the SDK's canonical catalog for any
    field the error instance did not carry. Never logs or embeds a token/secret.
    """
    code = getattr(err, "code", None)

    retryable = getattr(err, "retryable", None)
    if retryable is None:
        retryable = bool(_catalog_field(code, "retryable"))

    retry_after = getattr(err, "retry_after", None)
    if retry_after is None:
        retry_after = _catalog_field(code, "retry_after")

    visual = _VISUAL_BY_CODE.get(code or "", _GENERIC_VISUAL)
    event_type = "task.retry" if retryable else "browser.error"

    return MappedError(
        code=code,
        visual=visual,
        event_type=event_type,
        retryable=bool(retryable),
        retry_after=retry_after if isinstance(retry_after, int) else None,
    )


__all__ = ["MappedError", "map_sdk_error"]
