"""The flagship "spooky workforce" workforce — departments-only.

Entering Live mode (or pressing the prominent "Run workforce" button) animates the four
seeded DEPARTMENTS (Horror/Mystery Books + Spooky Masks/Costumes): for each department it
spawns :data:`GHOSTS_PER_DEPARTMENT` ghosts, each running the department's OWN role against
its OWN real target and surfacing the priced/detail list it brings back through a REAL
GhostCrawl session. The example.com "stage" (varied actions on one safe URL) and the utility
stage sections (research/extraction/verify/error/canvas) are GONE forward-only: the
map is departments-only, so everything visible is a real result repository.

Everything routes through the SAME authoritative pool/section path a mission uses
(``GhostPool.spawn`` → section roster) — no bespoke render path, no client SDK, NAMES +
URLs only over the wire (thin-frontend).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ghostopia_orchestration import WorkQueue
from ghostopia_sections import Section
from ghostopia_shared import Task

from .orchestrator import make_pool_stage_dispatch

__all__ = [
    "FLAGSHIP_DEPARTMENT_IDS",
    "GHOSTS_PER_DEPARTMENT",
    "WORKFORCE_MISSION_ID",
    "WORKFORCE_SAFE_DEFAULT",
    "STAGE_SECTION_IDS",
    "WorkforceRelay",
    "WorkforceSpec",
    "build_department_workforce",
    "run_department_workforce",
    "workforce_desired_cap",
    "workforce_pool_cap",
    "workforce_visible_cap",
]

#: The three baton STAGE sections the background relay walks ghosts through (PIPELINE-DESIGN):
#: research (scout_urls) → extraction (navigate_and_extract) → verify (verify → deliver). These
#: are the intermediate sections the OLD workforce left empty (it teleported ghosts straight into
#: the terminal department); the relay finally populates them.
STAGE_SECTION_IDS: tuple[str, ...] = ("research", "extraction", "verify")


def _env_int(name: str) -> int | None:
    """Parse a positive int env override; return None when unset/blank/malformed/non-positive."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None

#: The synthetic mission id every workforce/department task rolls up under (196). Workforce
#: ghosts spawn via ``GhostPool.spawn`` (not ``mission.submit``), so without a mission id their
#: real ``result.scraped`` finds never persisted or reached the Data Graveyard. Tagging every
#: workforce/department task with ONE stable mission id routes them through the SAME
#: results-recorder → ``result.mission_progress`` pipeline a mission uses; the Data Graveyard's
#: "by department" view groups by the ghost's SECTION (from its task row), so a single mission
#: bucket is correct — the department, not the mission, is the result repository.
WORKFORCE_MISSION_ID = "workforce"

#: The flagship "spooky workforce": four themed departments seeded as world zones in
#: ``maps/graveyard.sections.json`` (Plan 01 what-to-scrape identity). Two book departments
#: (``navigate_and_extract`` against a books.toscrape category) and two search-driven product
#: departments (``search_and_detail`` on a keyless product query). This tuple names WHICH
#: seeded departments the workforce animates — the actual target/query + schema live in the
#: sections DATA (no per-site logic here; the ghost reads its own department's identity).
FLAGSHIP_DEPARTMENT_IDS: tuple[str, ...] = (
    "best-price",
    "horror-books",
    "mystery-books",
    "spooky-masks",
    "spooky-costumes",
)

#: Ghosts-per-department in the flagship workforce (3 × 4 departments = 12). Env-overridable
#: via ``GHOSTOPIA_GHOSTS_PER_SECTION`` (positive int) so a small-plan operator can shrink the
#: fleet — read once at import so it is fixed for the process boot (set it before start).
GHOSTS_PER_DEPARTMENT = _env_int("GHOSTOPIA_GHOSTS_PER_SECTION") or 3

#: The governor-safe fallback concurrency the workforce uses when the account's real
#: concurrent-live-session limit is UNKNOWN (no ``me()`` entitlement, an offline boot, a
#: token-less deploy). Kept small so a keyless/mis-derived boot can never open more live
#: sessions than a modest plan allows — the fix for the 429 "concurrent live-session limit"
#: storm that left the Data Graveyard empty (every over-concurrent ``sessions.create`` was
#: rate-limited). The pool semaphore queues any overflow; ghosts wait, they never over-open.
WORKFORCE_SAFE_DEFAULT = 3

