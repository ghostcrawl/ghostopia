"""ghostopia shared domain types — the single Pydantic contract.

Every entity the world, runtime, orchestrator, behaviors, sections, and server share
lives here as a Pydantic model or Enum. This module ALSO owns the dynamic-behavior
CONTRACT TYPES — ``GhostHandle``, ``Behavior``/
``BehaviorContext``/``EndReason``, ``SectionDef``/``SectionRef``, ``WorldQuery``, and the
full-primitive value types ``Point``/``Button`` (+ the ``BrowserProvider`` Protocol name)
— so the behaviors package and sections package depend ONLY on
``ghostopia-shared`` and never cycle with ghost-runtime.

The concrete ``BrowserProvider`` implementation lives in the ghost-runtime; here it is anchored
as a Protocol so downstream shapes reference a stable name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------------------
# Value types (full-primitive capability anchors)
# --------------------------------------------------------------------------------------


class Point(BaseModel):
    """A pixel/tile coordinate."""

    x: float
    y: float


class Button(StrEnum):
    """Mouse button for the CDP-WS raw-input relay."""

    LEFT = "left"
    MIDDLE = "middle"
    RIGHT = "right"


class Bounds(BaseModel):
    """A tile rectangle on the map (section bounds / region)."""

    x: int
    y: int
    w: int
    h: int


# --------------------------------------------------------------------------------------
# Ghost state machine (EVENT_PROTOCOL §3 — all 16 states)
# --------------------------------------------------------------------------------------


class GhostState(StrEnum):
    """The authoritative coarse lifecycle FSM (server-owned; client interpolates)."""

    IDLE = "IDLE"
    RECEIVING_TASK = "RECEIVING_TASK"
    WALKING = "WALKING"
    AT_WORKSTATION = "AT_WORKSTATION"
    OPENING_BROWSER = "OPENING_BROWSER"
    NAVIGATING = "NAVIGATING"
    SEARCHING = "SEARCHING"
    READING = "READING"
    SCROLLING = "SCROLLING"
    EXTRACTING = "EXTRACTING"
    PROCESSING = "PROCESSING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"
    RETURNING_HOME = "RETURNING_HOME"


# --------------------------------------------------------------------------------------
# Event catalog (EVENT_PROTOCOL §2 — four namespaces)
# --------------------------------------------------------------------------------------


class EventType(StrEnum):
    """Every dotted event name in the catalog. ``type`` on the envelope is one of these
    (validated as a plain string on the wire so an unknown type is rejected, never
    ``eval``'d)."""

    # ghost.* — lifecycle & movement
    GHOST_SPAWNED = "ghost.spawned"
    GHOST_STATUS_CHANGED = "ghost.status_changed"
    GHOST_ASSIGNED = "ghost.assigned"
    GHOST_WANDER = "ghost.wander"
    GHOST_WALKING = "ghost.walking"
    GHOST_ARRIVED = "ghost.arrived"
    GHOST_RETURNING_HOME = "ghost.returning_home"
    GHOST_IDLE = "ghost.idle"
    # browser.* — real GhostCrawl browser session
    BROWSER_SESSION_OPENED = "browser.session_opened"
    BROWSER_NAVIGATE = "browser.navigate"
    BROWSER_ACTION = "browser.action"
    BROWSER_FRAME = "browser.frame"
    BROWSER_ERROR = "browser.error"
    BROWSER_SESSION_CLOSED = "browser.session_closed"
    # task.* — task/mission lifecycle
    TASK_SPAWNED = "task.spawned"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_RETRY = "task.retry"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    # result.* — extracted data & progress
    RESULT_RECORD_EXTRACTED = "result.record_extracted"
    RESULT_SCRAPED = "result.scraped"
    RESULT_VERIFIED = "result.verified"
    RESULT_MISSION_PROGRESS = "result.mission_progress"
    RESULT_MISSION_COMPLETED = "result.mission_completed"


class GhostEvent(BaseModel):
    """A normalized ghost/browser/task/result event (the payload envelopes carry)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    ghost_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GhostCommand(BaseModel):
    """A command issued to a ghost (management/driver → runtime)."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    ghost_id: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    """A normalized event from an AgentProvider (deterministic runner OR LLM)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class BrowserEvent(BaseModel):
    """A browser.* session event."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    """A mapped SDK error surfaced to the world (GHOSTCRAWL_INTEGRATION error map)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    retryable: bool = False
    retry_after: float | None = None
    detail: str | None = None


# --------------------------------------------------------------------------------------
# Liveness contract — REAL per-ghost operator-attention on the status envelope
# --------------------------------------------------------------------------------------


class GhostAttention(BaseModel):
    """Whether a ghost needs the OPERATOR. Set when a ghost hits a state only a
    human can clear — captcha_required / a non-retryable ``browser.error`` / ``task.failed`` /
    pool-exhausted / auth-needed — and cleared on resolution/removal. ``reason`` is a short
    server-sourced code the HUD may surface. Keyed by ghost so set→clear is explicit."""

    model_config = ConfigDict(extra="forbid")

    needs: bool = False
    reason: str | None = None


# --------------------------------------------------------------------------------------
# World / domain entities
# --------------------------------------------------------------------------------------


class WorldObject(BaseModel):
    """A placed object on the map (grave, terminal, landmark, decoration)."""

    id: str
    type: str
    x: int
    y: int
    direction: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Workstation(BaseModel):
    """A computer-graveyard workstation a ghost walks to and works at."""

    id: str
    x: int
    y: int
    section: str | None = None
    occupied_by: str | None = None


class PlacedProp(BaseModel):
    """A prop placed on the world map.

    A placed prop is FULLY described by its ``catalog_id`` (which prop from
    ``assets/props.catalog.json``), its ``tile`` (the top-left tile of its footprint),
    an ``orientation`` (facing), and an optional ``state`` (e.g. a lantern lit/dark).
    Nothing about the prop's art/footprint is stored here — that lives in the shared
    catalog, so this IS the data model the Graveyard Builder edits. The prop's
    footprint (looked up from the catalog) contributes to collision + is routed-around
    by A*; the renderer draws it purely from {catalog def, tile, orientation, state}."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(min_length=1)
    tile: tuple[int, int]
    orientation: str = "s"
    state: str | None = None
    # Optional per-instance colour tint (editor "recolor"), 0xRRGGBB. ``None`` = the
    # prop's native art. Kept optional so every prior placed-props map stays valid.
    tint: int | None = None


# --------------------------------------------------------------------------------------
# Editable map contract (Graveyard Builder) — the wire shape the in-app editor
# sends the server on ``map.save``. STRICT (``extra='forbid'``) + size-capped so a hostile
# / malformed map can never crash the server or be half-applied. The server
# converts a validated ``EditableMap`` into a ``WorldMap`` (footprint→collision + A*), checks
# reachability, then atomically swaps it live. NOTHING here trusts client geometry blindly.
# --------------------------------------------------------------------------------------

#: Hard caps on an editable map's dimensions/collections (defence against a giant/hostile map).
MAP_MAX_DIM = 256
MAP_MAX_PROPS = 4000
MAP_MAX_DESTS = 512
MAP_MAX_AREAS = 256
MAP_MAX_REGIONS = 256


class EditableArea(BaseModel):
    """A painted section PLOT rect the editor edits (areas layer). ``section`` is the
    logical section this plot maps to; ``id`` is the plot's stable name."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    section: str = Field(min_length=1, max_length=64)
    x: int
    y: int
    w: int = Field(ge=1)
    h: int = Field(ge=1)


class EditableGrave(BaseModel):
    """A ghost home-grave destination the editor places/moves."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str = "grave"
    x: int
    y: int
    region: str | None = None


class EditableWorkstation(BaseModel):
    """A workstation destination the editor places/moves."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str = "workstation"
    x: int
    y: int
    section: str | None = None
    occupied_by: str | None = None


class EditableDirCollision(BaseModel):
    """A one-way directional edge mask entry ({x, y, blocked: [sides]})."""

    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    blocked: list[str] = Field(default_factory=list)


class EditableMap(BaseModel):
    """The full map the Graveyard Builder edits + sends on ``map.save`` (strict wire
    shape). Server-side validation (schema here + semantic checks in ``map_editor``) is the
    trust boundary: a malformed/hostile map is REJECTED with a reason, never half-applied.

    ``to_load_dict`` renders the exact dict :func:`ghostopia_world.load_map` consumes, so the
    server reuses the SAME loader/collision/A* path the shipped world uses — no editor-only map
    code path."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    width: int = Field(ge=1, le=MAP_MAX_DIM)
    height: int = Field(ge=1, le=MAP_MAX_DIM)
    tile_size: int = Field(default=16, ge=1, le=256)
    walkable: list[list[int]]
    regions: dict[str, Bounds] = Field(default_factory=dict, max_length=MAP_MAX_REGIONS)
    areas: list[EditableArea] = Field(default_factory=list, max_length=MAP_MAX_AREAS)
    placed_props: list[PlacedProp] = Field(default_factory=list, max_length=MAP_MAX_PROPS)
    graves: list[EditableGrave] = Field(default_factory=list, max_length=MAP_MAX_DESTS)
    workstations: list[EditableWorkstation] = Field(
        default_factory=list, max_length=MAP_MAX_DESTS
    )
    directional_collision: list[EditableDirCollision] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_grid(self) -> EditableMap:
        """The walkable grid MUST be exactly ``height`` rows of ``width`` 0/1 cells (a client
        cannot smuggle a ragged / oversized / non-binary grid past ``extra='forbid'``)."""
        if len(self.walkable) != self.height:
            raise ValueError(
                f"walkable grid has {len(self.walkable)} rows, expected height={self.height}"
            )
        for r, row in enumerate(self.walkable):
            if len(row) != self.width:
                raise ValueError(
                    f"walkable row {r} has {len(row)} cells, expected width={self.width}"
                )
            for c, cell in enumerate(row):
                if cell not in (0, 1):
                    raise ValueError(f"walkable[{r}][{c}]={cell!r} is not 0 or 1")
        return self

    def to_load_dict(self) -> dict[str, Any]:
        """Render the dict shape :func:`ghostopia_world.load_map` consumes (destinations
        nested, areas/placed_props/directional_collision passthrough)."""
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "tile_size": self.tile_size,
            "walkable": [list(row) for row in self.walkable],
            "regions": {k: b.model_dump() for k, b in self.regions.items()},
            "areas": [a.model_dump() for a in self.areas],
            "placed_props": [p.model_dump() for p in self.placed_props],
            "destinations": {
                "graves": [g.model_dump() for g in self.graves],
                "workstations": [w.model_dump() for w in self.workstations],
            },
            "directional_collision": [d.model_dump() for d in self.directional_collision],
        }


class Ghost(BaseModel):
    """A visual worker entity. Differentiated by name/section/task/location/UI —
    NOT by RPG class."""

    id: str
    name: str
    home_grave: str
    section: str | None = None
    state: GhostState = GhostState.IDLE
    task_id: str | None = None
    behavior_override: str | None = None
    position: Point | None = None


class Task(BaseModel):
    """A unit of work assigned to a ghost (mission fan-out). ``kind`` routes it to a
    section; ``behavior_hint`` can request a specific behavior (role-resolution §B)."""

    id: str
    kind: str
    mission_id: str | None = None
    behavior_hint: str | None = None
    target: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)


class Mission(BaseModel):
    """A named collection of tasks (the '500 companies' fan-out)."""

    id: str
    title: str
    task_ids: list[str] = Field(default_factory=list)
    done: int = 0
    total: int = 0


class Agent(BaseModel):
    """An AgentProvider instance (deterministic runner or LLM brain), behind the seam."""

    id: str
    kind: Literal["deterministic", "llm"] = "deterministic"
    provider: str | None = None


class BrowserSession(BaseModel):
    """A real GhostCrawl browser session bound to a ghost/task."""

    session_id: str
    ghost_id: str | None = None
    target: str | None = None
    engine: str | None = None
    url: str | None = None


class Result(BaseModel):
    """One extracted result / record landing in the Data Graveyard."""

    task_id: str
    url: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    record: dict[str, Any] | None = None


# --------------------------------------------------------------------------------------
# Section model (ghostopia-original)
# --------------------------------------------------------------------------------------


class SectionDef(BaseModel):
    """A labelled sub-region of the graveyard: bounds + a workstation pool + a role
    (default behavior) + task-routing rules. Declared as DATA alongside the map."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    bounds: Bounds
    role: str
    workstation_types: list[str] = Field(default_factory=list)
    capacity: int = Field(default=1, ge=0)
    accepts: list[str] = Field(default_factory=list)
    routes_to: list[str] = Field(default_factory=list)
    palette: str | None = None
    #: An explicit section-kind tag. ``"department"`` marks a real result repository —
    #: the server-authoritative signal the client trusts to gate map result-clicks (only a
    #: department plot opens a findings card). ``None`` for non-department ground (resting).
    #: Never inferred client-side from ``target_url`` presence — the server tag is
    #: the single source of truth.
    kind: str | None = None
    # -- what-to-scrape identity -------------------------------------------------
    #: The department's default scrape target: a concrete URL to scrape (e.g. a
    #: books.toscrape category) when a dispatched task carries no url of its own.
    target_url: str | None = None
    #: The department's default search seed (a product-search term) — the search-driven
    #: alternative to ``target_url`` for departments whose finds start from a query.
    query: str | None = None
    #: An optional free-text category/theme label for the department's target.
    category: str | None = None
    #: The department's default extraction schema (field-name -> type hint), applied
    #: when a dispatched task carries no ``extract_schema`` of its own.
    extract_schema: dict[str, Any] | None = None


class SectionRef(BaseModel):
    """A lightweight reference to a section a ghost belongs to (role + optional bounds/
    roster), passed into ``BehaviorContext`` without the full ``SectionDef``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    role: str
    bounds: Bounds | None = None
    roster: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Dynamic-behavior contract Protocols
# --------------------------------------------------------------------------------------

EndReason = Literal["completed", "failed", "cancelled", "retargeted"]


@runtime_checkable
class GhostHandle(Protocol):
    """The narrow command surface a Behavior uses to drive the visible ghost. A Behavior
    touches the ghost ONLY through this (the NpcHandle analog)."""

    def walk_to(self, dest: Point) -> None: ...
    def walk_to_workstation(self) -> None: ...
    def walk_home(self) -> None: ...
    def walk_to_section_workstation(self, section_id: str) -> None: ...
    def walk_to_section_drop(self, section_id: str | None = ...) -> None: ...
    def wander(self) -> None: ...
    def face_browser(self) -> None: ...
    def play_work(self) -> None: ...
    def play_error(self) -> None: ...
    def play_success(self) -> None: ...
    def say(self, text: str, kind: str = ...) -> None: ...
    def set_overlay(self, kind: str) -> None: ...
    # read-only
    def is_idle(self) -> bool: ...
    def is_walking(self) -> bool: ...
    def at_workstation(self) -> bool: ...
    def position(self) -> Point: ...


@runtime_checkable
class WorldQuery(Protocol):
    """Read-only map query surface a Behavior consumes (free workstations, section
    bounds, random reachable/workstation tiles)."""

    def free_workstations(self, section: str | None = ...) -> list[Point]: ...
    def section_bounds(self, section: str) -> Bounds: ...
    def random_reachable(self, bounds: Bounds) -> Point: ...
    def random_reachable_global(
        self, from_point: Point | None = ..., max_radius: int | None = ...
    ) -> Point: ...
    def random_workstation(self, section: str) -> Point: ...


@runtime_checkable
class BrowserProvider(Protocol):
    """The full-primitive GhostCrawl capability a Behavior receives.

    Anchored here as a Protocol name; the concrete, capability-scoped implementation
    (session lifecycle + mouse/keyboard/page primitives + CDP-WS relay + live frames)
    lives in the ghost-runtime. A Behavior NEVER imports the SDK — it holds only this handle."""

    async def open(self, opts: dict[str, Any]) -> Any: ...
    async def release(self) -> None: ...
    async def scrape(self, opts: dict[str, Any]) -> Any: ...
    async def search(self, opts: dict[str, Any]) -> Any: ...
    async def extract(self, schema: dict[str, Any]) -> dict[str, Any]: ...
    async def screenshot(self) -> bytes: ...


@runtime_checkable
class Behavior(Protocol):
    """The decision unit. Original, modular, hot-registrable code
    with a documented lifecycle. ``on_tick`` MUST be non-blocking; long GhostCrawl calls
    are kicked off and their completion arrives on ``on_event``."""

    name: str

    def on_start(self, ctx: BehaviorContext) -> Any: ...
    def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None: ...
    def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None: ...
    def on_end(self, ctx: BehaviorContext, reason: EndReason) -> Any: ...


class BehaviorContext(BaseModel):
    """Everything a Behavior receives — the capability-scoped seam. A
    behavior gets ONLY these; never ``fs``/``net``/``child_process``/keys/raw SDK.

    Uses arbitrary types (Protocols + callables); it is a runtime carrier, not a wire
    model, so it is intentionally NOT ``extra='forbid'`` JSON-validated."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ghost: GhostHandle
    browser: BrowserProvider
    world: WorldQuery
    emit: Any  # Callable[[GhostEvent], None]
    task: Task | None = None
    section: SectionRef | None = None
    rng: Any = None  # Callable[[], float] — seeded RNG for deterministic tests
    log: Any = None  # Callable[[str], None]


# Resolve the forward references (``BehaviorContext`` used inside the Behavior Protocol).
BehaviorContext.model_rebuild()
