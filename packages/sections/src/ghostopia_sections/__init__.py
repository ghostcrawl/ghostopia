"""ghostopia-sections (module ``ghostopia_sections``) — the ghostopia-original Section
model: the per-group dynamism + assignment unit.

A :class:`Section` is a labelled sub-region of the graveyard (bounds), a pool of
workstation types, a live roster of ghosts, and a **role** (a default behavior + a
task-routing rule + an optional default ``gc_target``) — all declared as DATA
(``maps/graveyard.sections.json``). Missions/tasks fan out to sections BY ROLE
(:func:`route_task`, ``accepts``/``routes_to``, capacity-bounded) OR to an explicit
``TaskSpec.target.section``; a ghost's behavior resolves by a fixed precedence
(:func:`resolve_behavior`). Roles/hints/overrides are ``BehaviorRegistry`` names, so
adding or re-roling a section is a data change — no renderer/core-loop edit.
"""

from __future__ import annotations

from ghostopia_sections.fanout import RouteResult, fan_out_mission, route_task
from ghostopia_sections.resolve_behavior import IDLE_FALLBACK, resolve_behavior
from ghostopia_sections.section import (
    DEFAULT_SECTIONS_PATH,
    GcTarget,
    Section,
    SectionDef,
    load_default_sections,
    load_sections,
)

__all__ = [
    "DEFAULT_SECTIONS_PATH",
    "IDLE_FALLBACK",
    "GcTarget",
    "RouteResult",
    "Section",
    "SectionDef",
    "fan_out_mission",
    "load_default_sections",
    "load_sections",
    "resolve_behavior",
    "route_task",
]