#: A palette so the department ghosts are visually distinct on the map.
_PALETTE = (
    0x7AD7FF, 0xFFB347, 0x8BE04A, 0xFF5AA8, 0x7FD7C4, 0x9B7BFF,
    0xF6C744, 0x5AC8FA, 0xE879F9, 0x34D399, 0xFB7185, 0xA78BFA,
    0xFACC15, 0x38BDF8, 0xF472B6, 0x4ADE80, 0xFF8A5B, 0xC084FC,
)


@dataclass(frozen=True)
class WorkforceSpec:
    """One department ghost: which REAL behavior it runs, its section, its real-target task."""

    ghost_id: str
    name: str
    section: Section
    behavior_name: str
    action: str
    color: int
    task: Task


def workforce_desired_cap() -> int:
    """How many department ghosts the flagship workforce WANTS running at once.

    The whole visible workforce is the four departments × :data:`GHOSTS_PER_DEPARTMENT`; a
    department ghost owns exactly one live session, so this is the concurrency the workforce
    would use if the plan allowed it. It is a CEILING, not the cap actually applied — the real
    cap is clamped to the account limit by :func:`workforce_pool_cap` so the pool never opens
    more live sessions than the plan permits."""
    return GHOSTS_PER_DEPARTMENT * len(FLAGSHIP_DEPARTMENT_IDS)


def workforce_pool_cap(account_limit: int | None = None) -> int:
    """The concurrency cap the workforce pool applies — NEVER above the account's plan limit.

    Root-cause fix for the empty Data Graveyard: the workforce opens one live GhostCrawl
    session per department ghost, so running more ghosts than the account's concurrent
    live-session limit made every over-concurrent ``sessions.create`` return a retryable
    ``rate_limited`` 429 ("You've reached your plan's concurrent live-session limit") — no
    session ever opened, so no ``result.scraped`` was ever recorded. The cap is therefore:

    * ``GHOSTOPIA_WORKFORCE_MAX_CONCURRENT`` (positive int) FORCES the cap when set — the
      operator override, still clamped to ``account_limit`` when that is known so the override
      can only ever LOWER, never breach, the plan;
    * otherwise ``min(workforce_desired, account_limit)`` when the account's real limit is known
      (the SDK ``me().max_concurrency`` entitlement — the single source of truth);
    * otherwise the governor-safe :data:`WORKFORCE_SAFE_DEFAULT` (3) when the limit is unknown,
      so a keyless/offline boot can never over-open.

    Always at least 1. The pool semaphore queues any ghost beyond the cap (it waits for a free
    slot rather than over-opening), so the whole flagship workforce still materializes on the
    map — it just paces its live sessions to what the plan allows."""
    desired = workforce_desired_cap()
    limit = account_limit if isinstance(account_limit, int) and account_limit > 0 else None
    forced = _env_int("GHOSTOPIA_WORKFORCE_MAX_CONCURRENT")
    if forced is not None:
        cap = min(forced, limit) if limit is not None else forced
    elif limit is not None:
        cap = min(desired, limit)
    else:
        cap = min(desired, WORKFORCE_SAFE_DEFAULT)
    return max(1, cap)


def workforce_visible_cap() -> int:
    """How many ghosts may WALK/work concurrently — DECOUPLED from the live-session cap.

    The background baton relay runs many more VISIBLE stage ghosts (departments × the three
    research/extraction/verify stages, plus transient queue-dispatched hops) than the small
    concurrent-live-session budget allows, because the stateless stages never open a session.
    This sizes the pool's visible-workforce semaphore generously so N ≫ 2 ghosts walk at once
    while the live-session semaphore independently caps open sessions at the plan limit."""
    depts = len(FLAGSHIP_DEPARTMENT_IDS)
    # featured solo pipeline_crawl ghosts + per-department presence at each stage + queue hops.
    return workforce_desired_cap() + depts * (len(STAGE_SECTION_IDS) + 2)


