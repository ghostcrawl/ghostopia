"""ghostopia-ghost-world (module ``ghostopia_world``) — the server-authoritative
tile-grid world model, hand-rolled A* pathfinder, and read-only WorldQuery.

The world geometry is DATA (``maps/graveyard.json``, loaded by :func:`load_map`);
region bounds double as the section partition. Behaviors read
:class:`ghostopia_shared.WorldQuery` via :func:`create_world_query`; the TS renderer
reads the same map JSON.
"""

from __future__ import annotations

from ghostopia_world.collision import (
    BLOCKED,
    WALKABLE,
    build_masks,
    cell_walkable,
    direction_from,
    directional_blocked,
    in_bounds,
    make_walkable_callback,
)
from ghostopia_world.critters import (
    GROUND_KINDS,
    OVERHEAD_KINDS,
    Critter,
    CritterConfig,
    CritterKind,
    CritterState,
    critter_layer,
    select_follow_target,
    spawn_critters,
    step_critter,
)
from ghostopia_world.map import (
    DEFAULT_MAP_PATH,
    WorldMap,
    load_default_map,
    load_map,
    load_map_file,
)
from ghostopia_world.pathfinding import MAX_NODES, find_path
from ghostopia_world.placed_props import (
    DEFAULT_CATALOG_PATH,
    apply_placed_props,
    blocked_tiles,
    footprint_tiles,
    load_prop_footprints,
    load_prop_footprints_file,
    parse_placed_props,
)
from ghostopia_world.world_query import create_world_query

__all__ = [
    # map model
    "WorldMap",
    "load_map",
    "load_map_file",
    "load_default_map",
    "DEFAULT_MAP_PATH",
    # collision
    "WALKABLE",
    "BLOCKED",
    "in_bounds",
    "cell_walkable",
    "direction_from",
    "directional_blocked",
    "build_masks",
    "make_walkable_callback",
    # pathfinding
    "find_path",
    "MAX_NODES",
    # placed props
    "DEFAULT_CATALOG_PATH",
    "apply_placed_props",
    "blocked_tiles",
    "footprint_tiles",
    "load_prop_footprints",
    "load_prop_footprints_file",
    "parse_placed_props",
    # world query
    "create_world_query",
    # critters
    "Critter",
    "CritterConfig",
    "CritterKind",
    "CritterState",
    "GROUND_KINDS",
    "OVERHEAD_KINDS",
    "critter_layer",
    "select_follow_target",
    "spawn_critters",
    "step_critter",
]
