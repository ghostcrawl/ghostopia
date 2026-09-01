"""Inbound WS message validation.

Every message the thin TS frontend sends the server arrives as an ``Envelope`` (shared
contract): ``{protocol_version, type, ghost_id, ts, payload}``. The ``type`` selects one
of the management-verb models from ``ghostopia_shared.task`` (all ``extra='forbid'``);
the ``payload`` is validated against that model. An UNKNOWN ``type`` is rejected outright
(``UnknownMessageTypeError``) — client input is never ``eval``'d and never fanned out
un-validated.

This is the server's inbound boundary; ``ws_gateway`` calls ``validate_inbound`` on every
received frame after JWT-authenticating the connection.
"""

from __future__ import annotations

from typing import Any, Literal

from ghostopia_shared import EditableMap
from ghostopia_shared.task import (
    MissionAssign,
    MissionCancel,
    MissionCreate,
    MissionMonitor,
    MissionPause,
    MissionResume,
    MissionRetarget,
    MissionRun,
    MissionUpdate,
    TaskAssign,
    TaskCancel,
    TaskCreate,
    TaskMonitor,
    TaskPause,
    TaskResume,
    TaskRetarget,
    TaskRun,
    TaskUpdate,
)
from pydantic import BaseModel


class Ping(BaseModel):
    """A liveness ping the client may send; payload is empty."""

    model_config = {"extra": "forbid"}


class SimStart(BaseModel):
    """An authed operator control verb: begin the simulated world. Payload empty —
    the server owns which ghosts/behaviors run; the client only requests the stream."""

    model_config = {"extra": "forbid"}


class SimStop(BaseModel):
    """An authed operator control verb: halt the simulated world. Payload empty."""

    model_config = {"extra": "forbid"}


class MissionSubmit(BaseModel):
    """A real-mission submission — STAGE-3 single-session OR STAGE-6 fan-out.

    Carries ONLY NAMES + urls — never a key/secret (credentials resolve server-side from
    ``config``). Two shapes are accepted:

    * **legacy single** — ``target_name`` + ``url`` → one real GhostCrawl session (stage 3);
    * **fan-out** — ``urls`` (multi-target) + ``entry_section`` + ``agent_mode`` → the mission
      is split + fanned out across sections/ghosts through the bounded WorkQueue (stage 6).

    ``agent_mode`` picks the per-mission brain by NAME (``deterministic`` | ``llm``); the
    Anthropic key is read server-side, never crosses the WS."""

    model_config = {"extra": "forbid"}

    # legacy single-session fields (optional so the fan-out shape validates too).
    target_name: str | None = None
    url: str | None = None
    # STAGE-6 fan-out fields.
    title: str | None = None
    urls: list[str] | None = None
    entry_section: str | None = None
    agent_mode: Literal["deterministic", "llm"] | None = None
    query: str | None = None


class GhostManage(BaseModel):
    """A runtime MANAGEMENT command: assign-behavior / assign-section /
    pause / resume / cancel / retarget a ghost — server-authoritative.

    Carries NAMES only (``behavior`` / ``section`` registry/section ids) — never a key. The
    concrete Pydantic-per-command validation + authoritative effect live in
    :func:`ghostopia_server.management.handle_management_command`; this is the strict inbound
    gate (``extra='forbid'``) so an unknown field is rejected before dispatch."""

    model_config = {"extra": "forbid"}

    command: str
    ghost_id: str
    behavior: str | None = None
    section: str | None = None
    # operator commands: send-to-workstation targets a specific workstation by id.
    workstation: str | None = None


class CatalogRequest(BaseModel):
    """A STAGE-7 management-surface request for the server-relayed capability catalog.

    The client sends this on connect; the server answers with ``catalog.behaviors`` (from
    ``behaviors.list()``) + ``catalog.sections`` so the Sections panel + Ghost inspector
    dropdowns are populated from the server — adding a behavior/section needs NO UI edit.
    Payload empty (``extra='forbid'``)."""

    model_config = {"extra": "forbid"}


class CritterPet(BaseModel):
    """A click-to-pet on an autonomous graveyard critter.

    Carries only the critter id the operator tapped; the server acks a ``critter.petted``
    (a heart/spark flash) ONLY for a known critter. ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    critter_id: str


class GhostSelect(BaseModel):
    """A STAGE-4 live-inspector selection: stream ONE ghost's real frames.

    Carries only the ghost id to focus (``ghost_id: null`` / absent = deselect / panel close).
    The server opens ``recordings.visual().watch()`` for ONLY the selected ghost
    and relays ``browser.frame`` envelopes; the client holds no key. ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    ghost_id: str | None = None


class MapSave(BaseModel):
    """The Graveyard Builder ``map.save`` verb: the edited DRAFT map (operator-only,
    JWT-gated by the authed WS). ``map`` is the STRICT :class:`EditableMap` — the schema layer
    already enforces bounds/size-caps/grid-shape (``extra='forbid'``) before the server's deeper
    semantic validation (catalog allowlist + reachability) in ``map_editor`` runs."""

    model_config = {"extra": "forbid"}

    map: EditableMap