# ------------------------------------------------------------------------------------------
# Flagship "spooky workforce" — the four themed DEPARTMENTS.
# ------------------------------------------------------------------------------------------
#
# Each flagship department is a seeded world zone (Plan 01) whose ghosts run its OWN role
# against its OWN real target and surface the priced/detail list they bring back. A department
# drives a REAL scrape:
#   * a books department (role ``navigate_and_extract``) points at a books.toscrape category
#     and pulls {title, price, rating, availability};
#   * a product department (role ``search_and_detail``) runs a keyless product search and
#     pulls {title, price} per found result.
# Everything routes through the SAME ``GhostPool.spawn`` → section path a mission uses — no
# bespoke render path. The target/query + schema are read from the section's own DATA (the
# Plan 01 what-to-scrape identity), so there is NO per-site logic here.


def _pipeline_task(section: Section, ghost_id: str) -> Task:
    """Build the FEATURED solo ``pipeline_crawl`` task for one department.

    A featured ghost walks the ENTIRE best-price flow SOLO (research → extraction → verify →
    deliver into this department → wander), session-backed so the operator can watch a REAL
    browser in the inspector. It reads its identity from ``task.params``: the search ``query``
    (the section's own query, else its label so a book department still searches), the origin
    ``repository_section`` every find is tagged to, and the department's ``extract_schema``.
    ``stateless=False`` so it opens a live session (bounded by the live-session semaphore)."""
    query = section.query or section.defn.label
    target_url = section.target_url or ""
    params: dict[str, Any] = {
        "ghost_id": ghost_id,
        "query": query,
        # P2: hand the department's declared listing/category page to the pipeline so its
        # research stage DISCOVERS real product candidates from it (listing → product pages)
        # instead of web-searching a junk label string that returns nothing — the deterministic
        # data source for a department that knows its own target. A department with no
        # target_url (a pure product-search department) falls back to the keyless search.
        "target_url": target_url,
        "repository_section": section.id,
        "stateless": False,
        # Advanced real-retail departments (masks/costumes) extract from the CAPTCHA-solved page
        # the ghost renders in-session; the safe keyless book departments re-fetch server-side.
        "session_extract": bool(getattr(section.defn, "advanced", False)),
        "max_results": 5,
        "dwell_ms": 1800.0,
    }
    schema = section.extract_schema
    if schema is not None:
        params["extract_schema"] = schema
    target = {"url": target_url} if target_url else {"query": query, "action": "search"}
    return Task(
        id=f"dept-{ghost_id}",
        kind="extract",
        mission_id=WORKFORCE_MISSION_ID,
        target=target,
        params=params,
    )


