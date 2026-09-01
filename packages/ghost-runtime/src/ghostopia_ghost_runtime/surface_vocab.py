"""The ONE curated customer vocabulary + the boundary sanitizer.

Every scrap of text the customer sees that is DERIVED from a live event — a ``browser.action``
``kind``, a ``browser.error`` ``code``, a ``say`` / ``status`` / ``reason`` / ``message`` —
passes through this module before it can reach the canvas / HUD / inspector. The contract:

* a KNOWN action-kind / error-code maps to a curated, on-brand spooky-friendly phrase;
* an UNKNOWN / raw value maps to a generic safe phrase — the raw string is NEVER surfaced;
* ANY free text containing a banned term (:mod:`ghostopia_shared.surface_safe`) collapses to
  the generic safe phrase — a banned term can never slip through.

This keeps a live session's raw internal status off the customer surface: the GhostDriver used
to echo it verbatim; now the extractors route every derived string through
:func:`sanitize_kind` / :func:`sanitize_code` / :func:`sanitize_text`.
"""

from __future__ import annotations

from ghostopia_shared.surface_safe import is_surface_safe

# Generic safe fall-backs — used for an UNKNOWN kind/code, or ANY text that carries a banned
# term. They convey the SITUATION generically (working / held) without any vendor/mechanics
# vocabulary, so the operator still knows a ghost is busy or stuck.
GENERIC_WORKING = "Working…"
GENERIC_HELD = "Held at the gate…"

#: The inspector activity-view line for an API-only ghost: a scrape/extract ghost has
#: no live browser frame, so the inspector shows this on-brand activity message instead of an
#: empty/misleading frame. Curated surface-safe (no vendor/mechanics vocabulary).
ACTIVITY_VIEW_MESSAGE = "Gathering data — no live view for this kind of work…"

#: The ONLY session-persona fields ghostopia may surface. Mirrors the provider
#: whitelist + the SDK accessor; any other key is ignored so a raw user-agent / engine
#: codename / fingerprint internal can never reach the customer surface.
PERSONA_WHITELIST_FIELDS = ("device_class", "os_class", "browser_class", "locale")

#: Known ``browser.action`` kinds → a curated bubble phrase. A kind not listed here maps to
#: :data:`GENERIC_WORKING` (never the raw kind), so a new/vendor-named action cannot leak.
ACTION_KIND_PHRASE: dict[str, str] = {
    "navigate": "Drifting to a page…",
    "search": "Peering around…",
    "read": "Reading the walls…",
    "scroll": "Scrolling through…",
    "extract": "Gathering whispers…",
    "scrape": "Gathering whispers…",
    "crawl": "Combing the halls…",
    "map": "Mapping the halls…",
    "click": "Reaching out…",
    "type": "Whispering keys…",
    "fill": "Whispering keys…",
    "wait": "Lingering a moment…",
    "hover": "Hovering close…",
    "screenshot": "Taking a likeness…",
    "draw": "Sketching on the board…",
    "draw_degraded": "Sketching on the board…",
    "verify": "Double-checking…",
    "process": "Sorting the findings…",
}

#: Known error / retry codes → a curated bubble phrase. A code not listed here maps to
#: :data:`GENERIC_HELD` (never the raw code), so a vendor-named challenge reads only as a
#: safe "held at the gate" phrase. Keyed on the SDK canonical catalog codes (error_map).
ERROR_CODE_PHRASE: dict[str, str] = {
    "captcha_required": "Held at a gate…",
    "blocked": "Turned away at the door…",
    "navigation_failed": "Lost the trail…",
    "target_http_error": "The page went cold…",
    "empty_content": "Found an empty room…",
    "rate_limited": "Told to slow down…",
    "quota_backend_unavailable": "Catching my breath…",
    "pool_exhausted": "Waiting for a free lantern…",
    "egress_integrity_failed": "The path faded out…",
    "render_hung": "Lost in the fog…",
    "render_timeout": "Faded into the fog…",
    "engine_crashed": "Tripped over a root…",
    "engine_timeout": "Faded into the fog…",
    "service_unavailable": "The house is quiet…",
    "internal_error": "A chill in the air…",
    "payment_required": "Out of candles…",
    "tier_unavailable": "Out of candles…",
    "bad_request": "That didn't work out…",
    "unauthorized": "No key for this door…",
    "forbidden": "The door is barred…",
    "not_found": "Nothing here…",
    "conflict": "Crossed paths…",
    "byo_proxy_invalid": "The road was closed…",
    "task_failed": "Couldn't finish this one…",
}


