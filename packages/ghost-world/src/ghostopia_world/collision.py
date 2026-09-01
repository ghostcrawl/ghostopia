"""Collision primitives for the tile world.

The walkable grid is a ``height x width`` matrix of 0/1 (0 = blocked). A
*directional edge mask* additionally blocks entry into a specific tile through a
specific side — modelling one-way ledges / crypt thresholds without a second full
grid. All geometry is supplied by the caller (loaded from
map DATA); nothing here hardcodes a map.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

WALKABLE = 1
BLOCKED = 0

# Sides of a tile, named by the edge you enter THROUGH.
#   moving from (x-1, y) -> (x, y)  enters through the West side  "W"
#   moving from (x+1, y) -> (x, y)  enters through the East side  "E"
#   moving from (x, y-1) -> (x, y)  enters through the North side "N"
#   moving from (x, y+1) -> (x, y)  enters through the South side "S"
Side = str


def in_bounds(x: int, y: int, width: int, height: int) -> bool:
    """True iff (x, y) is inside the [0, width) x [0, height) tile field."""
    return 0 <= x < width and 0 <= y < height


def cell_walkable(
    grid: Sequence[Sequence[int]], x: int, y: int, width: int, height: int
) -> bool:
    """Static (directionless) walkability: in-bounds AND grid cell != BLOCKED."""
    if not in_bounds(x, y, width, height):
        return False
    return grid[y][x] != BLOCKED


def direction_from(from_x: int, from_y: int, x: int, y: int) -> Side | None:
    """Which side of (x, y) is entered when arriving from (from_x, from_y).

    Returns None for non-adjacent / diagonal / same-cell moves (no directional
    mask applies to those).
    """
    dx = x - from_x
    dy = y - from_y
    if dx == 1 and dy == 0:
        return "W"  # came from the west, entered through the west side
    if dx == -1 and dy == 0:
        return "E"
    if dx == 0 and dy == 1:
        return "N"
    if dx == 0 and dy == -1:
        return "S"
    return None


def directional_blocked(
    masks: Mapping[tuple[int, int], frozenset[Side]],
    x: int,
    y: int,
    from_x: int,
    from_y: int,
) -> bool:
    """True iff entering (x, y) from (from_x, from_y) is blocked by an edge mask."""
    blocked_sides = masks.get((x, y))
    if not blocked_sides:
        return False
    side = direction_from(from_x, from_y, x, y)
    return side is not None and side in blocked_sides


def build_masks(
    entries: Sequence[Mapping[str, Any]] | None,
) -> dict[tuple[int, int], frozenset[Side]]:
    """Normalize the JSON ``directional_collision`` list into a lookup table.

    Each entry is ``{"x": int, "y": int, "blocked": ["W", ...]}``.
    """
    out: dict[tuple[int, int], frozenset[Side]] = {}
    for e in entries or ():
        x = int(e["x"])
        y = int(e["y"])
        sides = frozenset(str(s).upper() for s in e.get("blocked", ()))
        if sides:
            out[(x, y)] = sides
    return out


def make_walkable_callback(
    grid: Sequence[Sequence[int]],
    width: int,
    height: int,
    masks: Mapping[tuple[int, int], frozenset[Side]] | None = None,
) -> Callable[..., bool]:
    """Return the ``is_walkable(x, y, from_x=None, from_y=None)`` callback A* uses.

    Static collision + bounds always apply; the directional mask applies only when
    a ``from_x/from_y`` origin is supplied (i.e. an actual move into the tile).
    """
    masks = masks or {}

    def is_walkable(
        x: int, y: int, from_x: int | None = None, from_y: int | None = None
    ) -> bool:
        if not cell_walkable(grid, x, y, width, height):
            return False
        if from_x is not None and from_y is not None:
            if directional_blocked(masks, x, y, from_x, from_y):
                return False
        return True

    return is_walkable
