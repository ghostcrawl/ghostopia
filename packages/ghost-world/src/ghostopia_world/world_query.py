"""The read-only WorldQuery behaviors consume.

``create_world_query(world_map)`` returns an object satisfying the shared
:class:`ghostopia_shared.WorldQuery` Protocol: free workstations, section bounds, a
random reachable tile within bounds, and a random workstation in a section. All
reads — it never mutates the map. RNG is seedable for deterministic tests
(``random.Random(seed)``).
"""

from __future__ import annotations

import random
from collections import deque

from ghostopia_shared import Bounds, Point, WorldQuery

from ghostopia_world.map import WorldMap

_NEIGHBOURS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


class _WorldQuery:
    """Concrete read-only query over a :class:`WorldMap`."""

    def __init__(self, world_map: WorldMap, rng: random.Random | None = None) -> None:
        self._map = world_map
        self._rng = rng or random.Random()

    def free_workstations(self, section: str | None = None) -> list[Point]:
        return [
            Point(x=w.x, y=w.y)
            for w in self._map.workstations.values()
            if w.occupied_by is None
            and (section is None or w.section == section)
        ]

    def section_bounds(self, section: str) -> Bounds:
        return self._map.region_bounds(section)

    def random_reachable(
        self, bounds: Bounds, rng: random.Random | None = None
    ) -> Point:
        r = rng or self._rng
        candidates = [
            (x, y)
            for y in range(bounds.y, bounds.y + bounds.h)
            for x in range(bounds.x, bounds.x + bounds.w)
            if self._map.is_walkable(x, y)
        ]
        if not candidates:
            raise ValueError(f"no reachable tile within bounds {bounds!r}")
        x, y = r.choice(candidates)
        return Point(x=x, y=y)

    def random_reachable_global(
        self,
        from_point: Point | None = None,
        max_radius: int | None = None,
        rng: random.Random | None = None,
    ) -> Point:
        """Pick a walkable waypoint MAP-WIDE (not section-bounded) over the live
        collision grid (props/fences already folded into ``walkable``).

        When ``from_point`` is given, the waypoint is guaranteed A*-reachable from it
        (a BFS flood over the same directional walkable callback the pathfinder uses,
        so ghosts never target a tile they can't route to). ``max_radius`` caps the
        Chebyshev distance from ``from_point``. Seeded-RNG deterministic; read-only.
        """
        r = rng or self._rng
        walk = self._map.walkable_callback()
        if from_point is None:
            candidates = [
                (x, y)
                for y in range(self._map.height)
                for x in range(self._map.width)
                if self._map.is_walkable(x, y)
            ]
        else:
            sx, sy = int(from_point.x), int(from_point.y)
            reachable = self._flood(sx, sy, walk)
            candidates = [t for t in reachable if t != (sx, sy)]
            if max_radius is not None:
                candidates = [
                    (x, y)
                    for (x, y) in candidates
                    if abs(x - sx) <= max_radius and abs(y - sy) <= max_radius
                ]
        if not candidates:
            # Degenerate map (or a fully-fenced start): fall back to the start tile.
            if from_point is not None:
                return Point(x=int(from_point.x), y=int(from_point.y))
            raise ValueError("no reachable tile map-wide")
        x, y = r.choice(sorted(candidates))
        return Point(x=x, y=y)

    def _flood(self, sx: int, sy: int, walk) -> list[tuple[int, int]]:  # type: ignore[no-untyped-def]
        """BFS the set of tiles reachable from ``(sx, sy)`` under the directional
        walkable callback (static collision + edge masks — same rules as A*)."""
        if not walk(sx, sy):
            return []
        seen: set[tuple[int, int]] = {(sx, sy)}
        queue: deque[tuple[int, int]] = deque([(sx, sy)])
        while queue:
            cx, cy = queue.popleft()
            for dx, dy in _NEIGHBOURS:
                nx, ny = cx + dx, cy + dy
                nxt = (nx, ny)
                if nxt in seen:
                    continue
                if not walk(nx, ny, cx, cy):
                    continue
                seen.add(nxt)
                queue.append(nxt)
        return list(seen)

    def random_workstation(self, section: str) -> Point:
        stations = [
            w
            for w in self._map.workstations.values()
            if w.section == section
        ]
        if not stations:
            raise ValueError(f"no workstation in section {section!r}")
        w = self._rng.choice(stations)
        return Point(x=w.x, y=w.y)


def create_world_query(
    world_map: WorldMap, rng: random.Random | None = None
) -> WorldQuery:
    """Build the read-only :class:`WorldQuery` over ``world_map``.

    Pass a seeded ``random.Random(seed)`` for deterministic random_* results.
    """
    return _WorldQuery(world_map, rng)
