"""Per-ghost behavior vs section role resolution.

A ghost's active behavior resolves by a FIXED precedence — a per-ghost override wins,
else a task hint, else the section's role (the area default), else the idle-wander
fallback::

    resolve_behavior(ghost, section, task):
      1. ghost.behavior_override    # operator pinned a behavior on this ghost
      2. task.behavior_hint         # a mission/task requested a specific behavior
      3. section.role               # the section's default behavior
      4. "idle_wander"              # fallback when nothing else applies

The RESOLVED name MUST be a :class:`~ghostopia_behaviors.registry.BehaviorRegistry`
key (role/hint/override are all registry names) — an unregistered name is a validation
error (fail fast, no silent idle). Section roles/hints/overrides therefore compose
cleanly with the plugin registry: re-roling a section is a data change.
"""

from __future__ import annotations

from ghostopia_behaviors.builtin import discover_builtins
from ghostopia_behaviors.registry import BehaviorRegistry, behaviors
from ghostopia_shared.types import Ghost, Task
from ghostopia_shared.types import SectionDef as _SharedSectionDef

from ghostopia_sections.section import Section, SectionDef

__all__ = ["IDLE_FALLBACK", "resolve_behavior"]

#: The final-tier fallback behavior when no override/hint/role applies. It is a
#: registered builtin (``idle_wander``), so the fallback itself validates.
IDLE_FALLBACK = "idle_wander"

# The default registry is the process-wide singleton; the builtins self-register on
# import. ``discover_builtins`` is idempotent (Python's import cache prevents a second
# module body execution), so ensuring discovery on first use is cheap + safe.
_discovered = False


def _default_registry() -> BehaviorRegistry:
    global _discovered
    if not _discovered:
        discover_builtins()
        _discovered = True
    return behaviors


def _section_role(section: Section | SectionDef | _SharedSectionDef) -> str:
    """The role of either a :class:`Section` runtime or a bare ``SectionDef``."""
    return section.role


def resolve_behavior(
    ghost: Ghost,
    section: Section | SectionDef | _SharedSectionDef | None,
    task: Task | None,
    registry: BehaviorRegistry | None = None,
) -> str:
    """Resolve the behavior name for ``ghost`` via the fixed precedence.

    Precedence: ``ghost.behavior_override`` > ``task.behavior_hint`` > ``section.role``
    > :data:`IDLE_FALLBACK`. The resolved name is validated against ``registry`` (the
    process-wide ``behaviors`` singleton by default); an unregistered name raises
    ``ValueError`` — a role/hint/override that is not a registered behavior is a data
    error, never a silent fallback to idle.
    """
    reg = registry if registry is not None else _default_registry()

    name = (
        ghost.behavior_override
        or (task.behavior_hint if task is not None else None)
        or (_section_role(section) if section is not None else None)
        or IDLE_FALLBACK
    )

    if name not in reg.names():
        raise ValueError(
            f"resolved behavior {name!r} is not registered "
            f"(registered: {sorted(reg.names())})"
        )
    return name
