"""ghostopia-behaviors — the first-class dynamic Behavior system.

A ghost runs ONE active :class:`~ghostopia_behaviors.behavior.Behavior` — a pluggable
tick/event module that decides what it does over time and drives the visible ghost
through the narrow ``GhostHandle`` while reaching GhostCrawl ONLY through the
full-primitive ``ctx.browser`` (never the SDK). Behaviors self-register by name against a
:class:`~ghostopia_behaviors.registry.BehaviorRegistry` with capability meta
(``param_schema``/``examples``) carried as DATA, so the renderer/core loop never branch on
behavior kind.

Author a behavior in ONE file, drop it under ``builtin/`` → the auto-discovery loader
registers it with zero renderer/core-loop edit.
"""

from __future__ import annotations

from ghostopia_behaviors.behavior import Behavior, BehaviorContext, EndReason
from ghostopia_behaviors.registry import (
    BehaviorMeta,
    BehaviorRegistration,
    BehaviorRegistry,
    behaviors,
)

__all__ = [
    "Behavior",
    "BehaviorContext",
    "EndReason",
    "BehaviorMeta",
    "BehaviorRegistration",
    "BehaviorRegistry",
    "behaviors",
]
