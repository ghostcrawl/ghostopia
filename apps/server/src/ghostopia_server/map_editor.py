"""Server-authoritative map editor verbs — the Graveyard Builder backend.

The in-app editor edits a DRAFT map client-side and sends it on the JWT-gated ``map.save``
WS verb. This module is the TRUST BOUNDARY: it VALIDATES the submitted map (strict Pydantic
``EditableMap`` at the schema layer, then the SEMANTIC checks here — in-bounds footprints, a
catalog-id allowlist, at least one section plot present, and A* REACHABILITY of every grave/
workstation from the hub over the RECOMPUTED collision grid), and only on a fully-valid map
does it swap the live world atomically + rebroadcast a ``world.snapshot`` so every client and
running ghost picks it up. An invalid/hostile map is REJECTED with a reason and the live map
is left UNTOUCHED — never half-applied.

Security:
* the verbs run BEHIND the authed WS (``WsGateway`` verifies the operator JWT pre-accept) +
  the strict inbound allow-list, so they are operator-only and their payload is schema-checked
  before this handler ever runs;
* nothing here trusts client geometry — the collision grid + A* graph are recomputed
  server-side from the SAME loader the shipped world uses (no editor-only map code path);
* ``map.reset`` restores the built-in DESIGNED graveyard (``maps/graveyard.json`` — never
  overwritten), which stays the shipped default (the editor is additive).
"""

from __future__ import annotations

import collections
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ghostopia_shared import (
    Bounds,
    EditableArea,
    EditableDirCollision,
    EditableGrave,
    EditableMap,
    EditableWorkstation,
    Envelope,
    PlacedProp,
)
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_world import WorldMap, load_default_map, load_map
from ghostopia_world.map import DEFAULT_MAP_PATH
from ghostopia_world.placed_props import (
    footprint_tiles,
    load_prop_footprints_file,
)

Broadcast = Callable[[Envelope], Awaitable[None]]

# The 4-neighbourhood used by the reachability flood-fill (matches the A* neighbour set).
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


# --------------------------------------------------------------------------------------
# Default / round-trip helpers
# --------------------------------------------------------------------------------------


def _load_default_raw() -> dict[str, Any]:
    """The shipped designed graveyard as its RAW JSON dict (BASE walkable — props NOT folded
    in). This is the canonical default the editor starts from + ``map.reset`` restores."""
    with open(DEFAULT_MAP_PATH) as f:
        return json.load(f)


def default_editable_map() -> EditableMap:
    """The shipped designed graveyard as an :class:`EditableMap` (base terrain + props list).

    Reads the raw default JSON so the ``walkable`` grid is the BASE terrain (prop footprints
    are re-folded at validation time) — so removing a prop in the editor really re-opens its
    tile. This is the starting draft AND what ``map.reset`` restores."""
    return editable_from_raw(_load_default_raw())


def editable_from_raw(data: dict[str, Any]) -> EditableMap:
    """Build a strict :class:`EditableMap` from a raw map-JSON dict (the graveyard.json shape:
    destinations nested, base walkable, areas/placed_props/directional_collision passthrough)."""
    dests = data.get("destinations") or {}
    graves = [EditableGrave(**g) for g in dests.get("graves", ())]
    workstations = [EditableWorkstation(**w) for w in dests.get("workstations", ())]
    regions = {k: Bounds(**b) for k, b in (data.get("regions") or {}).items()}
    areas = [EditableArea(**a) for a in (data.get("areas") or ())]
    props = [PlacedProp(**p) for p in (data.get("placed_props") or ())]
    dcoll = [EditableDirCollision(**d) for d in (data.get("directional_collision") or ())]
    return EditableMap(
        name=str(data.get("name", "graveyard")),
        width=int(data["width"]),
        height=int(data["height"]),
        tile_size=int(data.get("tile_size", 16)),
        walkable=[[int(c) for c in row] for row in data["walkable"]],
        regions=regions,
        areas=areas,
        placed_props=props,
        graves=graves,
        workstations=workstations,
        directional_collision=dcoll,
    )


# --------------------------------------------------------------------------------------
# Validation (the trust boundary)
# --------------------------------------------------------------------------------------


