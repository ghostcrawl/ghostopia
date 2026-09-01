"""The data-driven tile-grid world model.

``load_map(data)`` builds a :class:`WorldMap` from a plain dict (JSON) — the world
geometry is DATA (``maps/graveyard.json``), never hardcoded in this module. A
``WorldMap`` exposes a walkable grid, the six named regions (each a tile-rect
``Bounds`` that doubles as the section partition), and grave/workstation
destination lookups. Collision lives in :mod:`ghostopia_world.collision`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ghostopia_shared import Bounds, PlacedProp, Workstation, WorldObject
from pydantic import BaseModel, ConfigDict, Field

from ghostopia_world.collision import (
    Side,
    build_masks,
    cell_walkable,
    make_walkable_callback,
)
from ghostopia_world.placed_props import (
    apply_placed_props,
    load_prop_footprints_file,
    parse_placed_props,
)

# The default map ships as DATA at the ghostopia workspace root: ghostopia/maps/.
# map.py lives at packages/ghost-world/src/ghostopia_world/map.py -> parents[4] is
# the ghostopia/ root.
DEFAULT_MAP_PATH: Path = Path(__file__).resolve().parents[4] / "maps" / "graveyard.json"


class WorldMap(BaseModel):
    """A loaded tile world: walkable grid + region partition + destinations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "graveyard"
    width: int
    height: int
    tile_size: int = 16
    walkable: list[list[int]]
    regions: dict[str, Bounds] = Field(default_factory=dict)
    graves: dict[str, WorldObject] = Field(default_factory=dict)
    workstations: dict[str, Workstation] = Field(default_factory=dict)
    # Server-owned placed-props layer. Each prop's footprint (looked up from the
    # shared catalog) is already folded into ``walkable`` above, so A* routes around them;
    # this list is retained so the server can broadcast the layer + recompute on change.
    placed_props: list[PlacedProp] = Field(default_factory=list)
    # Directional edge masks: (x, y) -> blocked entry sides. Excluded from the wire
    # dump shape callers compare against; it is derived collision state.
    directional_masks: dict[tuple[int, int], frozenset[Side]] = Field(
        default_factory=dict, exclude=True
    )

    # -- collision -------------------------------------------------------------

    def is_walkable(self, x: int, y: int) -> bool:
        """Static (directionless) walkability: in-bounds and not a blocked tile."""
        return cell_walkable(self.walkable, x, y, self.width, self.height)

    def walkable_callback(self) -> Callable[..., bool]:
        """The ``is_walkable(x, y, from_x=None, from_y=None)`` callback for A*.

        Applies static collision + the directional edge masks.
        """
        return make_walkable_callback(
            self.walkable, self.width, self.height, self.directional_masks
        )

    # -- destinations / regions -----------------------------------------------

    def grave_by_id(self, grave_id: str) -> WorldObject | None:
        return self.graves.get(grave_id)

    def workstation_by_id(self, ws_id: str) -> Workstation | None:
        return self.workstations.get(ws_id)

    def region_bounds(self, name: str) -> Bounds:
        return self.regions[name]


def load_map(data: dict[str, Any]) -> WorldMap:
    """Build a :class:`WorldMap` from a decoded map dict (WORLD_SPEC data)."""
    width = int(data["width"])
    height = int(data["height"])
    walkable = [[int(c) for c in row] for row in data["walkable"]]

    # Placed-props layer: fold each prop's footprint into the walkable grid so
    # A* routes around it. Footprints come from the shared catalog; a missing catalog file
    # degrades gracefully to 1x1 footprints (the layer still renders, collision is minimal).
    placed_props = parse_placed_props(data.get("placed_props"))
    if placed_props:
        try:
            footprints = load_prop_footprints_file()
        except (OSError, ValueError):
            footprints = {}
        walkable = apply_placed_props(walkable, placed_props, footprints)

    regions = {name: Bounds(**b) for name, b in (data.get("regions") or {}).items()}

    destinations = data.get("destinations") or {}
    graves: dict[str, WorldObject] = {}
    for g in destinations.get("graves", ()):
        obj = WorldObject(
            id=g["id"],
            type=g.get("type", "grave"),
            x=int(g["x"]),
            y=int(g["y"]),
            properties={
                k: v for k, v in g.items() if k not in {"id", "type", "x", "y"}
            },
        )
        graves[obj.id] = obj

    workstations: dict[str, Workstation] = {}
    for w in destinations.get("workstations", ()):
        ws = Workstation(
            id=w["id"],
            x=int(w["x"]),
            y=int(w["y"]),
            section=w.get("section"),
            occupied_by=w.get("occupied_by"),
        )
        workstations[ws.id] = ws

    masks = build_masks(data.get("directional_collision"))

    return WorldMap(
        name=data.get("name", "graveyard"),
        width=width,
        height=height,
        tile_size=int(data.get("tile_size", 16)),
        walkable=walkable,
        regions=regions,
        graves=graves,
        workstations=workstations,
        placed_props=placed_props,
        directional_masks=masks,
    )


def load_map_file(path: str | Path) -> WorldMap:
    """Load + parse a map JSON file into a :class:`WorldMap`."""
    with open(path) as f:
        return load_map(json.load(f))


def load_default_map() -> WorldMap:
    """Load the shipped default graveyard map (``maps/graveyard.json``)."""
    return load_map_file(DEFAULT_MAP_PATH)
