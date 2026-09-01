"""Server-side autonomous critter runtime.

Owns a capped set of :class:`~ghostopia_world.Critter` WorldEntities, steps their PURE
ghost-world FSM each tick, and broadcasts their positions/state over the authed WS so the
thin renderer can draw them. A ``critter.pet`` control verb (a click ack) broadcasts a
``critter.petted`` so the renderer flashes a heart/spark. Shared by both the STAGE-2 sim
(:mod:`sim_runtime`) and the STAGE-3 live app (:mod:`gc_event_source`) — the critters live
in the world regardless of which runtime drives the ghosts.

The floor never moves: critters are ENTITIES the renderer depth-sorts / floats above the
ground; the static-floor invariant is untouched. Deterministic given the seed.
"""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable, Mapping

from ghostopia_shared import Bounds, Envelope, Point
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_world import (
    Critter,
    CritterConfig,
    CritterKind,
    WorldMap,
    spawn_critters,
    step_critter,
)

Broadcast = Callable[[Envelope], Awaitable[None]]

_SPAWN_TYPE = "critter.spawned"
_UPDATE_TYPE = "critter.update"
_PET_TYPE = "critter.petted"


class CritterRuntime:
    """Manages the graveyard critters for one running world (sim OR live)."""

    def __init__(
        self,
        broadcast: Broadcast,
        world_map: WorldMap,
        *,
        seed: int = 1337,
        counts: Mapping[CritterKind, int] | None = None,
        max_total: int = 4,
    ) -> None:
        self._broadcast = broadcast
        self._map = world_map
        self._rng = random.Random(seed)
        self._cfg = CritterConfig()
        self._counts = dict(
            counts
            or {CritterKind.CAT: 1, CritterKind.WISP: 1, CritterKind.BAT: 1}
        )
        self._max_total = max_total
        self._critters: list[Critter] = []

    # -- geometry --------------------------------------------------------------

    def _roam_bounds(self) -> Bounds:
        return Bounds(x=0, y=0, w=self._map.width, h=self._map.height)

    def _is_walkable_px(self, px: float, py: float) -> bool:
        """Pixel walkability for the ground cat (pixel → tile)."""
        tx = int(px // self._map.tile_size)
        ty = int(py // self._map.tile_size)
        return self._map.is_walkable(tx, ty)

    # -- lifecycle -------------------------------------------------------------

    async def spawn(self) -> None:
        """Spawn the capped critter set + announce each so a fresh client renders them."""
        self._critters = spawn_critters(
            self._counts,
            self._roam_bounds(),
            tile_size=self._map.tile_size,
            rng=self._rng,
            is_walkable=self._is_walkable_px,
            max_total=self._max_total,
        )
        for c in self._critters:
            await self._broadcast(
                serialize_envelope(type=_SPAWN_TYPE, ts=time.time(), payload=c.snapshot())
            )

    async def step(self, dt_ms: float, ghost_positions: Mapping[str, Point]) -> None:
        """Advance every critter's FSM by ``dt_ms`` + broadcast a batch position update.

        ``ghost_positions`` maps ghost_id → its WORLD-PIXEL position (the follow candidates);
        pass an empty mapping when positions aren't tracked (critters still wander/idle)."""
        if not self._critters:
            return
        bounds = self._roam_bounds()
        for c in self._critters:
            step_critter(
                c,
                dt_ms,
                ghost_positions,
                bounds=bounds,
                tile_size=self._map.tile_size,
                cfg=self._cfg,
                rng=self._rng,
                is_walkable=self._is_walkable_px,
            )
        await self._broadcast(
            serialize_envelope(
                type=_UPDATE_TYPE,
                ts=time.time(),
                payload={"critters": [c.snapshot() for c in self._critters]},
            )
        )

    async def pet(self, critter_id: str) -> bool:
        """Ack a pet on a KNOWN critter (broadcast ``critter.petted``). Ignore an unknown id."""
        if not any(c.id == critter_id for c in self._critters):
            return False
        await self._broadcast(
            serialize_envelope(type=_PET_TYPE, ts=time.time(), payload={"id": critter_id})
        )
        return True

    @property
    def critters(self) -> list[Critter]:
        return list(self._critters)


__all__ = ["CritterRuntime"]
