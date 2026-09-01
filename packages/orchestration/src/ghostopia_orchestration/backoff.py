"""Retry-After-aware backoff scheduler (no API hammering).

GhostCrawl is proxy-exit-count-bound and governor-gated: a ``429`` / ``pool_exhausted`` /
retryable ``5xx`` carries a ``retry_after`` the SDK error catalog advertises (see
``ghostopia_ghostcrawl_provider.error_map`` / ``ghostcrawl.errors.ERROR_CODES``). The
WorkQueue re-enqueues a retryable task only AFTER at least that many seconds, so the next
attempt never spin-hammers the API.

:func:`compute_backoff` returns the delay (seconds) before the next attempt: an exponential
term (capped) with the SDK ``retry_after`` as a HARD FLOOR, plus a small capped jitter to
de-synchronise a fanned-out batch. The ``retry_after`` floor is honoured exactly — the
returned delay is always ``>= retry_after`` (never invented shorter).
"""

from __future__ import annotations

import random
from collections.abc import Callable

__all__ = ["compute_backoff"]


def compute_backoff(
    attempt: int,
    retry_after: int | float | None,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.5,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay (seconds) before re-attempting a retryable task.

    * ``attempt`` — the 1-based attempt number just completed (1 after the first failure).
    * ``retry_after`` — the SDK-advertised cooldown floor; the result is never below it.
    * ``base``/``cap`` — the exponential term is ``base * 2**(attempt-1)`` capped at ``cap``.
    * ``jitter`` — a capped uniform jitter (``rng() * jitter``) added on top to spread a batch.

    The exponential term is capped FIRST, then the ``retry_after`` floor is applied, so a
    large advertised cooldown always wins (no hammering). Jitter is additive and small.
    """
    exp = base * (2 ** max(0, attempt - 1))
    exp = min(exp, cap)
    floor = float(retry_after) if retry_after is not None else 0.0
    delay = max(exp, floor)
    return delay + rng() * jitter