def build_department_workforce(
    sections: list[Section],
    *,
    advanced_enabled: frozenset[str] = frozenset(),
) -> list[WorkforceSpec]:
    """Build the FEATURED solo pipeline ghosts — the watchable ≤ live-budget few.

    The workforce no longer spawns direct-to-department ghosts (the OLD behavior that teleported
    a ghost into its terminal department and left research/extraction/verify empty). Instead:

    * FEATURED (this function) — a BOUNDED few (``GHOSTOPIA_LIVE_BROWSER_GHOSTS``, default 2)
      session-backed ghosts, one per department, each running ``pipeline_crawl`` SOLO through the
      whole flow so the operator watches a real browser walk the stages. Kept ≤ the account's
      concurrent-live-session cap so the live budget is never churned. The canonical
      ``best-price`` department leads the flagship order so the best-price example runs on boot.
    * BACKGROUND — the stateless baton (:class:`WorkforceRelay`) walks stage ghosts through the
      research/extraction/verify sections via the WorkQueue; it is NOT part of this template.

    ``advanced_enabled`` is the set of opt-in ADVANCED real-retail department ids
    the operator has explicitly toggled on. An advanced department (``defn.advanced``) is
    featured FIRST when enabled (so the operator immediately watches it run against real retail)
    and is otherwise SKIPPED entirely — the safe keyless mode never touches a real store until
    opt-in. A non-advanced department is always eligible.

    Missing departments are skipped. Deterministic ids/colors — no RNG."""
    by_id = {s.id: s for s in sections}
    specs: list[WorkforceSpec] = []
    # Feature every non-advanced (safe keyless) department by default so each gets a DEDICATED
    # pipeline ghost that reliably runs its full crawl every cycle — rather than a few featured
    # ones plus the rest starving in the shared background pool. Bounded by the account's
    # concurrent-live-session cap (the pool semaphore queues any overflow) and env-overridable via
    # ``GHOSTOPIA_LIVE_BROWSER_GHOSTS``. The warm-session pool self-regulates (it only warms into a
    # genuinely free slot), so featuring N ≙ the cap never over-opens.
    _safe_default_depts = sum(
        1 for s in sections if s.id in FLAGSHIP_DEPARTMENT_IDS and not s.defn.advanced
    )
    live_budget = _env_int("GHOSTOPIA_LIVE_BROWSER_GHOSTS")
    live_budget = max(1, _safe_default_depts) if live_budget is None else live_budget
    # An ENABLED advanced department LEADS (the operator wants to watch the real-retail scrape),
    # whether it lives in the flagship tuple (masks/costumes) or outside it (haunted-market) — so
    # it always gets a featured, session-backed ghost when toggled on, and is never crowded out of
    # the live budget by the default book departments. The rest follow in the safe flagship order.
    enabled_advanced = [
        s.id for s in sections if s.defn.advanced and s.id in advanced_enabled
    ]
    ordered_ids = [
        *enabled_advanced,
        *[d for d in FLAGSHIP_DEPARTMENT_IDS if d not in enabled_advanced],
    ]
    featured = 0
    for di, dept_id in enumerate(ordered_ids):
        section = by_id.get(dept_id)
        if section is None:
            continue
        # an advanced department is OFF unless explicitly enabled (spends the user's key).
        if section.defn.advanced and section.id not in advanced_enabled:
            continue
        if featured >= live_budget:
            break
        ghost_id = f"dept-{section.id}-0"
        name = f"{section.defn.label} (featured)"
        color = _PALETTE[di % len(_PALETTE)]
        featured += 1
        specs.append(
            WorkforceSpec(
                ghost_id=ghost_id,
                name=name,
                section=section,
                behavior_name="pipeline_crawl",
                action="pipeline_crawl",
                color=color,
                task=_pipeline_task(section, ghost_id),
            )
        )
    return specs


def _stage_presence_task(
    stage: Section, dept: Section, ghost_id: str, mission_id: str
) -> Task:
    """Build the looping presence task for a background baton STAGE ghost.

    A stage-presence ghost sits at ITS stage desk (research / extraction / verify) but carries
    the ORIGIN ``repository_section`` (the department) so every result it delivers is tagged to
    that department — never the working stage — keeping the Data Graveyard "by department"
    grouping correct. The research + extraction stages run STATELESS (server scrape, zero live
    budget); verify re-checks its sample url (its live session is bounded by the pool's
    live-session semaphore)."""
    schema = dept.extract_schema
    query = dept.query or dept.defn.label
    url = dept.target_url or ""
    params: dict[str, Any] = {
        "ghost_id": ghost_id,
        "repository_section": dept.id,
        "stage_section": stage.id,
        "stateless": True,
        "query": query,
        "urls": [url] if url else [],
        "url": url,
        "dwell_ms": 1200.0,
        "expect": {},
    }
    if schema is not None:
        params["extract_schema"] = schema
    kind = stage.accepts[0] if stage.accepts else "scout"
    return Task(
        id=ghost_id,
        kind=kind,
        mission_id=mission_id,
        target={"url": url} if url else {"action": kind},
        params=params,
    )


