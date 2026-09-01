"""The customer-surface banned lexicon + a single ``is_surface_safe`` predicate.

ghostopia is a public consumer of GhostCrawl. NOTHING a customer sees on the canvas / HUD /
inspector / dashboard should echo a raw, low-level status string a live session's provider output
could otherwise carry — an anti-bot vendor name or a networking/anti-detection term. Those are
collapsed to a curated, on-brand phrase instead. This module is the ONE authoritative definition
of the terms stripped from customer-facing copy. It is imported by:

* the server broadcast sanitizer (``gc_event_source`` / ``status_poll`` / ``ghost_pool``),
* the ghost-runtime curated vocabulary (``surface_vocab``), and
* the ``check_surface_language.sh`` gate (which allowlists THIS file as the definition site).

The set living here (and only here + its TS mirror) is intentional and allowlisted.
"""

from __future__ import annotations

import re

#: The banned customer-surface lexicon. Every entry is matched case-insensitively with a
#: word boundary (so it will not false-positive inside an unrelated longer word). Compound
#: tokens tolerate a space / hyphen / underscore separator.
BANNED_SURFACE_TERMS: frozenset[str] = frozenset(
    {
        # anti-bot / WAF vendor names a raw status could carry
        "datadome",
        "kasada",
        "perimeterx",
        "imperva",
        "akamai",
        "cloudflare",
        "waf",
        "anti-bot",
        "antibot",
        # low-level networking / anti-detection terms
        "proxy",
        "residential",
        "datacenter",
        "fingerprint",
        "stealth",
        "spoof",
        "bypass",
        "clearance",
        "cf_clearance",
        "captcha-bypass",
        # customer-surface language that must never label a session
        "human",
    }
)

#: The single detection regex. Built once from :data:`BANNED_SURFACE_TERMS`; compound tokens
#: (``anti-bot`` / ``cf_clearance`` / ``captcha-bypass`` / ``real human``) accept a
#: space / hyphen / underscore separator so ``anti bot`` / ``real-human`` are caught too.
_BANNED_REGEX = re.compile(
    r"\b(?:"
    r"datadome|kasada|perimeterx|imperva|akamai|cloudflare|waf|anti[\s_-]?bot|"
    r"proxy|residential|data[\s_-]?center|datacenter|fingerprint|stealth|spoof(?:ing|ed)?|"
    r"bypass|clearance|cf[\s_-]?clearance|captcha[\s_-]?bypass|"
    r"human|real[\s_-]?human"
    r")\b",
    re.IGNORECASE,
)


def is_surface_safe(text: object) -> bool:
    """Return ``True`` when ``text`` contains NONE of the banned lexicon.

    Non-string / empty input is treated as safe (there is nothing to leak). The check is
    case-insensitive + word-boundary anchored so ``"working…"`` is safe while
    ``"Resolving DataDome…"`` / ``"verify you are human"`` / ``"residential proxy"`` are not.
    """
    if not isinstance(text, str) or not text:
        return True
    return _BANNED_REGEX.search(text) is None


def banned_terms_in(text: object) -> list[str]:
    """Return the banned terms present in ``text`` (lowercased, de-duplicated, in order).

    Used by tests + the gate's diagnostics to report exactly WHICH term leaked. Empty when
    the text is surface-safe.
    """
    if not isinstance(text, str) or not text:
        return []
    seen: list[str] = []
    for m in _BANNED_REGEX.finditer(text):
        term = m.group(0).lower()
        if term not in seen:
            seen.append(term)
    return seen


__all__ = ["BANNED_SURFACE_TERMS", "banned_terms_in", "is_surface_safe"]
