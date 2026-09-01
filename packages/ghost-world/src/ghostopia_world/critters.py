"""Autonomous graveyard critters — pure WorldEntity FSM.

ghostopia's ORIGINAL graveyard idiom (NOT the reference repos' office pets): a black
**cat** roams the ground, a will-o'-**wisp** and a **bat** drift overhead. Each is a
:class:`Critter` WorldEntity driven by a deterministic ``wander / idle / follow`` FSM:

* **idle** — hold still a beat, then either pick a random reachable **wander** target or,
  if a ghost is close, **follow** it;
* **wander** — amble toward a random target within its roam bounds, then settle to idle;
* **follow** — trail the nearest ghost within ``follow_radius`` for a bounded time, then
  give up and wander again (so a critter never latches forever).

Everything here is PURE + server-authoritative + deterministic given a seeded
:class:`random.Random` — no PixiJS, no wall clock, no map object. Positions are in WORLD
PIXELS (the same space the ghost driver + renderer use); walkability for the ground cat is
supplied as a callback so this module never imports the map. The server (sim_runtime /
gc_event_source) owns the ``Critter`` list, steps it each tick, and broadcasts positions;
the renderer draws them. Count is capped by the caller (:func:`spawn_critters`).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from ghostopia_shared import Bounds, Point

__all__ = [
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


class CritterKind(StrEnum):
    """The three original graveyard critters (NO reference-repo pets)."""

    CAT = "cat"  # black cat — roams the GROUND (depth-sorted with the ghosts)
    WISP = "wisp"  # will-o'-wisp — drifts OVERHEAD
    BAT = "bat"  # bat — flaps OVERHEAD


class CritterState(StrEnum):
    """The critter FSM: idle → wander/follow → idle."""

    IDLE = "idle"
    WANDER = "wander"
    FOLLOW = "follow"


#: Kinds that live on the ground layer (walkability-clamped, depth-sorted with ghosts).
GROUND_KINDS: frozenset[CritterKind] = frozenset({CritterKind.CAT})
#: Kinds that fly on the overhead layer (ignore walkability).
OVERHEAD_KINDS: frozenset[CritterKind] = frozenset({CritterKind.WISP, CritterKind.BAT})


def critter_layer(kind: CritterKind) -> str:
    """`"ground"` for the cat (depth-sorted with ghosts), `"overhead"` for wisp/bat."""
    return "ground" if kind in GROUND_KINDS else "overhead"


@dataclass(frozen=True)
class CritterConfig:
    """Tunables for the critter FSM (all PIXELS / MILLISECONDS; deterministic)."""

    #: ground/fly travel speed in world px per ms.
    ground_speed: float = 0.045
    fly_speed: float = 0.060
    #: idle dwell window (ms) — a uniform draw in [min, max].
    idle_min_ms: float = 700.0
    idle_max_ms: float = 2600.0
    #: max time spent wandering to a single target before settling to idle (ms).
    wander_max_ms: float = 5000.0
    #: a ghost within this radius (px) makes a critter eligible to FOLLOW it.
    follow_radius: float = 96.0
    #: while following, drop the ghost once it is farther than this (px).
    follow_drop: float = 190.0
    #: a follow lasts at most this long (ms) before the critter wanders off.
    follow_max_ms: float = 5200.0
    #: probability (0..1) that an eligible idle critter chooses FOLLOW over WANDER.
    follow_prob: float = 0.45
    #: distance (px) at which a target counts as reached.
    reach_eps: float = 3.5
    #: how close behind a followed ghost the critter aims to sit (px).
    follow_gap: float = 14.0


@dataclass
class Critter:
    """One autonomous critter WorldEntity (position in WORLD PIXELS)."""

    id: str
    kind: CritterKind
    x: float
    y: float
    state: CritterState = CritterState.IDLE
    target: Point | None = None
    follow_ghost_id: str | None = None
    #: elapsed time (ms) in the CURRENT state — drives idle-dwell / wander/follow timeouts.
    state_ms: float = 0.0
    #: dwell budget for the current IDLE (ms), redrawn on entry.
    idle_budget_ms: float = 0.0
    #: last horizontal travel sign (−1/0/+1) — the renderer flips the cat by this.
    facing: int = 1

    @property
    def layer(self) -> str:
        return critter_layer(self.kind)

    def snapshot(self) -> dict[str, object]:
        """The wire shape the server broadcasts (pixel coords + coarse state + facing)."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "state": self.state.value,
            "facing": self.facing,
            "layer": self.layer,
        }


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def select_follow_target(
    critter: Critter, ghosts: Mapping[str, Point], radius: float
) -> str | None:
    """The nearest ghost within ``radius`` px of ``critter`` (or ``None`` if none).

    PURE + deterministic (nearest wins; ties break on the ghost_id sort order), so the
    follow-target choice is unit-testable without a running world."""
    best_id: str | None = None
    best_d = radius
    for gid in sorted(ghosts):
        p = ghosts[gid]
        d = _dist(critter.x, critter.y, p.x, p.y)
        if d <= best_d:
            best_d = d
            best_id = gid
    return best_id


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _random_target(bounds: Bounds, tile_size: int, rng: random.Random) -> Point:
    """A random PIXEL point inside ``bounds`` (a tile-rect), inset by half a tile."""
    x0 = (bounds.x + 0.5) * tile_size
    x1 = (bounds.x + bounds.w - 0.5) * tile_size
    y0 = (bounds.y + 0.5) * tile_size
    y1 = (bounds.y + bounds.h - 0.5) * tile_size
    if x1 < x0:
        x0 = x1 = (bounds.x + bounds.w / 2.0) * tile_size
    if y1 < y0:
        y0 = y1 = (bounds.y + bounds.h / 2.0) * tile_size
    return Point(x=rng.uniform(x0, x1), y=rng.uniform(y0, y1))


