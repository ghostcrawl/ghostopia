"""Bounded, ``retry_after``-aware backoff for opening a GhostCrawl session.

A ``sessions.create`` can come back as a RETRYABLE rate-limit — the account's concurrent
live-session cap ("You've reached your plan's concurrent live-session limit") — surfaced by
the provider as a raised error carrying ``retryable`` / ``retry_after`` (on ``err.mapped``,
the ghostcrawl-provider ``ProviderCallError``). At the concurrency edge a queued workforce /
pool ghost should WAIT and retry rather than die to ERROR, so its department still delivers
its finds. This helper classifies such an error and computes a non-negative backoff (seconds)
honouring the ``retry_after`` floor, a capped exponential term, and a small jitter.

It is dependency-free ON PURPOSE (no ``ghostcrawl`` / ``ghostcrawl-provider`` import): the
behaviors package stays decoupled from the SDK, so the error is inspected purely by attribute
(duck-typed). This mirrors ``ghostopia_orchestration.backoff.compute_backoff`` without pulling
the orchestration → ghostcrawl-provider dependency chain into behaviors.
"""

from __future__ import annotations

import random
from collections.abc import Callable

__all__ = ["MAX_SESSION_ATTEMPTS", "retry_after_floor", "session_backoff"]

#: Cap on ``sessions.create`` attempts before a retryable rate-limit degrades the ghost to
#: sessionless scraping. Kept LOW (3): the flagship account's per-tier CONCURRENT-live-session
#: cap is small (e.g. growth = 2) and independent of ``max_concurrency``, so a workforce running
#: more department ghosts than that cap has its over-cap ghosts hit a PERSISTENT (not transient)
#: live-session limit — they must fall to productive sessionless scraping FAST (~2 short retries,
#: ~10s) instead of spin-hammering ``sessions.create`` through a long backoff (the 429 storm that
#: left the Data Graveyard slow/empty). A genuine transient blip still recovers within the 3
#: attempts; a hard cap degrades quickly so every ghost delivers real finds. See the workforce
#: cap note in ``workforce.py`` (the cap SHOULD track the live-session limit, not max_concurrency).
MAX_SESSION_ATTEMPTS = 3


def retry_after_floor(err: BaseException) -> float | None:
    """The retry backoff floor (seconds) when ``err`` is a RETRYABLE provider error, else ``None``.

    Duck-typed: a provider error carries a ``mapped`` object with ``retryable`` /
    ``retry_after`` (the ghostcrawl-provider ``ProviderCallError``), or exposes those attrs
    directly. A non-retryable error (or a plain exception) returns ``None`` so the caller
    re-raises and the ghost takes its normal ERROR path — this helper only ever asks the caller
    to WAIT on an explicitly retryable failure. A retryable error with no advertised
    ``retry_after`` yields a ``0.0`` floor (the exponential term still applies)."""
    carrier = getattr(err, "mapped", err)
    retryable = getattr(carrier, "retryable", None)
    if retryable is not True:
        return None
    retry_after = getattr(carrier, "retry_after", None)
    if retry_after is None:
        return 0.0
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return 0.0


def session_backoff(
    attempt: int,
    retry_after: float | int | None,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.5,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay (seconds) before the next ``sessions.create`` attempt.

    The exponential term ``base * 2**(attempt-1)`` is capped at ``cap`` FIRST, then the
    ``retry_after`` floor is applied (a large advertised cooldown always wins — no hammering),
    then a small additive jitter de-synchronises a fanned-out wave. Always ``>= retry_after``.
    """
    exp = min(base * (2 ** max(0, attempt - 1)), cap)
    floor = float(retry_after) if retry_after is not None else 0.0
    return max(exp, floor) + rng() * jitter
