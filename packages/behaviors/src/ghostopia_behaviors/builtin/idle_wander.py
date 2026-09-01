"""``IdleWander`` — an ambient behavior.

Ghosts are ghosts: when idle and a dwell timer expires, they ROAM the whole map —
picking a reachable waypoint MAP-WIDE (``ctx.world.random_reachable_global`` over the
live collision grid, so props/fences are routed around and never stood on) and
occasionally drifting back toward their home section so they don't stray forever. It
never opens a browser — pure ambient motion so the graveyard feels alive between tasks.
Tasked ghosts (a real work destination) are handled by their work behavior; this one
only fires while ``ctx.ghost.is_idle()``. Deterministic under a seeded ``ctx.rng``.
"""

from __future__ import annotations

from ghostopia_shared import Bounds, EndReason, GhostEvent
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class IdleWanderParams(BaseModel):
    """Parameters for the ambient IdleWander behavior."""

    dwell_ms: float = Field(
        default=180.0,
        ge=0.0,
        description="Initial settle before the FIRST roam step; subsequent steps use the "
        "jittered ``pause_min_ms``..``pause_max_ms`` window (a brief human pause, not "
        "a full stationary sit).",
    )
    pause_min_ms: float = Field(
        default=60.0,
        ge=0.0,
        description="Lower bound of the jittered human pause between chained waypoints — the "
        "ghost re-emits its next walk within this window so it always has a next segment "
        "queued (continuous motion, no frozen-Zzz dwell).",
    )
    pause_max_ms: float = Field(
        default=260.0,
        ge=0.0,
        description="Upper bound of the jittered human pause between chained waypoints. Kept "
        "short so an idle ghost reads as continuously, purposefully moving.",
    )
    roam_radius: int | None = Field(
        default=None,
        ge=1,
        description="Optional Chebyshev cap on how far a single roam step travels; "
        "None = anywhere reachable on the map.",
    )
    home_bias: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Probability a roam step drifts back toward the resting-graveyard area "
        "(the shared graves) instead of roaming map-wide — a LOOSE anchor, NOT a designated "
        "per-ghost home (graves are transient shared rest, never an owned home).",
    )
    home_every: int = Field(
        default=4,
        ge=1,
        description="Force a drift-back step toward the resting area at least once every N "
        "roams so a ghost that keeps rolling map-wide still drifts home eventually.",
    )


class IdleWander:
    """Ambient map-wide roam with a loose home anchor, on a dwell timer."""

    name = "idle_wander"

    def __init__(self) -> None:
        self._params = IdleWanderParams()
        self._dwell_remaining = 0.0
        self._since_home = 0

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = IdleWanderParams.model_validate(ctx.task.params)
        self._dwell_remaining = self._params.dwell_ms
        self._since_home = 0

    def _bounds(self, ctx: BehaviorContext) -> Bounds | None:
        if ctx.section is None:
            return None
        if ctx.section.bounds is not None:
            return ctx.section.bounds
        try:
            return ctx.world.section_bounds(ctx.section.id)
        except (KeyError, ValueError):
            return None

    def _next_pause_ms(self, ctx: BehaviorContext) -> float:
        """A short jittered human pause before the next chained waypoint.

        Learned from natural human-flow pacing (dwell/read timing
        is a jittered distribution, never a fixed constant) — ORIGINAL implementation: a
        uniform draw over ``pause_min_ms``..``pause_max_ms`` on the seeded rng so the cadence
        reads organic yet stays deterministic under test."""
        lo = self._params.pause_min_ms
        hi = max(lo, self._params.pause_max_ms)
        return lo if hi <= lo else ctx.rng.uniform(lo, hi)

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        self._dwell_remaining -= dt_ms
        if self._dwell_remaining > 0.0:
            return
        # Re-arm with a SHORT jittered pause (not a full dwell) so the ghost chains straight
        # into its next waypoint on arrival — a continuous supply of paths for the client's
        # segment-lerp, never a long stationary sit. NO per-ghost grave is written
        # here: roaming is transient, the resting area is a loose shared anchor only.
        self._dwell_remaining = self._next_pause_ms(ctx)
        if not ctx.ghost.is_idle():
            return

        bounds = self._bounds(ctx)
        # Drift home when the home-bias roll hits OR the every-N safety valve trips —
        # but only if we actually know a home section box to drift toward.
        drift_home = bounds is not None and (
            self._since_home >= self._params.home_every
            or ctx.rng.random() < self._params.home_bias
        )
        if drift_home and bounds is not None:
            dest = ctx.world.random_reachable(bounds, rng=ctx.rng)
            self._since_home = 0
        else:
            pos = ctx.ghost.position()
            dest = ctx.world.random_reachable_global(
                from_point=pos, max_radius=self._params.roam_radius, rng=ctx.rng
            )
            self._since_home += 1
        ctx.ghost.walk_to(dest)
        await ctx.emit_event("ghost.wander", {"to": {"x": dest.x, "y": dest.y}})

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        return None


behaviors.register(
    "idle_wander",
    IdleWander,
    BehaviorMeta(
        kind="ambient",
        needs=[],
        label="Idle Wander",
        param_schema=IdleWanderParams,
        examples=[{"title": "slow ambient drift", "params": {"dwell_ms": 2000.0}}],
        overlay="idle",
    ),
)