def _enter_idle(c: Critter, rng: random.Random, cfg: CritterConfig) -> None:
    c.state = CritterState.IDLE
    c.state_ms = 0.0
    c.target = None
    c.follow_ghost_id = None
    c.idle_budget_ms = rng.uniform(cfg.idle_min_ms, cfg.idle_max_ms)


def _enter_wander(
    c: Critter, bounds: Bounds, tile_size: int, rng: random.Random
) -> None:
    c.state = CritterState.WANDER
    c.state_ms = 0.0
    c.follow_ghost_id = None
    c.target = _random_target(bounds, tile_size, rng)


def _enter_follow(c: Critter, ghost_id: str) -> None:
    c.state = CritterState.FOLLOW
    c.state_ms = 0.0
    c.follow_ghost_id = ghost_id


def _advance_toward(
    c: Critter,
    target: Point,
    dt_ms: float,
    speed: float,
    is_walkable: Callable[[float, float], bool] | None,
) -> bool:
    """Move ``c`` toward ``target`` by ``speed*dt`` px. Returns True once within reach.

    A ground critter (``is_walkable`` supplied) never steps onto a blocked pixel — a blocked
    step is skipped (it will re-target on the next idle), so critters never mutate the floor
    or clip through walls."""
    dx = target.x - c.x
    dy = target.y - c.y
    dist = math.hypot(dx, dy)
    if dist <= 1e-6:
        return True
    step = min(dist, speed * dt_ms)
    nx = c.x + dx / dist * step
    ny = c.y + dy / dist * step
    if is_walkable is not None and not is_walkable(nx, ny):
        return False  # blocked — hold position; the FSM will pick a fresh target
    if abs(dx) > 0.01:
        c.facing = 1 if dx > 0 else -1
    c.x = nx
    c.y = ny
    return dist - step <= 1e-6


