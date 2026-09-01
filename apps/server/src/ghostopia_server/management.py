"""Runtime MANAGEMENT surface — assign / pause / resume / retarget.

The operator can, AT RUNTIME, re-shape the workforce without a
redeploy — pin a behavior on a ghost, move a ghost to another section, pause/resume it, or
retarget it (cancel the in-flight run + re-home it). Each command is:

* **Pydantic-validated** (``extra='forbid'``) — a hallucinated/unknown field is rejected;
* **server-authoritative** — applied to the live :class:`~ghostopia_server.ghost_pool.GhostPool`
  records + :class:`~ghostopia_sections.Section` rosters, never trusted from the wire;
* **NAMES only** — a command carries a behavior/section NAME, never a key.

The command→effect table:

| command          | server effect                                                          |
|------------------|------------------------------------------------------------------------|
| ``assign_behavior`` | set ``rec.behavior_override`` (wins in ``resolve_behavior`` on re-mount) |
| ``assign_section``  | move roster (old ``remove_ghost`` → new ``add_ghost``); rebind section  |
| ``pause``           | set ``rec.paused`` — the executor tick loop SKIPS on_tick (real halt)   |
| ``resume``          | clear ``rec.paused``                                                    |
| ``cancel``          | signal ``rec.abort_event`` + cancel the run → on_end(cancelled)+release |
| ``retarget``        | cancel the in-flight run (→ ``on_end(cancelled)`` + release) + re-home  |

An unknown command, an unknown ghost, an unregistered behavior, or an unknown section is a
:class:`ManagementError` (never a silent apply). This handler + the orchestrator are the
clean assign/lifecycle seam the Task/mission management API composes over.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from ghostopia_behaviors import behaviors as behavior_registry
from ghostopia_sections import Section
from ghostopia_shared import Point
from ghostopia_world import WorldMap, find_path
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .ghost_pool import GhostPool

__all__ = [
    "AssignBehavior",
    "AssignSection",
    "CancelGhost",
    "ManagementError",
    "PauseGhost",
    "ReassignGhost",
    "RecallGhost",
    "ResumeGhost",
    "RetargetGhost",
    "SendToWorkstation",
    "handle_management_command",
    "plan_operator_walk",
]


class ManagementError(ValueError):
    """A rejected management command (unknown command/ghost/behavior/section)."""


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssignBehavior(_Cmd):
    """Pin ``behavior`` on ``ghost_id`` (wins in ``resolve_behavior`` on the next re-mount)."""

    command: Literal["assign_behavior"]
    ghost_id: str
    behavior: str


class AssignSection(_Cmd):
    """Move ``ghost_id`` into ``section`` (roster move + walk to the new bounds)."""

    command: Literal["assign_section"]
    ghost_id: str
    section: str


class PauseGhost(_Cmd):
    """Pause ``ghost_id`` (its behavior's ``on_tick`` early-returns until resumed)."""

    command: Literal["pause"]
    ghost_id: str


class ResumeGhost(_Cmd):
    """Resume a paused ``ghost_id``."""

    command: Literal["resume"]
    ghost_id: str


class CancelGhost(_Cmd):
    """Cancel ``ghost_id``'s in-flight run: signal its abort event + cancel the run task so the
    executor / runner stops, fires ``on_end(cancelled)`` once, and releases the session."""

    command: Literal["cancel"]
    ghost_id: str


class RetargetGhost(_Cmd):
    """Cancel ``ghost_id``'s in-flight run (``on_end(cancelled)`` + release) and re-home it
    on a new ``section`` and/or ``behavior`` (either may be omitted)."""

    command: Literal["retarget"]
    ghost_id: str
    section: str | None = None
    behavior: str | None = None


class SendToWorkstation(_Cmd):
    """Operator command: walk ``ghost_id`` to a chosen workstation (by id) and/or
    ``section``. Re-paths the ghost via A* to the workstation tile + seats it in that section's
    roster; the ghost's world position reflects the walk (server-authoritative)."""

    command: Literal["send_to_workstation"]
    ghost_id: str
    section: str | None = None
    workstation: str | None = None


class RecallGhost(_Cmd):
    """Operator command: recall ``ghost_id`` HOME — walk it back to the graveyard and
    idle. Re-paths via A* to the home grave; state → RETURNING_HOME."""

    command: Literal["recall"]
    ghost_id: str


class ReassignGhost(_Cmd):
    """Operator command: move ``ghost_id`` to another ``section`` and walk it there.
    Roster move (old ``remove_ghost`` → new ``add_ghost``) + an A* re-path into the new plot."""

    command: Literal["reassign"]
    ghost_id: str
    section: str


ManagementCommand = Annotated[
    AssignBehavior
    | AssignSection
    | CancelGhost
    | PauseGhost
    | ReassignGhost
    | RecallGhost
    | ResumeGhost
    | RetargetGhost
    | SendToWorkstation,
    Field(discriminator="command"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(ManagementCommand)


def _sections_by_id(sections: list[Section]) -> dict[str, Section]:
    return {s.id: s for s in sections}


def handle_management_command(
    cmd: dict[str, Any] | BaseModel,
    pool: GhostPool,
    sections: list[Section],
    *,
    registry: Any = behavior_registry,
) -> dict[str, Any]:
    """Validate + apply one management command to authoritative state.

    ``cmd`` is a raw dict (validated here against the discriminated command union) or an
    already-validated command model. Returns a small ``{ok, command, ghost_id, ...}``
    result describing the applied effect. Raises :class:`ManagementError` for an unknown
    command shape, an unknown ghost, an unregistered behavior, or an unknown section.
    """
    if isinstance(cmd, BaseModel):
        model: Any = cmd
    else:
        try:
            model = _ADAPTER.validate_python(cmd)
        except ValidationError as err:
            raise ManagementError(f"invalid management command: {err}") from None

    if not pool.has(model.ghost_id):
        raise ManagementError(f"unknown ghost {model.ghost_id!r}")
    rec = pool.record(model.ghost_id)
    by_id = _sections_by_id(sections)

    if isinstance(model, AssignBehavior):
        _require_behavior(registry, model.behavior)
        rec.behavior_override = model.behavior
        return {"ok": True, "command": "assign_behavior", "ghost_id": rec.ghost_id,
                "behavior": model.behavior}

    if isinstance(model, AssignSection):
        target = _require_section(by_id, model.section)
        _move_section(rec, target, by_id)
        return {"ok": True, "command": "assign_section", "ghost_id": rec.ghost_id,
                "section": target.id}

    if isinstance(model, PauseGhost):
        rec.paused = True
        return {"ok": True, "command": "pause", "ghost_id": rec.ghost_id, "paused": True}

    if isinstance(model, ResumeGhost):
        rec.paused = False
        return {"ok": True, "command": "resume", "ghost_id": rec.ghost_id, "paused": False}

    # cancel: signal the abort event (the executor tick loop / run_real_task both honor it →
    # on_end(cancelled) + session release) AND cancel the run task for a hard interrupt. Clear
    # paused so a cancelled-while-paused ghost isn't wedged. Idempotent.
    if isinstance(model, CancelGhost):
        rec.paused = False
        rec.abort_event.set()
        if rec.run_task is not None and not rec.run_task.done():
            rec.run_task.cancel()
        rec.state = "CANCELLED"
        return {"ok": True, "command": "cancel", "ghost_id": rec.ghost_id, "cancelled": True}

    # retarget: cancel the in-flight run (executor fires on_end(cancelled) + release), then
    # rebind the new section/behavior so the ghost re-homes on its next assignment.
    if isinstance(model, RetargetGhost):
        if model.behavior is not None:
            _require_behavior(registry, model.behavior)
            rec.behavior_override = model.behavior
        if model.section is not None:
            target = _require_section(by_id, model.section)
            _move_section(rec, target, by_id)
        rec.abort_event.set()  # stop the in-flight run (fan-out path honors the event too)
        if rec.run_task is not None and not rec.run_task.done():
            rec.run_task.cancel()
        rec.state = "RETARGETED"
        return {"ok": True, "command": "retarget", "ghost_id": rec.ghost_id,
                "section": rec.section_id, "behavior": rec.behavior_override}

    # ---- operator commands: authoritative send / recall / reassign that actually
    #      re-path the ghost (A* on the pool's world map) + re-seat its section roster. ----
    world_map = pool.world_map

    if isinstance(model, SendToWorkstation):
        if model.section is not None:
            target_section = _require_section(by_id, model.section)
            _move_section(rec, target_section, by_id)
        seat_bounds = rec.section.bounds if rec.section is not None else None
        target_tile = _send_target_tile(world_map, model.workstation, seat_bounds)
        if target_tile is None:
            raise ManagementError(
                f"no workstation {model.workstation or ''!r} in section {rec.section_id!r}"
            )
        walk = _apply_walk(rec, world_map, target_tile, mode="workstation", state="WALKING")
        return {"ok": True, "command": "send_to_workstation", "ghost_id": rec.ghost_id,
                "section": rec.section_id, "target": {"x": target_tile.x, "y": target_tile.y},
                "walk": walk}

    if isinstance(model, RecallGhost):
        target_tile = _home_tile(world_map, near=rec.last_tile)
        walk = _apply_walk(rec, world_map, target_tile, mode="home", state="RETURNING_HOME")
        return {"ok": True, "command": "recall", "ghost_id": rec.ghost_id,
                "target": {"x": target_tile.x, "y": target_tile.y}, "walk": walk}

    if isinstance(model, ReassignGhost):
        target_section = _require_section(by_id, model.section)
        _move_section(rec, target_section, by_id)
        target_tile = _seat_tile(world_map, target_section.bounds)
        if target_tile is None:
            raise ManagementError(f"section {target_section.id!r} has no reachable tile")
        walk = _apply_walk(rec, world_map, target_tile, mode="section", state="WALKING")
        return {"ok": True, "command": "reassign", "ghost_id": rec.ghost_id,
                "section": rec.section_id, "target": {"x": target_tile.x, "y": target_tile.y},
                "walk": walk}

    raise ManagementError(f"unhandled command {model!r}")  # pragma: no cover


def _require_behavior(registry: Any, name: str) -> None:
    if name not in registry.names():
        raise ManagementError(f"unregistered behavior {name!r}")


def _require_section(by_id: dict[str, Section], section_id: str) -> Section:
    target = by_id.get(section_id)
    if target is None:
        raise ManagementError(f"unknown section {section_id!r}")
    return target


def _move_section(rec: Any, target: Section, by_id: dict[str, Section]) -> None:
    old = by_id.get(rec.section_id)
    if old is not None and old.id != target.id:
        old.remove_ghost(rec.ghost_id)
    target.add_ghost(rec.ghost_id)
    rec.section = target
    rec.section_id = target.id


# --------------------------------------------------------------------------------------
# operator-command re-pathing (PURE where it can be — unit-tested in isolation)
# --------------------------------------------------------------------------------------


def _home_tile(world_map: WorldMap, near: Point | None = None) -> Point:
    """The grave tile a recalled ghost walks back to — the NEAREST grave to ``near`` (the
    ghost's current tile) when supplied, else the first grave by id (graves are
    transient shared rest spots, never a hardcoded designated home)."""
    graves = sorted(world_map.graves.values(), key=lambda g: g.id)
    if not graves:
        return Point(x=0.0, y=0.0)
    if near is None:
        g = graves[0]
    else:
        g = min(graves, key=lambda gr: abs(gr.x - near.x) + abs(gr.y - near.y))
    return Point(x=float(g.x), y=float(g.y))


def _workstation_tile(world_map: WorldMap, ws_id: str) -> Point | None:
    ws = world_map.workstation_by_id(ws_id)
    return None if ws is None else Point(x=float(ws.x), y=float(ws.y))


def _in_bounds(tx: int, ty: int, bounds: Any) -> bool:
    return bounds.x <= tx < bounds.x + bounds.w and bounds.y <= ty < bounds.y + bounds.h


def _seat_tile(world_map: WorldMap, bounds: Any) -> Point | None:
    """A seat tile inside ``bounds``: a workstation within it, else the first walkable tile."""
    stations = sorted(
        (w for w in world_map.workstations.values() if _in_bounds(w.x, w.y, bounds)),
        key=lambda w: w.id,
    )
    if stations:
        return Point(x=float(stations[0].x), y=float(stations[0].y))
    for ty in range(bounds.y, bounds.y + bounds.h):
        for tx in range(bounds.x, bounds.x + bounds.w):
            if world_map.is_walkable(tx, ty):
                return Point(x=float(tx), y=float(ty))
    return None


def _send_target_tile(
    world_map: WorldMap, workstation: str | None, bounds: Any
) -> Point | None:
    """Resolve a send-to-workstation target: an explicit workstation id, else a seat in bounds."""
    if workstation is not None:
        return _workstation_tile(world_map, workstation)
    if bounds is None:
        return None
    return _seat_tile(world_map, bounds)


def _tile_ground_px(tx: float, ty: float, tile_size: int) -> dict[str, float]:
    """Tile → world-pixel ground point (bottom-centre) — matches the renderer/driver space."""
    return {"x": tx * tile_size + tile_size / 2.0, "y": ty * tile_size + float(tile_size)}


def plan_operator_walk(
    world_map: WorldMap, start: Point, target: Point
) -> list[dict[str, float]]:
    """PURE: an A* tile path start→target, converted to a WORLD-PIXEL walk path.

    Returns ``[]`` when the target is unreachable (the caller can still snap the ghost). Each
    point is a ``{x, y}`` ground pixel (the exact shape the renderer's walk interpolation
    consumes); unit-testable in isolation (its last point is the target tile's ground pixel)."""
    tiles = find_path(
        (int(round(start.x)), int(round(start.y))),
        (int(round(target.x)), int(round(target.y))),
        world_map.width,
        world_map.height,
        world_map.walkable_callback(),
    )
    ts = world_map.tile_size
    return [_tile_ground_px(float(tx), float(ty), ts) for (tx, ty) in tiles]


def _apply_walk(
    rec: Any, world_map: WorldMap, target: Point, *, mode: str, state: str
) -> dict[str, Any]:
    """Apply an operator re-path to ``rec`` (authoritative) + return the walk-command args.

    Sets the ghost's coarse ``state``, records the new ``last_tile`` + ``forced_target``, and
    plans the A* pixel path from its last known tile (or home) to ``target``."""
    start = rec.last_tile or _home_tile(world_map)
    path = plan_operator_walk(world_map, start, target)
    rec.state = state
    rec.forced_target = target
    rec.last_tile = target
    dest = _tile_ground_px(target.x, target.y, world_map.tile_size)
    return {"mode": mode, "path": path if path else [dest], "destination": dest}