def _scout_seed_task(dept: Section, research: Section, mission_id: str) -> Task:
    """The scout seed that ENTERS the WorkQueue baton for one department.

    Routed to the research section, it is dispatched onto a WALKING pool stage ghost (the
    Option-A bridge) that discovers candidate urls and emits ``task.spawned{extract}`` — each
    hopping to a walking extraction ghost. It carries the origin ``repository_section`` +
    ``stateless`` so the whole baton stays department-tagged and session-free."""
    query = dept.query or dept.defn.label
    url = dept.target_url or ""
    params: dict[str, Any] = {
        "repository_section": dept.id,
        "stage_section": research.id,
        "stateless": True,
        "query": query,
        "seeds": [url] if url else [],
        "spawn_kind": "extract",
        "dwell_ms": 1000.0,
    }
    schema = dept.extract_schema
    if schema is not None:
        params["extract_schema"] = schema
    return Task(
        id=f"scout-{dept.id}",
        kind=research.accepts[0] if research.accepts else "scout",
        mission_id=mission_id,
        target={"query": query, "action": "scout"},
        params=params,
    )


@dataclass
class WorkforceRelay:
    """The BACKGROUND baton relay (PIPELINE-DESIGN "hybrid split rule").

    Replaces the old direct-to-department background spawn. It (1) ``pool.spawn``s a looping
    presence stage ghost per department at each of the research / extraction / verify sections —
    so those intermediate sections are finally NON-EMPTY + visible + deliver to their origin
    department — and (2) feeds a :class:`~ghostopia_orchestration.WorkQueue` seeded with one
    scout task per department, whose Option-A dispatch bridge routes each hop onto a fresh
    WALKING pool stage ghost (``stage-*``). A sustainer re-seeds the baton so the pipeline keeps
    flowing while the workforce runs. Every ghost is stateless-safe on the background path, so N ≫
    2 ghosts walk concurrently while the live-session budget stays untouched."""

    pool: Any
    sections: list[Section]
    broadcast: Callable[[Any], Awaitable[None]]
    mission_id: str = WORKFORCE_MISSION_ID
    baton_pause_s: float = 2.0
    #: opt-in ADVANCED real-retail department ids the operator has toggled on (197). An advanced
    #: department (``defn.advanced``, e.g. the real-store masks/costumes/haunted-market) gets NO
    #: background presence/baton ghosts until it is enabled — the safe keyless default never
    #: scrapes real retail (which needs the CAPTCHA-solving key-backed path) unless opted in.
    advanced_enabled: frozenset[str] = field(default_factory=frozenset)
    _running: bool = field(default=False, init=False)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list, init=False)

    def _by_id(self) -> dict[str, Section]:
        return {s.id: s for s in self.sections}

    def _present_departments(self) -> list[Section]:
        by_id = self._by_id()
        out: list[Section] = []
        for d in FLAGSHIP_DEPARTMENT_IDS:
            section = by_id.get(d)
            if section is None:
                continue
            if getattr(section.defn, "advanced", False):
                # An ADVANCED real-retail department is NEVER served by this background baton —
                # even when enabled. The baton is stateless + KEYLESS (``_scout_seed_task`` /
                # ``_stage_presence_task`` set ``stateless=True`` and no ``session_extract``), which
                # is right for the keyless book stores but WRONG for a CAPTCHA-protected retail
                # store: a keyless fetch there returns a challenge/nav-chrome page with no priced
                # products, and the crawl records those top-nav tiles as junk "results". The
                # advanced department is instead served SOLELY by its FEATURED, session-backed
                # ``session_extract`` ghost (``build_department_workforce``), which renders the
                # CAPTCHA-solved page through the managed fleet and lifts the real priced product
                # list. So the baton skips advanced departments entirely.
                continue
            out.append(section)
        return out

    async def start(self) -> list[str]:
        """Spawn the presence stage ghosts + launch the baton + sustainer. Returns stage ids."""
        self._running = True
        by_id = self._by_id()
        spawned: list[str] = []
        # (1) presence stage ghosts: research/extraction/verify are non-empty + visible NOW.
        for dept in self._present_departments():
            for stage_id in STAGE_SECTION_IDS:
                stage = by_id.get(stage_id)
                if stage is None:
                    continue
                gid = f"stage-{stage_id}-{dept.id}"
                if getattr(self.pool, "has", None) is not None and self.pool.has(gid):
                    spawned.append(gid)
                    continue
                with contextlib.suppress(ValueError):
                    await self.pool.spawn(
                        ghost_id=gid,
                        name=f"{stage.defn.label} · {dept.defn.label}",
                        section=stage,
                        behavior_name=stage.role,
                        task=_stage_presence_task(stage, dept, gid, self.mission_id),
                        color=0x8BE04A,
                        loop=True,  # keep the stage rosters populated between waves.
                    )
                    spawned.append(gid)
        # (2) the WorkQueue baton (Option-A dispatch bridge) + (3) the sustainer that re-seeds
        # it — both run in the background for as long as the workforce is running.
        if by_id.get("research") is not None:
            self._tasks.append(asyncio.ensure_future(self._baton_sustainer()))
        return spawned

    async def _baton_sustainer(self) -> None:
        """Re-seed + drain the scout→extraction baton while the workforce runs (the sustainer)."""
        research = self._by_id().get("research")
        if research is None:
            return
        dispatch = make_pool_stage_dispatch(self.pool, self.sections, self.broadcast)
        while self._running:
            queue = WorkQueue(
                self.sections,
                dispatch,
                max_concurrent=self.pool.visible_workforce_cap,
            )
            seeded = False
            for dept in self._present_departments():
                queue.enqueue(
                    _scout_seed_task(dept, research, self.mission_id), from_section=research
                )
                seeded = True
            if not seeded:
                return
            with contextlib.suppress(Exception):
                await queue.run()
            try:
                await asyncio.sleep(self.baton_pause_s)
            except asyncio.CancelledError:
                return

    async def stop(self) -> None:
        """Cancel the background baton + sustainer (the presence/stage ghosts are despawned by
        ``workforce.stop`` / the idle-teardown by their ``stage-*`` id prefix)."""
        self._running = False
        for t in self._tasks:
            if not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        self._tasks.clear()