class MapLoad(BaseModel):
    """``map.load`` — request the current authoritative world snapshot (editor open). Empty."""

    model_config = {"extra": "forbid"}


class MapReset(BaseModel):
    """``map.reset`` — restore the built-in designed graveyard. Empty payload."""

    model_config = {"extra": "forbid"}


class SectionSave(BaseModel):
    """The department editor ``section.save`` verb (operator-only, JWT-gated by the
    authed WS, mirroring ``map.save``). ``section`` is the RAW department object — it is kept
    a loose ``dict`` at the schema layer ON PURPOSE so the deeper trust-boundary validation in
    :class:`~ghostopia_server.section_editor.SectionEditor` (strict :class:`SectionDef` →
    SSRF gate on ``target_url`` → surface-language guard on the label) owns the clean
    ``section.saved {ok:False, reason}`` reject envelope rather than a gateway-level rejection."""

    model_config = {"extra": "forbid"}

    section: dict[str, Any]


class SectionRemove(BaseModel):
    """The ``section.remove`` verb — drop a department by id at runtime. ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    id: str


class WorkforceStart(BaseModel):
    """``workforce.start`` — run the Live-mode workforce template (3 ghosts × 6 sections
    on example.com). Empty payload (``extra='forbid'``); the template is server-defined."""

    model_config = {"extra": "forbid"}


class WorkforceStop(BaseModel):
    """195 ``workforce.stop`` — dematerialize the running workforce (despawn every
    workforce/department ghost). Empty payload (``extra='forbid'``)."""

    model_config = {"extra": "forbid"}


class WorkforceAdvanced(BaseModel):
    """``workforce.advanced`` — toggle an opt-in ADVANCED real-retail department
    on/off at runtime. Carries the department ``id`` + ``enabled`` flag; the server honors only
    a KNOWN advanced department id (an arbitrary id is ignored so the safe keyless default can
    never be bypassed). ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    id: str
    enabled: bool


class GhostSpawn(BaseModel):
    """``ghost.spawn`` — add ONE ambient ghost into a named section (the per-section '+'
    control). Carries only the section NAME; the server picks the behavior + id. ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    section: str


class GhostDespawn(BaseModel):
    """``ghost.despawn`` — remove ONE ghost authoritatively (the per-section '-' control).
    Carries only the ghost id. ``extra='forbid'``."""

    model_config = {"extra": "forbid"}

    ghost_id: str


# The allow-list of inbound message ``type`` -> the model its ``payload`` must satisfy.
# Anything not in this map is rejected (never dispatched, never eval'd).
INBOUND_MODELS: dict[str, type[BaseModel]] = {
    "client.ping": Ping,
    "sim.start": SimStart,
    "sim.stop": SimStop,
    "mission.submit": MissionSubmit,
    "ghost.select": GhostSelect,
    "ghost.manage": GhostManage,
    "critter.pet": CritterPet,
    "catalog.request": CatalogRequest,
    "map.save": MapSave,
    "map.load": MapLoad,
    "map.reset": MapReset,
    "section.save": SectionSave,
    "section.remove": SectionRemove,
    "workforce.start": WorkforceStart,
    "workforce.stop": WorkforceStop,
    "workforce.advanced": WorkforceAdvanced,
    "ghost.spawn": GhostSpawn,
    "ghost.despawn": GhostDespawn,
    "task.create": TaskCreate,
    "task.assign": TaskAssign,
    "task.run": TaskRun,
    "task.update": TaskUpdate,
    "task.pause": TaskPause,
    "task.resume": TaskResume,
    "task.retarget": TaskRetarget,
    "task.cancel": TaskCancel,
    "task.monitor": TaskMonitor,
    "mission.create": MissionCreate,
    "mission.assign": MissionAssign,
    "mission.run": MissionRun,
    "mission.update": MissionUpdate,
    "mission.pause": MissionPause,
    "mission.resume": MissionResume,
    "mission.retarget": MissionRetarget,
    "mission.cancel": MissionCancel,
    "mission.monitor": MissionMonitor,
}


class UnknownMessageTypeError(ValueError):
    """Raised when an inbound message ``type`` is not in the allow-list."""


def is_known_type(msg_type: str) -> bool:
    """True when ``msg_type`` is an accepted inbound message type."""
    return msg_type in INBOUND_MODELS


def validate_inbound(msg_type: str, payload: Any) -> BaseModel:
    """Validate ``payload`` against the model for ``msg_type``.

    Raises ``UnknownMessageTypeError`` for an unrecognized ``type`` and
    ``pydantic.ValidationError`` when the payload does not match the model
    (unknown keys included — every model is ``extra='forbid'``).
    """
    model = INBOUND_MODELS.get(msg_type)
    if model is None:
        raise UnknownMessageTypeError(f"unknown inbound message type: {msg_type!r}")
    return model.model_validate(payload if payload is not None else {})
