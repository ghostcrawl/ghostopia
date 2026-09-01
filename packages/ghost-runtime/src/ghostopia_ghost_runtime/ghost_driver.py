"""The GhostDriver — events -> server-authoritative presentation.

The brain-stem that makes ghosts "feel alive". It consumes normalized event envelopes
(``type``/``ghost_id``/``payload`` — a :class:`ghostopia_shared.GhostEvent` or an
``Envelope``), applies the pure :func:`~ghostopia_ghost_runtime.state_machine.reduce` to keep
each ghost's authoritative COARSE lifecycle state in sync, and translates the resulting state
+ event into PRESENTATION through the :class:`GhostHandle`:

* WALKING / RETURNING_HOME  -> a walk command carrying an A* path (via :func:`find_path`)
* OPENING_BROWSER / work    -> face-browser + work animation
* ERROR / WAITING / RETRYING-> error animation (understated)
* COMPLETED                 -> success animation + a returning-home walk
* status changes            -> a contextual status bubble (``browser.navigate url=acme.com``
                               -> "Going to acme.com"; ``result.scraped n=42`` -> "Filed 42
                               records") via :data:`CONTEXTUAL_EXTRACTORS`

The driver is SOURCE-AGNOSTIC: simulated (stage 1-2, FakeBrowserProvider) and real GhostCrawl
(stage 3+) events produce identical presentation. It DECIDES no tasks — it dispatches the WORK
state to the mounted Behavior through a hook (the composition layer supplies the
instance); the Behavior EMITS, the driver REACTS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from ghostopia_shared import GhostCommand, GhostState, Point
from ghostopia_world import WorldMap, find_path

from ghostopia_ghost_runtime.ghost_handle import _GhostHandle, create_ghost_handle
from ghostopia_ghost_runtime.state_machine import reduce
from ghostopia_ghost_runtime.surface_vocab import (
    sanitize_code,
    sanitize_kind,
    sanitize_text,
)


class _EventLike(Protocol):
    """Structural shape the driver consumes (``GhostEvent`` / ``Envelope``)."""

    type: str
    ghost_id: str | None
    payload: Any


# A specific WORK state -> the per-kind `work.<kind>` clip the renderer plays so an observer
# can tell WHAT the ghost is doing at the workstation. The GhostDriver selects the
# clip from the real browser.action.kind / work phase; the handle emits `anim=work.<kind>`.
_WORK_STATE_KIND: dict[GhostState, str] = {
    GhostState.NAVIGATING: "navigating",
    GhostState.SEARCHING: "searching",
    GhostState.READING: "reading",
    GhostState.SCROLLING: "scrolling",
    GhostState.EXTRACTING: "extracting",
}


# The WORK phase the driver dispatches to the active Behavior (it never decides the work).
_WORK_STATES: frozenset[GhostState] = frozenset(
    {
        GhostState.OPENING_BROWSER,
        GhostState.NAVIGATING,
        GhostState.SEARCHING,
        GhostState.READING,
        GhostState.SCROLLING,
        GhostState.EXTRACTING,
        GhostState.PROCESSING,
    }
)


def _scraped_count(p: dict[str, Any]) -> int:
    n = p.get("n")
    if isinstance(n, int):
        return n
    fields = p.get("fields") or p.get("records")
    return len(fields) if isinstance(fields, list) else 0


# Event field -> a specific status bubble ("contextual status extraction"). The
# renderer never invents this text; the server extracts it from the real event payload.
#
# BOUNDARY SANITIZER: every bubble derived from a live event's kind/code/status/say
# is routed through the curated vocabulary — a KNOWN value maps to its on-brand phrase, an
# UNKNOWN/raw value maps to a generic safe phrase. A raw provider/SDK string (e.g. any raw
# internal engine status) is NEVER echoed onto the customer canvas. Only
# ``browser.navigate`` surfaces a literal payload value — the customer's OWN target url — which
# is not a provider/vendor string; it is still passed through ``sanitize_text`` as a guard.
CONTEXTUAL_EXTRACTORS: dict[str, Callable[[dict[str, Any]], str]] = {
    "task.assigned": lambda p: "Got a task",
    "ghost.walking": lambda p: "On my way",
    "browser.navigate": lambda p: sanitize_text(
        f"Going to {p.get('url', 'a page')}", fallback="Drifting to a page…"
    ),
    "browser.action": lambda p: sanitize_kind(p.get("kind")),
    "result.record_extracted": lambda p: "Filed a record",
    "result.scraped": lambda p: f"Filed {_scraped_count(p)} records",
    "browser.error": lambda p: sanitize_code(p.get("code")),
    "task.completed": lambda p: "All done!",
}

BehaviorDispatch = Callable[[str, _EventLike], None]


class GhostDriver:
    """Translates a ghost's event stream into visual commands + keeps its coarse state."""

    def __init__(
        self,
        world_map: WorldMap,
        command_sink: Callable[[GhostCommand], None],
    ) -> None:
        self._map = world_map
        self._sink = command_sink
        self._state: dict[str, GhostState] = {}
        self._handles: dict[str, _GhostHandle] = {}
        self._behavior_dispatch: BehaviorDispatch | None = None

    # -- public API ------------------------------------------------------------

    def state_of(self, ghost_id: str) -> GhostState:
        return self._state.get(ghost_id, GhostState.IDLE)

    def handle_for(self, ghost_id: str) -> _GhostHandle:
        h = self._handles.get(ghost_id)
        if h is None:
            h = create_ghost_handle(ghost_id, self._sink, self._map)
            self._handles[ghost_id] = h
        return h

    def set_behavior_dispatch(self, dispatch: BehaviorDispatch) -> None:
        """Mount the WORK-state hook. The driver forwards work-phase events to it and never
        decides the task itself (single direction of authority)."""
        self._behavior_dispatch = dispatch

    def dispatch(self, event: _EventLike) -> None:
        """Consume one normalized event: update coarse state + emit presentation."""
        ghost_id = event.ghost_id
        if ghost_id is None:
            return  # global/system event — no per-ghost presentation

        prev = self.state_of(ghost_id)
        new = reduce(prev, event)
        self._state[ghost_id] = new
        handle = self.handle_for(ghost_id)

        if new != prev:
            self._present_transition(handle, prev, new)

        self._present_contextual(handle, event)

        # Dispatch the WORK state to the active Behavior (composition layer supplies it).
        if new in _WORK_STATES and self._behavior_dispatch is not None:
            self._behavior_dispatch(ghost_id, event)

    # -- presentation ----------------------------------------------------------

    def _present_transition(
        self, handle: _GhostHandle, prev: GhostState, new: GhostState
    ) -> None:
        if new == GhostState.WALKING:
            self._walk_to(handle, "workstation", self._workstation_point(handle))
        elif new == GhostState.RETURNING_HOME:
            self._walk_to(handle, "home", self._grave_point(handle))
        elif new == GhostState.OPENING_BROWSER:
            handle.face_browser()
            handle.play_work()
        elif new in _WORK_STATES:
            handle.play_work(_WORK_STATE_KIND.get(new))
        elif new in (GhostState.WAITING, GhostState.RETRYING):
            handle.play_error()
        elif new == GhostState.ERROR:
            handle.play_error()
        elif new == GhostState.COMPLETED:
            handle.play_success()
            # Auto-return presentation: a completed ghost heads to the NEAREST FREE grave.
            self._walk_to(handle, "home", self._grave_point(handle))
        elif new == GhostState.IDLE and prev != GhostState.IDLE:
            # Settle facing a sensible resting direction on idle arrival.
            handle.face_rest()
            handle.say("Back home", "status")

    def _present_contextual(self, handle: _GhostHandle, event: _EventLike) -> None:
        extractor = CONTEXTUAL_EXTRACTORS.get(event.type)
        if extractor is None:
            return
        payload = event.payload if isinstance(event.payload, dict) else {}
        handle.say(extractor(payload), "status")

    # -- movement (driver plans the A* path itself; key-link to find_path) ------

    def _walk_to(self, handle: _GhostHandle, mode: str, dest: Point) -> None:
        path = self._plan_path(handle.position(), dest)
        handle.emit_walk(mode, dest, path)

    def _plan_path(self, start: Point, goal: Point) -> list[list[int]]:
        tiles = find_path(
            (round(start.x), round(start.y)),
            (round(goal.x), round(goal.y)),
            self._map.width,
            self._map.height,
            self._map.walkable_callback(),
        )
        return [[x, y] for (x, y) in tiles]

    # -- destination resolution ------------------------------------------------

    def _workstation_point(self, handle: _GhostHandle) -> Point:
        stations = sorted(self._map.workstations.values(), key=lambda w: w.id)
        free = [w for w in stations if w.occupied_by is None]
        w = (free or stations)[0]
        return Point(x=float(w.x), y=float(w.y))

    def _grave_point(self, handle: _GhostHandle) -> Point:
        """The NEAREST grave to the ghost's current position — a transient shared rest spot,
        NOT a hardcoded ``graves[0]`` designated home. Sharing is allowed (graves
        carry no occupancy field); mirrors :meth:`_workstation_point`'s free-selection style
        with a ``sorted`` id tie-break for determinism."""
        graves = sorted(self._map.graves.values(), key=lambda gr: gr.id)
        if not graves:
            return handle.position()
        pos = handle.position()
        nearest = min(graves, key=lambda gr: abs(gr.x - pos.x) + abs(gr.y - pos.y))
        return Point(x=float(nearest.x), y=float(nearest.y))
