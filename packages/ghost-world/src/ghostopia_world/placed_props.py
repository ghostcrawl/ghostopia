"""Server-owned PLACED-PROPS layer + footprint→collision contribution.

A :class:`ghostopia_shared.PlacedProp` is fully described by ``{catalog_id, tile,
orientation, state}``; its FOOTPRINT (how many tiles it occupies) lives in the shared
prop catalog (``assets/props.catalog.json``), NOT in the placed entry — so this IS the
data model the Graveyard Builder edits. This module is the PURE bridge from that
layer to collision: it looks up each placed prop's footprint, computes the tiles it
covers, and merges them into the walkable grid so the hand-rolled A* routes AROUND placed
props (a ghost is never trapped — the footprint just becomes blocked terrain like any
obstacle). Nothing here touches PixiJS, the wall clock, or a live map object; the geometry
is all supplied by the caller (map DATA), matching the rest of :mod:`ghostopia_world`.

Footprints are authored in tile space in the DEFAULT orientation; the shipped props are
either footprint-symmetric (crypt/mausoleum 2x2) or use e/w MIRROR orientations that keep
the same dims, so orientation never changes the covered tiles for the shipped catalog.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ghostopia_shared import PlacedProp

# The prop catalog ships as DATA at the ghostopia workspace root: ghostopia/assets/.
# placed_props.py lives at packages/ghost-world/src/ghostopia_world/placed_props.py ->
# parents[4] is the ghostopia/ root.
DEFAULT_CATALOG_PATH: Path = (
    Path(__file__).resolve().parents[4] / "assets" / "props.catalog.json"
)

# catalog_id -> (footprint_w, footprint_h) in tiles.
Footprints = dict[str, tuple[int, int]]


def load_prop_footprints(catalog_data: dict[str, Any]) -> Footprints:
    """Extract ``catalog_id -> (w, h)`` footprints from a decoded prop-catalog dict."""
    props = catalog_data.get("props")
    if not isinstance(props, dict):
        raise ValueError('invalid prop catalog: expected a "props" object')
    out: Footprints = {}
    for cid, defn in props.items():
        fp = defn.get("footprint") if isinstance(defn, dict) else None
        if not isinstance(fp, dict) or "w" not in fp or "h" not in fp:
            raise ValueError(f'prop "{cid}" missing a footprint {{w, h}}')
        w = int(fp["w"])
        h = int(fp["h"])
        if w < 1 or h < 1:
            raise ValueError(f'prop "{cid}" footprint needs w>=1, h>=1')
        out[cid] = (w, h)
    return out


def load_prop_footprints_file(path: str | Path = DEFAULT_CATALOG_PATH) -> Footprints:
    """Load + parse ``props.catalog.json`` into a footprint lookup."""
    with open(path) as f:
        return load_prop_footprints(json.load(f))


def parse_placed_props(entries: Iterable[dict[str, Any]] | None) -> list[PlacedProp]:
    """Normalize the map JSON ``placed_props`` list into validated models."""
    return [PlacedProp(**e) for e in (entries or ())]


def footprint_tiles(prop: PlacedProp, footprints: Footprints) -> list[tuple[int, int]]:
    """The tile coordinates a placed prop occupies (top-left ``tile`` + its footprint box).

    An unknown ``catalog_id`` falls back to a 1x1 footprint (a missing catalog entry never
    crashes collision — the prop simply occupies its own tile).
    """
    w, h = footprints.get(prop.catalog_id, (1, 1))
    ox, oy = prop.tile
    return [(ox + dx, oy + dy) for dy in range(h) for dx in range(w)]


def blocked_tiles(
    placed: Iterable[PlacedProp], footprints: Footprints
) -> set[tuple[int, int]]:
    """The union of every placed prop's footprint tiles (the collision contribution)."""
    out: set[tuple[int, int]] = set()
    for prop in placed:
        out.update(footprint_tiles(prop, footprints))
    return out


def apply_placed_props(
    walkable: Sequence[Sequence[int]],
    placed: Iterable[PlacedProp],
    footprints: Footprints,
) -> list[list[int]]:
    """Return a NEW walkable grid with every placed prop's footprint marked blocked.

    Pure: the input grid is copied, never mutated. Footprint tiles outside the grid are
    ignored (a prop hanging off the edge blocks only its in-bounds tiles). The result is
    the grid A* walks, so placed props become obstacles the pathfinder routes around.
    """
    grid = [list(row) for row in walkable]
    height = len(grid)
    width = len(grid[0]) if height else 0
    for x, y in blocked_tiles(placed, footprints):
        if 0 <= x < width and 0 <= y < height:
            grid[y][x] = 0
    return grid
