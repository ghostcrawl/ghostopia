"""Layer 1 (always-on): build the capability-scoped ``BehaviorContext``.

User/AI-authored behaviors run server-side driving real browsers with real GhostCrawl keys.
So a behavior must reach the outside world ONLY through a bounded context: its visual
:class:`GhostHandle`, its one-session :class:`BrowserProvider`, the read-only
:class:`WorldQuery`, the ``emit``/``log`` callbacks, its ``task``/``section``, and a seeded
``rng`` — and NOTHING else. No ``os``/``sys``/``socket``/``subprocess``/``httpx``/the raw SDK/
GhostCrawl keys are reachable from the returned context.

This builder is deliberately thin: it constructs the SAME ``BehaviorContext`` the behavior
contract documents — a plain dataclass whose OWN attributes are exactly the eight
bounded fields — with a freshly seeded :class:`random.Random`. Building the canonical type
(rather than a bespoke one) is what guarantees a future subprocess/isolate executor can
marshal the ctx across a process boundary UNCHANGED.

The context holds no back-reference to host globals: ``emit`` and ``log`` are the injected
callbacks (no ``print``/global reach), and ``rng`` is a per-context stream (never the process
global ``random`` module), so a behavior's decisions are deterministic under test.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from ghostopia_behaviors.behavior import BehaviorContext, Emit
from ghostopia_shared import GhostHandle, SectionRef, Task, WorldQuery

__all__ = ["build_capability_scoped_context"]


def _noop_log(_msg: str) -> None:
    return None


def _never_watched() -> bool:
    return False


def build_capability_scoped_context(
    *,
    ghost: GhostHandle,
    browser: Any,
    world: WorldQuery,
    emit: Emit,
    task: Task | None = None,
    section: SectionRef | None = None,
    seed: int = 0,
    log: Callable[[str], None] | None = None,
    watched: Callable[[], bool] | None = None,
) -> BehaviorContext:
    """Assemble the capability-scoped :class:`BehaviorContext` for one behavior run.

    Returns a context whose OWN attribute surface is exactly
    ``{ghost, browser, world, emit, task, section, rng, log}`` — no host/SDK/keys member is
    reachable. ``rng`` is a fresh ``random.Random(seed)`` (deterministic per ``seed`` and
    independent of the process-global RNG); ``emit``/``log`` are the injected callbacks.

    ``browser`` is typed ``Any`` here on purpose: the caller passes the SSRF-guarded handle
    from :func:`guard_browser_provider`, which is a structural ``BrowserProvider`` — keeping
    this seam pure (no concrete provider import).
    """
    return BehaviorContext(
        ghost=ghost,
        browser=browser,
        world=world,
        emit=emit,
        task=task,
        section=section,
        rng=random.Random(seed),
        log=log if log is not None else _noop_log,
        watched=watched if watched is not None else _never_watched,
    )