def _reachable_tiles(world: WorldMap, start: tuple[int, int]) -> set[tuple[int, int]]:
    """Every tile reachable from ``start`` over the world's walkable callback (BFS, 4-neigh)."""
    is_walkable = world.walkable_callback()
    seen: set[tuple[int, int]] = set()
    if not is_walkable(start[0], start[1]):
        return seen
    queue = collections.deque([start])
    seen.add(start)
    while queue:
        cx, cy = queue.popleft()
        for dx, dy in _NEIGHBOURS:
            nx, ny = cx + dx, cy + dy
            nb = (nx, ny)
            if nb in seen:
                continue
            if is_walkable(nx, ny, cx, cy):
                seen.add(nb)
                queue.append(nb)
    return seen


def _hub_start(world: WorldMap) -> tuple[int, int] | None:
    """The hub start tile for reachability — the first grave's tile if walkable, else a
    walkable neighbour of it (production seats a ghost on its home grave)."""
    graves = sorted(world.graves.values(), key=lambda g: g.id)
    if not graves:
        return None
    is_walkable = world.walkable_callback()
    g = graves[0]
    if is_walkable(g.x, g.y):
        return (g.x, g.y)
    for dx, dy in _NEIGHBOURS:
        if is_walkable(g.x + dx, g.y + dy):
            return (g.x + dx, g.y + dy)
    return None


def _dest_reachable(dest: tuple[int, int], reachable: set[tuple[int, int]]) -> bool:
    """A destination is reachable if its tile OR any 4-neighbour is in the reachable set (a
    ghost seats itself ON or ADJACENT to the destination)."""
    if dest in reachable:
        return True
    return any((dest[0] + dx, dest[1] + dy) in reachable for dx, dy in _NEIGHBOURS)


def validate_editable_map(
    m: EditableMap,
) -> tuple[bool, str | None, WorldMap | None]:
    """Validate an :class:`EditableMap` end-to-end. Returns ``(ok, reason, world)``.

    On success ``world`` is the RECOMPUTED :class:`WorldMap` (footprints folded into collision,
    A* graph implied) ready to swap live. On failure ``reason`` names the first problem and
    ``world`` is ``None`` — the caller MUST leave the live map untouched.

    Checks (in order): catalog-id allowlist → in-bounds footprints → every section plot present
    → at least one grave + workstation → A* REACHABILITY of every grave/workstation from the hub.
    """
    # 1. catalog-id allowlist + in-bounds footprints (recompute footprints from the catalog).
    try:
        footprints = load_prop_footprints_file()
    except (OSError, ValueError):
        footprints = {}
    for i, prop in enumerate(m.placed_props):
        if prop.catalog_id not in footprints:
            return (False, f"unknown catalog id {prop.catalog_id!r} at placed_props[{i}]", None)
        # every footprint tile must be in-bounds (a prop cannot hang off the grid).
        for tx, ty in footprint_tiles(prop, footprints):
            if not (0 <= tx < m.width and 0 <= ty < m.height):
                return (
                    False,
                    f"prop {prop.catalog_id!r} at placed_props[{i}] footprint is out of bounds",
                    None,
                )

    # 2. at least one section PLOT (area) must be present. Scenery regions
    #    (the crypt) and the grave-scatter region (ghost-graves) are intentionally UNLABELED —
    #    named plots exist ONLY for the real GhostCrawl-work zones — so a plot is NO LONGER
    #    required for every region (that over-strict rule predated dropping the "home" concept).
    if not m.areas:
        return (False, "no section plots present", None)

    # 3. destinations exist.
    if not m.graves:
        return (False, "no graves — at least one home grave is required", None)
    if not m.workstations:
        return (False, "no workstations — at least one workstation is required", None)

    # 4. recompute the collision grid + A* graph via the SAME loader the shipped world uses.
    try:
        world = load_map(m.to_load_dict())
    except (KeyError, ValueError, TypeError) as err:  # a shape the strict schema still let by
        return (False, f"map failed to load: {err}", None)

    # 5. reachability: every grave + workstation reachable from the hub over the recomputed grid.
    start = _hub_start(world)
    if start is None:
        return (False, "hub grave is not on a walkable tile — no ghost could start", None)
    reachable = _reachable_tiles(world, start)
    for g in world.graves.values():
        if not _dest_reachable((g.x, g.y), reachable):
            return (False, f"grave {g.id!r} is not reachable from the hub", None)
    for w in world.workstations.values():
        if not _dest_reachable((w.x, w.y), reachable):
            return (False, f"workstation {w.id!r} is not reachable from the hub", None)

    return (True, None, world)


# --------------------------------------------------------------------------------------
# The WS verb handler
# --------------------------------------------------------------------------------------


