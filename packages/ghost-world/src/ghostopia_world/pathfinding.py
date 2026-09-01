"""Hand-rolled A* over the tile grid.

An ORIGINAL, dependency-free A* (4-neighbour, Manhattan heuristic, ``heapq`` open
set, a ``MAX_NODES`` safety cap). Collision is INJECTED via an
``is_walkable(x, y, from_x, from_y)`` callback so callers supply static collision +
per-cell occupancy + directional edge masks (deliberately NO
external pathfinding library). Unreachable goal -> ``[]`` (never raises); overflow of
the node budget -> ``[]``.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable

Tile = tuple[int, int]
IsWalkable = Callable[..., bool]

# Safety cap on nodes expanded before A* gives up and reports no-path. Prevents a
# runaway search on a huge/pathological grid.
MAX_NODES = 20_000

# 4-neighbour moves (no diagonals — the ghost walks orthogonally).
_NEIGHBOURS: tuple[Tile, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _heuristic(a: Tile, b: Tile) -> int:
    """Manhattan distance — admissible for 4-neighbour unit-cost movement."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _reconstruct(came_from: dict[Tile, Tile], goal: Tile) -> list[Tile]:
    path: list[Tile] = [goal]
    node = goal
    while node in came_from:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


def find_path(
    start: Tile,
    goal: Tile,
    width: int,
    height: int,
    is_walkable: IsWalkable,
    max_nodes: int = MAX_NODES,
) -> list[Tile]:
    """Return an ordered list of tiles from ``start`` to ``goal`` inclusive.

    ``is_walkable(x, y, from_x, from_y)`` decides whether a move INTO ``(x, y)``
    from ``(from_x, from_y)`` is allowed (static collision + directional masks +
    occupancy). Returns ``[]`` when the goal is unreachable or the ``max_nodes``
    budget is exhausted. Never raises on an unreachable goal.
    """
    # Reject a start/goal that isn't a legal standing tile (out-of-bounds or
    # blocked) up front — no move to validate direction against, so pass no origin.
    if not is_walkable(start[0], start[1]):
        return []
    if not is_walkable(goal[0], goal[1]):
        return []
    if start == goal:
        return [start]

    open_heap: list[tuple[int, int, Tile]] = []
    counter = 0  # tie-breaker keeps the heap ordering total + deterministic
    heapq.heappush(open_heap, (_heuristic(start, goal), counter, start))

    came_from: dict[Tile, Tile] = {}
    g_score: dict[Tile, int] = {start: 0}
    closed: set[Tile] = set()

    expanded = 0
    while open_heap:
        _f, _c, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            return _reconstruct(came_from, goal)

        closed.add(current)
        expanded += 1
        if expanded > max_nodes:
            return []

        cx, cy = current
        tentative_g = g_score[current] + 1
        for dx, dy in _NEIGHBOURS:
            nx, ny = cx + dx, cy + dy
            neighbour = (nx, ny)
            if neighbour in closed:
                continue
            # directional callback: entering (nx, ny) FROM (cx, cy)
            if not is_walkable(nx, ny, cx, cy):
                continue
            if tentative_g < g_score.get(neighbour, 1 << 30):
                came_from[neighbour] = current
                g_score[neighbour] = tentative_g
                counter += 1
                f = tentative_g + _heuristic(neighbour, goal)
                heapq.heappush(open_heap, (f, counter, neighbour))

    return []