def step_critter(
    c: Critter,
    dt_ms: float,
    ghosts: Mapping[str, Point],
    *,
    bounds: Bounds,
    tile_size: int,
    cfg: CritterConfig,
    rng: random.Random,
    is_walkable: Callable[[float, float], bool] | None = None,
) -> None:
    """Advance one critter's FSM by ``dt_ms`` (mutates ``c``; PURE given ``rng``).

    ``ghosts`` maps ghost_id → its WORLD-PIXEL position (the follow candidates). ``bounds``
    is the critter's roam rect (TILE coords); ``is_walkable(px, py)`` gates the ground cat
    (pass ``None`` for the flying wisp/bat). Deterministic for a seeded ``rng``.
    """
    c.state_ms += dt_ms
    speed = cfg.ground_speed if c.kind in GROUND_KINDS else cfg.fly_speed
    walk = is_walkable if c.kind in GROUND_KINDS else None

    if c.state is CritterState.IDLE:
        if c.state_ms >= c.idle_budget_ms:
            ft = select_follow_target(c, ghosts, cfg.follow_radius)
            if ft is not None and rng.random() < cfg.follow_prob:
                _enter_follow(c, ft)
            else:
                _enter_wander(c, bounds, tile_size, rng)
        return

    if c.state is CritterState.WANDER:
        target = c.target
        if target is None or c.state_ms >= cfg.wander_max_ms:
            _enter_idle(c, rng, cfg)
            return
        if _advance_toward(c, target, dt_ms, speed, walk):
            _enter_idle(c, rng, cfg)
        return

    if c.state is CritterState.FOLLOW:
        gid = c.follow_ghost_id
        gpos = ghosts.get(gid) if gid is not None else None
        if (
            gpos is None
            or c.state_ms >= cfg.follow_max_ms
            or _dist(c.x, c.y, gpos.x, gpos.y) > cfg.follow_drop
        ):
            _enter_wander(c, bounds, tile_size, rng)
            return
        # aim for a spot a short gap behind the ghost so the critter trails, not overlaps.
        d = _dist(c.x, c.y, gpos.x, gpos.y)
        if d > cfg.follow_gap:
            _advance_toward(c, gpos, dt_ms, speed, walk)
        return


def spawn_critters(
    counts: Mapping[CritterKind, int],
    bounds: Bounds,
    *,
    tile_size: int,
    rng: random.Random,
    is_walkable: Callable[[float, float], bool] | None = None,
    max_total: int = 4,
) -> list[Critter]:
    """Spawn a CAPPED set of critters at random reachable points inside ``bounds``.

    ``counts`` requests per-kind counts; the total is hard-capped at ``max_total``
    ("capped count"). Ground critters are placed on a walkable pixel (via ``is_walkable``);
    flyers anywhere in bounds. Deterministic for a seeded ``rng``."""
    out: list[Critter] = []
    idx = 0
    for kind in (CritterKind.CAT, CritterKind.WISP, CritterKind.BAT):
        want = max(0, int(counts.get(kind, 0)))
        for _ in range(want):
            if len(out) >= max_total:
                return out
            walk = is_walkable if kind in GROUND_KINDS else None
            pos = _spawn_point(bounds, tile_size, rng, walk)
            c = Critter(id=f"critter-{kind.value}-{idx}", kind=kind, x=pos.x, y=pos.y)
            _enter_idle(c, rng, CritterConfig())
            out.append(c)
            idx += 1
    return out


def _spawn_point(
    bounds: Bounds,
    tile_size: int,
    rng: random.Random,
    is_walkable: Callable[[float, float], bool] | None,
) -> Point:
    """A random spawn pixel inside ``bounds`` (walkable for a ground critter, best-effort)."""
    for _ in range(16):
        p = _random_target(bounds, tile_size, rng)
        if is_walkable is None or is_walkable(p.x, p.y):
            return p
    return _random_target(bounds, tile_size, rng)
