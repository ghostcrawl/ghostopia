"""The Section model + runtime (ghostopia-original).

A **Section** is a labelled sub-region of the graveyard: ``bounds`` (a tile rect on the
:class:`~ghostopia_world.WorldMap`), a pool of workstation types, a live roster of
ghosts, and a **role** (a default behavior + a task-routing rule) — declared as DATA in
``maps/<map>.sections.json`` alongside the map file. Sections are the per-group dynamism
+ assignment unit missions fan out to (BY ROLE, ``accepts``/``routes_to``), never a
hardcoded per-ghost or per-site route.

This module owns:

* :class:`SectionDef` — the DATA model. It is the shared ``SectionDef`` (single contract
  source) PLUS an optional ``gc_target``: the section's default
  dual-target when a :class:`~ghostopia_shared.TaskSpec` omits its own ``gc_target``.
* :class:`Section` — the runtime wrapping a ``SectionDef`` + a live roster
  (``add_ghost``/``remove_ghost``) + a per-section task sub-queue + capacity accounting
  (``at_capacity``/``assign``/``release``).
* :func:`load_sections` / :func:`load_default_sections` — typed DATA loaders.

A ``canvas`` role hosts the whiteboard-draw recipe. Adding or re-roling a
section is a data change that touches neither the renderer nor the core loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from ghostopia_shared.types import Bounds, Task
from ghostopia_shared.types import SectionDef as _SharedSectionDef
from ghostopia_world.map import WorldMap

__all__ = [
    "DEFAULT_SECTIONS_PATH",
    "GcTarget",
    "Section",
    "SectionDef",
    "load_default_sections",
    "load_sections",
]

GcTarget = Literal["cloud", "selfhost"]

#: The shipped default sections DATA, next to ``maps/graveyard.json`` (mirrors the
#: world map's ``DEFAULT_MAP_PATH`` resolution — four ``parents`` up from this module
#: to the ghostopia workspace root, then ``maps/``).
DEFAULT_SECTIONS_PATH: Path = (
    Path(__file__).resolve().parents[4] / "maps" / "graveyard.sections.json"
)


class SectionDef(_SharedSectionDef):
    """The DATA model for a section: the shared ``SectionDef`` (id/label/bounds/role/
    workstation_types/capacity/accepts/routes_to/palette) PLUS an optional ``gc_target``.

    Subclassing the shared model keeps ONE contract source; the only ghostopia-sections
    additions are ``gc_target`` — the section's default dual-target applied by the
    fan-out when a ``TaskSpec`` omits its own ``target.gc_target`` — and ``advanced``: an
    opt-in "advanced" department that runs against REAL retail (spends the
    user's key), kept OFF by default so the safe keyless mode never touches a real store
    until the operator explicitly toggles it on.
    """

    gc_target: GcTarget | None = None
    #: An opt-in "advanced" real-retail department. ``True`` marks a department
    #: whose ghosts search a REAL product across REAL store pages (spending the user's key +
    #: exposing real-site variability), so the flagship workforce NEVER auto-features it — it
    #: runs ONLY when the operator explicitly enables it. ``False`` (the default) is the safe
    #: keyless mode behavior.
    advanced: bool = False


class Section:
    """A Section runtime: a :class:`SectionDef` + a live roster + a per-section task
    sub-queue + capacity accounting.

    ``capacity`` is the max number of concurrently *working* ghosts. Assigning a task
    picks a free roster ghost when a slot is open; otherwise the task is enqueued in the
    section sub-queue. Releasing a ghost frees its slot and drains the next queued task.
    """

    def __init__(self, defn: SectionDef) -> None:
        self.defn = defn
        #: ghost_ids that belong to this section (its roster).
        self.roster: list[str] = []
        #: ghost_id -> the Task it is currently working (the working set).
        self.working: dict[str, Task] = {}
        #: tasks accepted by the section but not yet assigned (capacity overflow).
        self.queue: list[Task] = []

    # -- SectionDef pass-throughs (ergonomics for the fan-out + resolution) ---------

    @property
    def id(self) -> str:
        return self.defn.id

    @property
    def role(self) -> str:
        return self.defn.role

    @property
    def bounds(self) -> Bounds:
        return self.defn.bounds

    @property
    def accepts(self) -> list[str]:
        return self.defn.accepts

    @property
    def routes_to(self) -> list[str]:
        return self.defn.routes_to

    @property
    def capacity(self) -> int:
        return self.defn.capacity

    @property
    def gc_target(self) -> GcTarget | None:
        """The section's default dual-target, or ``None`` when it carries none."""
        return self.defn.gc_target

    @property
    def kind(self) -> str | None:
        """The explicit section-kind tag: ``"department"`` for a real result repository,
        else ``None``. The server-authoritative signal the client trusts to gate map clicks."""
        return self.defn.kind

    # -- what-to-scrape identity pass-throughs -------------------------------
    # A department's default scrape target/search/schema, read by the fan-out dispatch
    # when a Task carries none of its own (orchestrator url + extract_schema fallback).

    @property
    def target_url(self) -> str | None:
        """The department's default scrape URL, or ``None`` when it carries none."""
        return self.defn.target_url

    @property
    def query(self) -> str | None:
        """The department's default search seed, or ``None`` when it carries none."""
        return self.defn.query

    @property
    def category(self) -> str | None:
        """The department's optional category/theme label, or ``None``."""
        return self.defn.category

    @property
    def extract_schema(self) -> dict[str, Any] | None:
        """The department's default extraction schema, or ``None`` when it carries none."""
        return self.defn.extract_schema

    # -- roster --------------------------------------------------------------------

    def add_ghost(self, ghost_id: str) -> None:
        """Add ``ghost_id`` to the roster (idempotent)."""
        if ghost_id not in self.roster:
            self.roster.append(ghost_id)

    def remove_ghost(self, ghost_id: str) -> None:
        """Remove ``ghost_id`` from the roster and its working slot (if any)."""
        if ghost_id in self.roster:
            self.roster.remove(ghost_id)
        self.working.pop(ghost_id, None)

    # -- capacity / assignment -----------------------------------------------------

    def at_capacity(self) -> bool:
        """True when the working set has reached ``capacity``."""
        return len(self.working) >= self.defn.capacity

    def _free_ghost(self, exclude: str | None = None) -> str | None:
        """A roster ghost not currently working (optionally excluding one), or ``None``."""
        for gid in self.roster:
            if gid != exclude and gid not in self.working:
                return gid
        return None

    def accepts_kind(self, kind: str) -> bool:
        """True when this section consumes task ``kind``."""
        return kind in self.defn.accepts

    def assign(self, task: Task, exclude: str | None = None) -> str | None:
        """Assign ``task`` to a free roster ghost, else enqueue it.

        ``exclude`` skips a ghost when picking (e.g. the just-released ghost on a drain,
        which is done and walking home). Returns the ghost_id it was assigned to, or
        ``None`` when the task was queued (at capacity or no free roster ghost).
        """
        if not self.at_capacity():
            gid = self._free_ghost(exclude=exclude)
            if gid is not None:
                self.working[gid] = task
                return gid
        self.queue.append(task)
        return None

    def release(self, ghost_id: str) -> Task | None:
        """Free ``ghost_id``'s working slot and drain the next queued task, if any.

        The just-released ghost is done (it walks home), so the drained task is handed
        to a *different* waiting roster ghost. Returns the task pulled off the sub-queue
        (now assigned), or ``None`` when the queue was empty or no other ghost is free.
        """
        self.working.pop(ghost_id, None)
        if self.queue and self._free_ghost(exclude=ghost_id) is not None:
            nxt = self.queue.pop(0)
            self.assign(nxt, exclude=ghost_id)
            return nxt
        return None

    # -- map overlay validation ----------------------------------------------------

    def validate_on_map(self, world_map: WorldMap) -> None:
        """Assert the section ``bounds`` are a legal tile rect inside ``world_map``.

        The section overlays its ``bounds`` on the :class:`WorldMap`; degenerate or
        out-of-map bounds are a data error (fail fast, data-driven wiring).
        """
        b = self.defn.bounds
        if b.w <= 0 or b.h <= 0:
            raise ValueError(f"section {self.defn.id!r} has degenerate bounds {b!r}")
        if (
            b.x < 0
            or b.y < 0
            or b.x + b.w > world_map.width
            or b.y + b.h > world_map.height
        ):
            raise ValueError(
                f"section {self.defn.id!r} bounds {b!r} fall outside the "
                f"{world_map.width}x{world_map.height} map"
            )


def _coerce_section_list(data: Any) -> list[dict[str, Any]]:
    """Accept either a bare ``[...]`` list or a ``{'sections': [...]}`` wrapper."""
    if isinstance(data, dict):
        raw = data.get("sections")
    else:
        raw = data
    if not isinstance(raw, list):
        raise ValueError("sections data must be a list (or a {'sections': [...]} object)")
    return raw


def load_sections(data: Any) -> list[Section]:
    """Build typed :class:`Section` runtimes from decoded sections DATA.

    ``data`` is either a JSON list of section objects or a ``{'sections': [...]}``
    wrapper. Each object is validated into a :class:`SectionDef` (incl. the optional
    ``gc_target``); an unknown key is rejected (``extra='forbid'`` inherited from the
    shared model). Duplicate section ids are a data error.
    """
    sections: list[Section] = []
    seen: set[str] = set()
    for obj in _coerce_section_list(data):
        defn = SectionDef.model_validate(obj)
        if defn.id in seen:
            raise ValueError(f"duplicate section id {defn.id!r}")
        seen.add(defn.id)
        sections.append(Section(defn))
    return sections


def load_sections_file(path: str | Path) -> list[Section]:
    """Load + parse a sections JSON file into :class:`Section` runtimes."""
    with open(path) as f:
        return load_sections(json.load(f))


def load_default_sections() -> list[Section]:
    """Load the shipped default sections (``maps/graveyard.sections.json``)."""
    return load_sections_file(DEFAULT_SECTIONS_PATH)
