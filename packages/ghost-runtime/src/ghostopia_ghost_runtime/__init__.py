"""ghostopia-ghost-runtime — the coarse lifecycle brain-stem.

Exports the pure lifecycle reducer (:func:`reduce` over :data:`transitions`), the concrete
:func:`create_ghost_handle` (the narrow command surface behaviors + the driver speak), and
the :class:`GhostDriver` that translates normalized events into server-authoritative
PRESENTATION commands (walk with an A* path, work/error/success animations, status +
contextual bubbles) and dispatches the WORK state to the active Behavior. Source-agnostic:
identical whether driven by simulated (stage 1-2) or real GhostCrawl (stage 3+) events.
"""

from __future__ import annotations

from ghostopia_ghost_runtime.ghost_driver import CONTEXTUAL_EXTRACTORS, GhostDriver
from ghostopia_ghost_runtime.ghost_handle import create_ghost_handle
from ghostopia_ghost_runtime.state_machine import (
    Transition,
    is_legal,
    reduce,
    transitions,
)
from ghostopia_ghost_runtime.surface_vocab import (
    GENERIC_HELD,
    GENERIC_WORKING,
    sanitize_code,
    sanitize_kind,
    sanitize_text,
)

__all__ = [
    "reduce",
    "is_legal",
    "transitions",
    "Transition",
    "create_ghost_handle",
    "GhostDriver",
    "CONTEXTUAL_EXTRACTORS",
    # customer-surface boundary sanitizer
    "sanitize_kind",
    "sanitize_code",
    "sanitize_text",
    "GENERIC_WORKING",
    "GENERIC_HELD",
]