async def run_department_workforce(
    pool: Any,
    sections: list[Section],
    *,
    loop: bool = False,
    on_spawn: Callable[[WorkforceSpec], Awaitable[None]] | None = None,
    start_relay: bool = True,
    broadcast: Callable[[Any], Awaitable[None]] | None = None,
    on_relay: Callable[[WorkforceRelay], None] | None = None,
    advanced_enabled: frozenset[str] = frozenset(),
) -> list[str]:
    """Materialize the flagship workforce: FEATURED solo pipeline ghosts + the BACKGROUND relay.

    * FEATURED — the ≤ live-budget ``dept-*`` ghosts from :func:`build_department_workforce`, each
      running ``pipeline_crawl`` SOLO (research → extraction → verify → deliver into its
      department → wander), session-backed so the operator can watch a real browser.
    * BACKGROUND — a :class:`WorkforceRelay` spawns ``stage-*`` ghosts through the
      research/extraction/verify sections and feeds a WorkQueue baton (Option-A dispatch bridge).

    Every spawn goes through the authoritative :meth:`GhostPool.spawn` and is idempotent per id
    (a start race that reaches ``spawn`` for an existing id is swallowed). Returns every
    spawned ghost id (featured + stage). ``loop`` keeps the featured ghosts alive between runs."""
    ids: list[str] = []
    for spec in build_department_workforce(sections, advanced_enabled=advanced_enabled):
        if getattr(pool, "has", None) is not None and pool.has(spec.ghost_id):
            ids.append(spec.ghost_id)
            continue
        try:
            await pool.spawn(
                ghost_id=spec.ghost_id,
                name=spec.name,
                section=spec.section,
                behavior_name=spec.behavior_name,
                task=spec.task,
                color=spec.color,
                loop=loop,
            )
        except ValueError:
            # (R3): a START RACE — a concurrent workforce.start already spawned this id
            # between our ``has`` check and this ``spawn``. Treat it as already-present
            # (idempotent) and NEVER let the ``"already in the pool"`` ValueError propagate out of
            # the caller (the WS receive loop) where it would tear the socket down.
            ids.append(spec.ghost_id)
            continue
        ids.append(spec.ghost_id)
        if on_spawn is not None:
            await on_spawn(spec)
    if start_relay:
        sink = broadcast if broadcast is not None else pool._broadcast
        relay = WorkforceRelay(pool, sections, sink, advanced_enabled=advanced_enabled)
        ids.extend(await relay.start())
        if on_relay is not None:
            on_relay(relay)
    return ids