def sanitize_kind(kind: object) -> str:
    """A ``browser.action`` ``kind`` → its curated bubble phrase.

    A known kind → its curated phrase; anything else (unknown, raw, or banned-term-bearing)
    → :data:`GENERIC_WORKING`. The raw kind is NEVER echoed.
    """
    if isinstance(kind, str):
        phrase = ACTION_KIND_PHRASE.get(kind.strip().lower())
        if phrase is not None:
            return phrase
    return GENERIC_WORKING


def sanitize_code(code: object) -> str:
    """A ``browser.error`` / ``task.retry`` ``code`` → its curated bubble phrase.

    A known code → its curated phrase; anything else (unknown, raw, or banned-term-bearing)
    → :data:`GENERIC_HELD`. The raw code is NEVER echoed.
    """
    if isinstance(code, str):
        phrase = ERROR_CODE_PHRASE.get(code.strip().lower())
        if phrase is not None:
            return phrase
    return GENERIC_HELD


def sanitize_text(text: object, *, fallback: str = GENERIC_WORKING) -> str:
    """A free-text passthrough (``say`` / ``status`` / ``reason`` / ``message``) → safe text.

    Curated safe text is returned unchanged; empty or banned-term-bearing text collapses to
    ``fallback`` (default :data:`GENERIC_WORKING`). Use ``fallback=GENERIC_HELD`` for an
    error/attention context so an unknown reason still reads as "held at the gate".
    """
    if isinstance(text, str) and text.strip() and is_surface_safe(text):
        return text
    return fallback


def build_persona(fields: object) -> str | None:
    """Build a customer-safe persona sentence from the whitelist fields ONLY.

    ``"Browsing as a <browser-class> <device> on <os> · <locale>"``. Reads ONLY the
    :data:`PERSONA_WHITELIST_FIELDS` keys — an extra ``user_agent``/``engine`` key is
    ignored — then routes the sentence through :func:`sanitize_text` as the surface-language
    backstop. If a banned token slipped into a whitelist value (or the sentence is empty),
    returns ``None`` (omit rather than leak a fallback). This is the canonical builder the
    server-side inspector fan-out uses.
    """
    if not isinstance(fields, dict):
        return None
    browser = str(fields.get("browser_class") or "").strip()
    device = str(fields.get("device_class") or "").strip()
    os_name = str(fields.get("os_class") or "").strip()
    locale = str(fields.get("locale") or "").strip()
    lead = " ".join(part for part in (browser, device) if part)
    parts: list[str] = []
    if lead:
        parts.append(f"Browsing as a {lead}")
    if os_name:
        parts.append(("on " + os_name) if parts else os_name)
    sentence = " ".join(parts).strip()
    if locale and sentence:
        sentence = f"{sentence} · {locale}"
    elif locale:
        sentence = locale
    if not sentence:
        return None
    safe = sanitize_text(sentence, fallback="")
    return safe or None


__all__ = [
    "ACTION_KIND_PHRASE",
    "ACTIVITY_VIEW_MESSAGE",
    "ERROR_CODE_PHRASE",
    "GENERIC_HELD",
    "GENERIC_WORKING",
    "PERSONA_WHITELIST_FIELDS",
    "build_persona",
    "sanitize_code",
    "sanitize_kind",
    "sanitize_text",
]