class _MapHolder:
    """The narrow world-map surface the editor swaps (satisfied by GhostPool or a test double)."""

    @property
    def world_map(self) -> WorldMap: ...  # pragma: no cover - Protocol-ish shape

    def set_world_map(self, world: WorldMap) -> None: ...  # pragma: no cover


class MapEditor:
    """Owns the authoritative editable map + the ``map.save``/``map.load``/``map.reset`` verbs.

    Holds the current base :class:`EditableMap` (the editor's source of truth) and swaps the
    live :class:`WorldMap` on the injected ``pool`` (so running ghosts re-path onto the new
    grid). A validated save writes-through to ``GHOSTOPIA_MAP_PATH`` when set (persistence
    across restart) — the shipped ``maps/graveyard.json`` is NEVER overwritten.
    """

    def __init__(self, broadcast: Broadcast, *, pool: Any | None = None) -> None:
        self._broadcast = broadcast
        self._pool = pool
        self._editable: EditableMap = self._initial_editable()

    def _initial_editable(self) -> EditableMap:
        """Load a persisted runtime map if ``GHOSTOPIA_MAP_PATH`` points at one, else the
        shipped designed default. A persisted map that no longer validates falls back to
        the default (never boots with a broken world)."""
        path = os.environ.get("GHOSTOPIA_MAP_PATH")
        if path and Path(path).exists():
            try:
                data = json.loads(Path(path).read_text())
                m = editable_from_raw(data)
                ok, _reason, _world = validate_editable_map(m)
                if ok:
                    return m
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return default_editable_map()

    # -- installation ---------------------------------------------------------------

    def install(self, gateway: Any) -> None:
        """Register the three editor verbs on the authed gateway (JWT-gated + allow-listed)."""
        gateway.register_control("map.save", self.on_save)
        gateway.register_control("map.load", self.on_load)
        gateway.register_control("map.reset", self.on_reset)

    # -- current state --------------------------------------------------------------

    @property
    def editable(self) -> EditableMap:
        """The current authoritative editable map (base terrain + props)."""
        return self._editable

    def _snapshot_env(self) -> Envelope:
        return serialize_envelope(
            type="world.snapshot", ts=time.time(), payload={"map": self._editable.model_dump()}
        )

    async def _broadcast_snapshot(self) -> None:
        await self._broadcast(self._snapshot_env())

    def _persist(self) -> None:
        path = os.environ.get("GHOSTOPIA_MAP_PATH")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._editable.to_load_dict()))
        except OSError:
            pass  # persistence is best-effort; the in-memory swap is authoritative.

    def _apply(self, m: EditableMap, world: WorldMap) -> None:
        """Atomic live swap: the editable source of truth AND the pool's A* map together."""
        self._editable = m
        if self._pool is not None:
            self._pool.set_world_map(world)
        self._persist()

    # -- verbs ----------------------------------------------------------------------

    async def on_save(self, envelope: Envelope) -> None:
        """``map.save {map}`` — validate → (on ok) swap live + rebroadcast; (on fail) reject."""
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        raw = payload.get("map")
        try:
            m = EditableMap.model_validate(raw)
        except Exception as err:  # noqa: BLE001 - any parse failure is a clean reject
            await self._broadcast(
                serialize_envelope(
                    type="map.saved",
                    ts=time.time(),
                    payload={"ok": False, "reason": f"invalid map shape: {err}"},
                )
            )
            return

        ok, reason, world = validate_editable_map(m)
        if not ok or world is None:
            await self._broadcast(
                serialize_envelope(
                    type="map.saved", ts=time.time(), payload={"ok": False, "reason": reason}
                )
            )
            return

        # validated → swap live atomically, then rebroadcast the new world.
        self._apply(m, world)
        await self._broadcast(
            serialize_envelope(type="map.saved", ts=time.time(), payload={"ok": True})
        )
        await self._broadcast_snapshot()

    async def on_load(self, _envelope: Envelope) -> None:
        """``map.load`` — rebroadcast the current authoritative world snapshot (editor open)."""
        await self._broadcast_snapshot()

    async def on_reset(self, _envelope: Envelope) -> None:
        """``map.reset`` — restore the built-in designed graveyard + rebroadcast it."""
        m = default_editable_map()
        world = load_default_map()
        self._apply(m, world)
        await self._broadcast(
            serialize_envelope(type="map.saved", ts=time.time(), payload={"ok": True, "reset": True})
        )
        await self._broadcast_snapshot()


__all__ = [
    "MapEditor",
    "default_editable_map",
    "editable_from_raw",
    "validate_editable_map",
]
