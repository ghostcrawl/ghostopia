"""The concrete GhostHandle.

``create_ghost_handle(ghost_id, command_sink, map)`` builds the concrete implementation of
the shared :class:`ghostopia_shared.GhostHandle` Protocol — the NARROW command surface both
Behaviors and the :class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver`
speak. Every method pushes ONE server-authoritative visual command
(:class:`ghostopia_shared.GhostCommand`) onto the sink (which the server broadcasts over the
WS); movement methods compute an A* path via :func:`ghostopia_world.find_path` and carry it
on the command. Read-only queries reflect the ghost's motion state.

The ghost entity stays DUMB: this handle holds NO decision logic — it only translates
imperative commands into visual commands + tracks motion state. Behaviors decide; the handle
moves/animates (three-layer seam).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from ghostopia_shared import GhostCommand, Point
from ghostopia_world import WorldMap, find_path

CommandSink = Callable[[GhostCommand], None]

# The default status-bubble kind for ``say`` (matches the Protocol default sentinel).
_DEFAULT_BUBBLE = "status"

# 8-way compass facing — the SAME buckets the client's ``facingFromVector`` uses
# (ghost-art/facing.ts): screen-space +dx=east, +dy=DOWN=south; atan2(dy,dx) sectored into
# 45deg buckets. Kept byte-parity with the client SECTOR order so a server-emitted facing
# selects the exact clip the renderer would (no new art). A zero vector rests facing south.
_FACING_SECTOR: tuple[str, ...] = ("e", "se", "s", "sw", "w", "nw", "n", "ne")
_FACING_DEFAULT = "s"


def _facing_toward(from_x: float, from_y: float, to_x: float, to_y: float) -> str:
    """The 8-way compass facing pointing from ``(from)`` toward ``(to)`` (screen space)."""
    dx = to_x - from_x
    dy = to_y - from_y
    if dx == 0.0 and dy == 0.0:
        return _FACING_DEFAULT
    angle = math.atan2(dy, dx)
    idx = math.floor(angle / (math.pi / 4.0) + 0.5 + 1e-9)
    idx = ((idx % 8) + 8) % 8
    return _FACING_SECTOR[idx]


def _tile(p: Point) -> tuple[int, int]:
    return (round(p.x), round(p.y))


class _GhostHandle:
    """Concrete :class:`ghostopia_shared.GhostHandle` over a :class:`WorldMap`.

    Structurally satisfies the shared Protocol; ``emit_walk`` is an extra low-level hook the
    driver uses to attach a pre-planned path (not part of the narrow Protocol behaviors see).
    """

    def __init__(
        self,
        ghost_id: str,
        command_sink: CommandSink,
        world_map: WorldMap,
        start: Point | None = None,
        section_id: str | None = None,
    ) -> None:
        self._id = ghost_id
        self._sink = command_sink
        self._map = world_map
        self._position: Point = start if start is not None else Point(x=0.0, y=0.0)
        self._walking = False
        self._at_ws = False
        self._last_walk_mode: str | None = None
        self._last_walk_tiles: int = 0
        # The section this ghost works in (its department). When set, the ghost prefers a free
        # workstation IN ITS OWN section, so a department's ghosts sit at that department's
        # computers instead of all piling onto the globally-first workstation.
        self._section_id = section_id
        # 196 FIX 3: the workstation seat this ghost currently HOLDS (its ``Workstation.id``), or
        # None when at rest. Reserved on ``walk_to_workstation`` (sets ``occupied_by`` at RUNTIME
        # so same-department ghosts pick DIFFERENT seats) and released on departure/despawn, so
        # a seat is never double-booked and never leaks. Load-time occupancy stays in map.py.
        self._reserved_ws_id: str | None = None
        # The idle/rest ``wander`` beat picks a RANDOM grave (not the nearest), but
        # determinism is a project invariant (no bare RNG) — so the draw is SEEDED by the ghost id.
        # ``random.Random(str)`` hashes the seed via SHA-512, giving a stable per-ghost sequence
        # across processes/runs (independent of PYTHONHASHSEED) while varying by ghost id.
        self._rng = random.Random(ghost_id)

    # -- command emission ------------------------------------------------------

    def _emit(self, kind: str, **args: object) -> None:
        self._sink(GhostCommand(kind=kind, ghost_id=self._id, args=dict(args)))

    def _plan_path(self, dest: Point) -> list[list[int]]:
        tiles = find_path(
            _tile(self._position),
            _tile(dest),
            self._map.width,
            self._map.height,
            self._map.walkable_callback(),
        )
        return [[x, y] for (x, y) in tiles]

    def emit_walk(
        self, mode: str, dest: Point, path: list[list[int]] | None = None
    ) -> None:
        """Emit a walk command toward ``dest`` (planning the A* path if not supplied)."""
        resolved = path if path is not None else self._plan_path(dest)
        self._emit(
            "walk",
            mode=mode,
            destination={"x": dest.x, "y": dest.y},
            path=resolved,
        )
        self._position = dest
        self._walking = True
        self._at_ws = False
        # remember this walk's intent + length so a server-side arrival driver (the pool mover)
        # can, after a travel beat, complete it as a workstation vs home/idle arrival.
        self._last_walk_mode = mode
        self._last_walk_tiles = len(resolved)

    def last_walk_mode(self) -> str | None:
        """The mode of the most recent walk (``"workstation"`` / ``"home"`` / …) — read by the
        pool's arrival driver to decide ``arrive(at_workstation=…)``. ``None`` before any walk."""
        return self._last_walk_mode

    def last_walk_tiles(self) -> int:
        """The A* path length (tiles) of the most recent walk — lets the arrival driver pace the
        server-side arrival roughly to the client's walk animation."""
        return self._last_walk_tiles

    def set_position(self, position: Point) -> None:
        """Place the ghost (server-authoritative). The composition layer sets a ghost's home
        grave as its start so the first A* walk plans from the right tile; emits no command
        (the spawn envelope carries the initial position to the renderer)."""
        self._position = position

    def arrive(self, *, at_workstation: bool = False) -> None:
        """Mark a walk as COMPLETE (the ghost reached its destination).

        The composition layer (sim clock / real arrival event) calls this when the ghost's
        path finishes: it clears the ``walking`` flag and, for a workstation walk, sets
        ``at_workstation`` True so a mounted behavior's ``at_workstation()`` gate opens and
        the work phase begins; an idle/home arrival clears both so the ghost reads idle
        again (and can wander once more). It emits NO command — arrival is a state fact the
        renderer already animated toward the destination + path carried on the walk command.
        """
        self._walking = False
        self._at_ws = at_workstation

    # -- movement (shared GhostHandle Protocol) --------------------------------

    def walk_to(self, dest: Point) -> None:
        # 196 FIX 3: an ad-hoc move away from the desk releases the held seat (the ghost is no
        # longer sitting there); a fresh workstation walk re-reserves.
        self._release_workstation()
        self.emit_walk("to", dest)

    def walk_to_workstation(self) -> None:
        # 196 FIX 3: RESERVE a seat (set ``occupied_by`` at runtime) so a department's other
        # ghosts skip it and spread across the section's computers instead of overlapping.
        ws = self._reserve_workstation()
        self.emit_walk("workstation", Point(x=float(ws[0]), y=float(ws[1])))

    def walk_home(self) -> None:
        # 196 FIX 3: heading home frees the ghost's seat so a queued same-section ghost can take it.
        self._release_workstation()
        grave = self._pick_grave()
        self.emit_walk("home", Point(x=float(grave[0]), y=float(grave[1])))

    def walk_to_section_workstation(self, section_id: str) -> None:
        """Walk to + reserve a workstation seat in a NAMED, possibly non-rostered section.

        The cross-section movement primitive the staged pipeline needs: a solo ghost hops from a
        research desk to an extraction desk to a verify desk; a baton stage ghost sits at its stage
        section's desk regardless of the department it will ultimately deliver to. The prior seat is
        RELEASED first (no foreign-seat leak / double-book), then a seat IN ``section_id`` is
        reserved (``occupied_by`` set at runtime so peers skip it). Stage hops stay mode
        ``"workstation"`` — ``_drive_arrivals`` gates on ``at_workstation()`` and never stalls."""
        # release-first so the target-section seat is chosen fresh (not the held-seat short-circuit).
        self._release_workstation()
        ws = self._reserve_workstation(target_section=section_id)
        self.emit_walk("workstation", Point(x=float(ws[0]), y=float(ws[1])))

    def walk_to_section_drop(self, section_id: str | None = None) -> None:
        """Walk to a department to DELIVER a finished result (deliver beat).

        The drop point is the centre tile of the target region — the ghost visibly carries its work
        INTO the department instead of abandoning it to a grave. ``section_id`` names an EXPLICIT
        origin department (a baton ghost delivers to a department it is NOT rostered to); the default
        (``None``) keeps the original own-section behavior. Emits a ``"deliver"`` walk (mode is NOT
        ``"workstation"``, so the server arrival clock lands the ghost idle, ready to rest). With no
        target and no own section, it degrades to :meth:`walk_home`."""
        # 196 FIX 3: delivering back to the department also vacates the workstation seat.
        self._release_workstation()
        target = section_id if section_id is not None else self._section_id
        if target is None:
            self.walk_home()
            return
        b = self._map.region_bounds(target)
        cx = int(b.x + b.w // 2)
        cy = int(b.y + b.h // 2)
        self.emit_walk("deliver", Point(x=float(cx), y=float(cy)))

    def wander(self) -> None:
        """Walk to a RANDOM (seeded) grave for the idle/rest beat.

        Unlike :meth:`walk_home` (nearest grave), ``wander`` draws a random grave from ``self._rng``
        (seeded by the ghost id at construction) so idle ghosts scatter across the graveyard instead
        of all piling onto the nearest stone — while staying deterministic per ghost. Emits a
        ``"home"`` walk (mode is NOT ``"workstation"``) so the arrival clock lands the ghost idle;
        it then rests (``face_rest``) and sinks. Any held workstation seat is released first."""
        self._release_workstation()
        grave = self._pick_random_grave()
        self.emit_walk("home", Point(x=float(grave[0]), y=float(grave[1])))

    # -- animation / facing ----------------------------------------------------

    def face_browser(self, facing: str | None = None) -> None:
        """Face the workstation while working. Threads an explicit 8-way ``facing``
        on the ``face`` command — computed toward the workstation tile the ghost is at (so a
        STATIONARY working ghost visibly orients to its computer, not stuck on its last
        movement delta). The arg is additive/optional: a legacy client ignores it safely."""
        if facing is None:
            ws = self._pick_workstation()
            facing = _facing_toward(self._position.x, self._position.y, float(ws[0]), float(ws[1]))
        self._emit("face", target="browser", facing=facing)
        self._walking = False
        self._at_ws = True

    def face_rest(self, facing: str | None = None) -> None:
        """Emit a RESTING facing on idle arrival. Additive/optional; leaves the ghost
        idle (no ``at_workstation``/``walking`` change). Defaults to the direction toward the
        nearest grave (a sensible settle) — which resolves to the south idle default when the
        ghost is already at its resting spot."""
        if facing is None:
            gx, gy = self._pick_grave()
            facing = _facing_toward(self._position.x, self._position.y, float(gx), float(gy))
        self._emit("face", target="rest", facing=facing)

    def play_work(self, kind: str | None = None) -> None:
        """Play the work animation. ``kind`` (navigating/searching/reading/scrolling/extracting)
        selects the per-kind ``work.<kind>`` clip so the renderer shows WHAT the ghost is doing.
        ``None`` keeps the generic ``work`` clip."""
        anim = f"work.{kind}" if kind else "work"
        self._emit("anim", anim=anim)

    def play_error(self) -> None:
        self._emit("anim", anim="error")

    def play_success(self) -> None:
        self._emit("anim", anim="success")

    # -- status / overlay ------------------------------------------------------

    def say(self, text: str, kind: str = _DEFAULT_BUBBLE) -> None:
        self._emit("say", text=text, bubble=kind)

    def set_overlay(self, kind: str) -> None:
        self._emit("overlay", overlay=kind)

    # -- read-only queries -----------------------------------------------------

    def is_idle(self) -> bool:
        return not self._walking and not self._at_ws

    def is_walking(self) -> bool:
        return self._walking

    def at_workstation(self) -> bool:
        return self._at_ws

    def position(self) -> Point:
        return self._position

    # -- destination resolution (data-driven, deterministic) -------------------

    def _select_workstation(self, target_section: str | None = None):  # type: ignore[no-untyped-def]
        """Pure seat selection (no mutation). Prefers a seat this ghost ALREADY holds (so a
        working ghost keeps its desk and ``face_browser`` orients to it), else the NEAREST free
        seat in the PREFERRED section, else any free seat, else any seat at all — a section with
        no/occupied free seats degrades to the shared pool (never a dead ghost). Returns the
        chosen ``Workstation`` (or None if the map has no workstations).

        ``target_section`` names an explicit FOREIGN section to seat in, overriding
        the ghost's own ``_section_id`` — the cross-section hop primitive. When ``None`` the ghost's
        own department is preferred (the original behavior)."""
        stations = sorted(self._map.workstations.values(), key=lambda w: w.id)
        if not stations:
            return None
        if self._reserved_ws_id is not None:
            held = self._map.workstations.get(self._reserved_ws_id)
            if held is not None:
                return held
        free = [w for w in stations if w.occupied_by is None]
        section = target_section if target_section is not None else self._section_id
        if section is not None:
            in_section = [w for w in free if w.section == section]
            if in_section:
                return min(
                    in_section,
                    key=lambda w: abs(w.x - self._position.x) + abs(w.y - self._position.y),
                )
        return (free or stations)[0]

    def _pick_workstation(self) -> tuple[int, int]:
        chosen = self._select_workstation()
        if chosen is None:
            return (round(self._position.x), round(self._position.y))
        return (chosen.x, chosen.y)

    def _reserve_workstation(self, target_section: str | None = None) -> tuple[int, int]:
        """Claim a seat at RUNTIME: mark ``occupied_by = ghost_id`` so same-section peers skip it
        (196 FIX 3). Idempotent — re-reserving keeps the same held seat; switching seats releases
        the prior one first. ``target_section`` seats the ghost in an explicit foreign
        section (the cross-section hop). Returns the seat coords."""
        chosen = self._select_workstation(target_section)
        if chosen is None:
            return (round(self._position.x), round(self._position.y))
        if self._reserved_ws_id is not None and self._reserved_ws_id != chosen.id:
            self._release_workstation()
        chosen.occupied_by = self._id
        self._reserved_ws_id = chosen.id
        return (chosen.x, chosen.y)

    def _release_workstation(self) -> None:
        """Vacate the held seat (clear ``occupied_by``) so it never leaks. Only clears a seat
        this ghost actually holds — defensive against a seat re-claimed by someone else."""
        if self._reserved_ws_id is None:
            return
        held = self._map.workstations.get(self._reserved_ws_id)
        if held is not None and held.occupied_by == self._id:
            held.occupied_by = None
        self._reserved_ws_id = None

    def release_workstation(self) -> None:
        """Public seat release for ghost teardown/despawn (196 FIX 3) — the pool calls this in its
        run ``finally`` so a seat is freed even if the ghost aborts mid-work (never a leaked seat)."""
        self._release_workstation()

    def _pick_grave(self) -> tuple[int, int]:
        """The NEAREST grave to the ghost's current position — a transient shared rest spot,
        NOT a designated per-ghost home. Graves have no occupancy field, so sharing
        is always allowed; several ghosts may rest at/near the same grave. Mirrors the
        free-selection style of :meth:`_pick_workstation` (nearest by Manhattan distance,
        ``sorted`` id as a deterministic tie-break) — never a hardcoded first grave."""
        graves = sorted(self._map.graves.values(), key=lambda g: g.id)
        if not graves:
            return (round(self._position.x), round(self._position.y))
        px, py = self._position.x, self._position.y
        nearest = min(graves, key=lambda g: abs(g.x - px) + abs(g.y - py))
        return (nearest.x, nearest.y)

    def _pick_random_grave(self) -> tuple[int, int]:
        """A RANDOM grave for the :meth:`wander` idle beat — drawn from the ghost's
        SEEDED ``_rng`` (seeded by ghost id) so idle ghosts scatter across the graveyard yet the
        choice is deterministic per ghost. Iterates a ``sorted``-by-id snapshot so the seeded draw
        is reproducible regardless of dict ordering."""
        graves = sorted(self._map.graves.values(), key=lambda g: g.id)
        if not graves:
            return (round(self._position.x), round(self._position.y))
        chosen = self._rng.choice(graves)
        return (chosen.x, chosen.y)


def create_ghost_handle(
    ghost_id: str,
    command_sink: CommandSink,
    map: WorldMap,  # noqa: A002 (matches the plan's public signature name)
    start: Point | None = None,
    section_id: str | None = None,
) -> _GhostHandle:
    """Build the concrete GhostHandle for ``ghost_id``.

    ``command_sink`` receives every emitted :class:`GhostCommand` (the server fans it out
    over the authed WS); ``map`` supplies the walkable grid + destinations the A* planner and
    the workstation/grave pickers read. ``section_id`` (optional) is the ghost's department —
    when set, ``walk_to_workstation`` prefers a free seat within that section.
    """
    return _GhostHandle(ghost_id, command_sink, map, start, section_id)
