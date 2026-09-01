"""``GhostPool`` — N concurrent ghosts, each with its OWN session + behavior + section.

STAGE 5: the real workforce. Where STAGE 3 (``gc_event_source.run_real_task``)
drove ONE ghost through ONE real GhostCrawl session, ``GhostPool`` scales to 5–10 (v0;
architecture ready to grow to ~50) INDEPENDENT ghosts. Each ghost:

* owns its OWN :class:`GhostCrawlProvider` session (no session sharing);
* runs its OWN ``Behavior`` instance (created BY NAME from the registry) as its
  OWN ``asyncio`` task, ticked CONCURRENTLY and non-blocking (the
  non-blocking ``on_tick`` scales to ~50);
* belongs to exactly ONE :class:`~ghostopia_sections.Section` roster; the pool
  tracks section membership so the orchestrator can fan work out per section.

Concurrency is hard-capped at ``max_concurrent`` via an :class:`asyncio.Semaphore` so the
pool never storms the proxy/governor concurrency budget. Each ghost runs behind the :class:`InProcessExecutor`, so ``on_end`` fires
EXACTLY once and the session is released on EVERY terminal path (completed/failed/
cancelled/timed-out). Every emitted envelope is tagged with the ghost_id and broadcast over
the authed WS; the SELECTED ghost alone gets the rich frame stream (that lives in
:mod:`frame_fanout`; the pool registers each ghost's provider in the shared
:class:`SessionRegistry` so the fan-out can find it).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ghostopia_behavior_executor import (
    InProcessExecutor,
    RunLimits,
    build_capability_scoped_context,
    guard_browser_provider,
)
from ghostopia_behaviors import behaviors as behavior_registry
from ghostopia_behaviors.behavior import Behavior
from ghostopia_ghost_runtime.ghost_handle import create_ghost_handle
from ghostopia_ghost_runtime.surface_vocab import sanitize_code
from ghostopia_sections import Section
from ghostopia_shared import (
    Envelope,
    GhostAttention,
    GhostCommand,
    Point,
    SectionRef,
    Task,
)
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_world import WorldMap, create_world_query, load_default_map

from .frame_fanout import SessionRegistry
from .gc_event_source import _command_to_envelope, _grave_tile

#: An async fan-out sink (``WsGateway.broadcast`` in production; a collector in tests).
Broadcast = Callable[[Envelope], Awaitable[None]]

#: Builds the BrowserProvider ONE ghost runs against (a real ``GhostCrawlProvider`` in
#: production; a MOCK in tests). Zero-arg so the pool owns one session per ghost.
ProviderFactory = Callable[[], Any]

_SPAWN_TYPE = "ghost.spawned"


class _LiveSessionGate:
    """Wraps a provider so the SESSION-OPEN path (and ONLY it) acquires the live-session
    semaphore (R6).

    The single ``GhostPool`` semaphore used to CONFLATE "visible working ghosts" with "open
    live sessions", so only ``growth`` (2) ghosts could ever walk at once. This gate lets the
    visible-workforce semaphore bound WALKING ghosts (large) while a SEPARATE live-session
    semaphore (cap = ``me().max_concurrency``) hard-caps concurrent open sessions — a stateless
    ghost that never opens a session never touches the live budget, so N ≫ 2 stateless ghosts
    walk concurrently with no 429 storm. ``create_session`` / ``open`` acquire the live
    semaphore; ``release`` (and the pool's terminal drain) release every slot this provider
    holds (ref-counted, idempotent). Every other member delegates straight through, so the
    frame fan-out (``.session`` / ``.live_frames``) and the SSRF guard see the real provider.
    """

    def __init__(
        self,
        inner: Any,
        live_sema: asyncio.Semaphore,
        warm_pool: Any = None,
        crawl_sema: asyncio.Semaphore | None = None,
    ) -> None:
        self._inner = inner
        self._live_sema = live_sema
        self._warm_pool = warm_pool
        # The CRAWL-concurrency gate (the tier's ``max_concurrency``, e.g. 12) — DISTINCT from the
        # live-session cap (``max_live_sessions``, e.g. 3). Every data call (scrape/extract/search/
        # scrape_rendered) passes through it, so the whole workforce issues up to the tier's
        # concurrent-request budget at once (fast data throughput) while interactive live sessions
        # stay capped separately. Shared across every ghost's gate (one pool semaphore).
        self._crawl_sema = crawl_sema
        self._held = 0

    async def _crawl(self, coro: Any) -> Any:
        """Run a data-call coroutine under the shared crawl-concurrency gate (tier max_concurrency)."""
        if self._crawl_sema is None:
            return await coro
        async with self._crawl_sema:
            return await coro

    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        return await self._crawl(self._inner.scrape(*args, **kwargs))

    async def scrape_rendered(self, *args: Any, **kwargs: Any) -> Any:
        return await self._crawl(self._inner.scrape_rendered(*args, **kwargs))

    async def extract(self, *args: Any, **kwargs: Any) -> Any:
        return await self._crawl(self._inner.extract(*args, **kwargs))

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        return await self._crawl(self._inner.search(*args, **kwargs))

    async def create_session(self, target: str, profile_name: str | None = None) -> Any:
        # P3: a pre-warmed session makes the open INSTANT (no ``sessions.create`` round-trip).
        # The warm pool already holds the live-session slot for it; acquiring transfers that slot
        # to us (we release it on ``release``), so the cap is respected without a second acquire.
        # ``adopt_session`` is a real provider member; a warm session id that can't be adopted
        # (e.g. TTL-reaped) frees its slot and falls through to the cold open.
        if self._warm_pool is not None:
            got = self._warm_pool.acquire()
            if got is not None:
                session_id, engine = got
                self._held += 1
                try:
                    return self._inner.adopt_session(session_id, engine, target)
                except BaseException:
                    self._held -= 1
                    self._live_sema.release()
                    raise
        await self._live_sema.acquire()
        self._held += 1
        try:
            return await self._inner.create_session(target, profile_name)
        except BaseException:
            self._held -= 1
            self._live_sema.release()
            raise

    async def open(self, target: str, profile: str | None = None) -> Any:
        await self._live_sema.acquire()
        self._held += 1
        try:
            return await self._inner.open(target, profile)
        except BaseException:
            self._held -= 1
            self._live_sema.release()
            raise

    async def release(self) -> None:
        try:
            await self._inner.release()
        finally:
            self._drain()

    def _drain(self) -> None:
        """Release every live-session slot this provider still holds (idempotent)."""
        while self._held > 0:
            self._held -= 1
            self._live_sema.release()

    def __getattr__(self, name: str) -> Any:
        # Every non-session member (nav / session / scrape / search / live_frames / …)
        # delegates to the real provider unchanged.
        return getattr(self._inner, name)

# --------------------------------------------------------------------------------------
# Liveness derivation — REAL attention + health, kept as PURE helpers so the
# set→clear state machine + the budget math are unit-testable without a running pool.
# --------------------------------------------------------------------------------------

#: Envelope types whose arrival CLEARS a ghost's operator-attention flag: the ghost made
#: forward progress (a fresh session/nav/record) or finished, so the blocking condition
#: resolved. task.retry is intentionally NOT here — it is transient/self-healing, not an
#: operator ask; a NON-retryable browser.error or task.failed is what raises attention.
_ATTENTION_CLEAR_TYPES = frozenset(
    {
        "browser.session_opened",
        "browser.navigate",
        "result.scraped",
        "result.record_extracted",
        "result.verified",
        "task.completed",
    }
)


def attention_for_event(event_type: str, payload: dict[str, Any]) -> GhostAttention | None:
    """Derive an operator-attention SET signal from one emitted envelope, or ``None``.

    Attention is raised ONLY for conditions a human must clear:
    a captcha, a NON-retryable ``browser.error``, ``task.failed``, a pool-exhausted /
    auth-needed mapped error. A retryable/transient error (``task.retry``) does NOT raise
    it — the runtime self-heals. The reason is the server-sourced error code (never authored
    copy). Returns ``None`` when the event does not itself indicate a blocking condition; the
    caller separately clears attention on a resolving event (:data:`_ATTENTION_CLEAR_TYPES`).
    """
    # The attention REASON is a customer-facing field (rendered as the roster "!"
    # tooltip). It is sanitized through the curated vocabulary BEFORE it leaves this function
    # so a raw/vendor-named error code (e.g. "cloudflare_challenge") can never surface — the
    # BLOCKING decision below still reads the RAW code (internal), only the reason is curated.
    if event_type == "task.failed":
        code = str(payload.get("code") or "")
        return GhostAttention(needs=True, reason=sanitize_code(code or "task_failed"))
    if event_type in ("browser.error", "task.retry"):
        code = str(payload.get("code") or "")
        visual = str(payload.get("visual") or "")
        hay = f"{code} {visual}".lower()
        retryable = payload.get("retryable")
        # captcha / auth / pool-exhausted always need the operator, even if flagged retryable.
        blocking_word = any(
            w in hay for w in ("captcha", "auth", "login", "pool", "exhaust", "forbidden")
        )
        if blocking_word or retryable is False:
            return GhostAttention(needs=True, reason=sanitize_code(code or event_type))
    return None


@dataclass
class GhostRecord:
    """One live ghost the pool manages: identity + session handle + current task + coarse
    state + its mounted :class:`Behavior` + its :class:`Section`.

    The mutable ``state``/``current_url``/``progress``/``record_count`` fields are the
    lightweight status the roster HUD shows (kept fresh by :func:`start_status_poll`); they
    are derived from the ghost's own emitted envelopes (:meth:`GhostPool._observe`)."""

    ghost_id: str
    name: str
    behavior_name: str
    section_id: str
    provider: Any = None
    behavior: Behavior | None = None
    section: Section | None = None
    task: Task | None = None
    state: str = "IDLE"
    current_url: str | None = None
    progress: float = 0.0
    record_count: int = 0
    session_id: str | None = None
    run_task: asyncio.Task[None] | None = None
    # -- liveness — REAL attention + enforced wall-clock budget for the fuel-gauge --
    #: raised when the ghost hits an operator-only condition (captcha / non-retryable error /
    #: task.failed / pool-exhausted); cleared on a resolving event. None == no attention.
    attention: GhostAttention | None = None
    # -- runtime MANAGEMENT state (applied by handle_management_command) --
    #: an operator-pinned behavior override (wins in resolve_behavior on the next re-mount).
    behavior_override: str | None = None
    #: while paused, the executor tick loop SKIPS on_tick — a real halt, not just a
    #: flag: no provider calls, budget frozen, until resumed.
    paused: bool = False
    #: a set abort event stops the in-flight run cooperatively (cancel/retarget); the executor
    #: (pool path) and run_real_task (fan-out path) both honor it → on_end(cancelled)+release.
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    # -- operator commands: the ghost's last authoritative TILE + a forced target --
    #: last known standing tile (A* start for an operator send/recall/reassign re-path).
    last_tile: Point | None = None
    #: an operator-forced destination TILE (send-to-workstation / recall / reassign target).
    forced_target: Point | None = None
    # -- world-visibility: the ghost's spawn colour + the last positioned ghost.spawned
    #    payload (so a late-joiner replay can re-send the exact spawn), and a loop flag so
    #    a workforce ghost re-runs its behavior + stays visibly alive instead of flash-completing.
    color: int = 0x9B7BFF
    #: the last positioned ghost.spawned payload broadcast for this ghost (replay-on-connect).
    spawn_payload: dict[str, Any] | None = None
    #: when True the pool re-runs the behavior (fresh session + instance) with a short roam
    #: pause between runs until aborted — keeps the keyless workforce populated + roaming.
    loop: bool = False
    #: an optional per-ghost emit sink: the WorkforceRelay dispatch bridge passes a
    #: capture that tees ``task.spawned`` into its WorkQueue before forwarding to the real
    #: broadcast, so a walking baton stage ghost's discovered urls route to the next stage.
    #: None → the pool's own broadcast (the normal path).
    emit_broadcast: Broadcast | None = None
    #: set by :meth:`despawn` before it aborts the run so the ``_run`` teardown knows a
    #: ``ghost.despawned`` (client sprite REMOVAL → dissolve fade) is coming and must NOT also
    #: drive the grave-sink de-materialize. A plain ``ghost.manage cancel`` leaves this
    #: False, so its aborted run DOES sink into a grave instead of freezing at full alpha.
    despawning: bool = False


@dataclass(frozen=True)
class GhostStatus:
    """A frame-free snapshot of one ghost's status (what the roster HUD renders)."""

    ghost_id: str
    name: str
    section_id: str
    behavior_name: str
    state: str
    task: str | None
    current_url: str | None
    progress: float
    record_count: int
    #: operator-attention flag (captcha / non-retryable error / …) or None → no alert.
    attention: GhostAttention | None = None


class GhostPool:
    """Manages up to ``max_concurrent`` concurrent ghosts, each independent.

    Each :meth:`spawn` builds a ghost record (its own provider + behavior + section), then
    launches an ``asyncio`` task that — under the concurrency semaphore — runs the behavior
    through the :class:`InProcessExecutor` against a capability-scoped ``BehaviorContext``.
    A completed ghost frees its slot, leaves its section roster, and unregisters its
    provider from the frame :class:`SessionRegistry`.
    """

    def __init__(
        self,
        broadcast: Broadcast,
        *,
        world_map: WorldMap | None = None,
        provider_factory: ProviderFactory,
        registry: Any = behavior_registry,
        session_registry: SessionRegistry | None = None,
        sections: list[Section] | None = None,
        max_concurrent: int = 8,
        visible_workforce_cap: int | None = None,
        wall_clock_ms: float = 300_000.0,
        tick_deadline_ms: float = 5_000.0,
        tick_interval_ms: float = 50.0,
        is_url_allowed: Callable[[str], Any] | None = None,
        seed: int = 1337,
        loop_pause_s: float = 1.5,
        walk_arrival_per_tile_s: float = 0.11,
        walk_arrival_max_s: float = 2.5,
    ) -> None:
        self._broadcast = broadcast
        self._map = world_map if world_map is not None else load_default_map()
        self._provider_factory = provider_factory
        self._registry = registry
        self._session_registry = session_registry
        # (R6): the LIVE-SESSION cap (``me().max_concurrency``, growth = 2) and the
        # VISIBLE-WORKFORCE cap are DECOUPLED. ``_max_concurrent`` is the live-session cap
        # (what ``max_concurrent`` reports; the mission WorkQueue + surfacing follow it, and
        # the ``_live_sema`` gates every session-open). The ``_visible_sema`` (larger) bounds
        # concurrently WALKING ghosts so N ≫ 2 stateless ghosts walk while live sessions stay
        # capped. When the visible cap is unset it follows the live cap (legacy single-cap).
        self._max_concurrent = max_concurrent
        self._visible_max = visible_workforce_cap if visible_workforce_cap else max_concurrent
        self._live_sema = asyncio.Semaphore(max_concurrent)
        self._visible_sema = asyncio.Semaphore(self._visible_max)
        # CRAWL-concurrency cap (the tier's ``max_concurrency``) — how many data calls
        # (scrape/extract/search) run at once across the whole workforce, DECOUPLED from the
        # live-session cap. Defaults to the visible cap until the entitlement value is applied at
        # startup (:meth:`set_crawl_concurrency`); every ghost's live gate shares this semaphore.
        self._crawl_max = self._visible_max
        self._crawl_sema = asyncio.Semaphore(self._crawl_max)
        self._limits = RunLimits(
            wall_clock_ms=wall_clock_ms, tick_deadline_ms=tick_deadline_ms
        )
        self._executor = InProcessExecutor(tick_interval_ms=tick_interval_ms)
        # When a real SSRF gate is injected, every behavior-navigated URL is validated AT
        # the handle. None → the raw provider (tests / a caller that
        # gates upstream); production composition injects the real gate.
        self._is_url_allowed = is_url_allowed
        self._seed = seed
        self._loop_pause_s = loop_pause_s
        #: server-side arrival clock: per-tile travel time + a cap (the pool has no sim clock, so
        #: a directly-spawned ghost's walk must be COMPLETED here or a work behavior's
        #: ``at_workstation()`` gate never opens — the ghost would stall forever mid-walk).
        self._walk_arrival_per_tile_s = walk_arrival_per_tile_s
        self._walk_arrival_max_s = walk_arrival_max_s
        self._records: dict[str, GhostRecord] = {}
        # P1 watched-hold: a probe returning the fanout's CURRENTLY-selected ghost id (or None).
        # Wired post-construction by the app (the fanout is built after the pool). Each ghost's
        # context gets a ``watched`` predicate = "am I the selected ghost?", which a session-backed
        # behavior uses to hold its live browser open + moving while the operator watches it.
        self._selected_probe: Callable[[], str | None] | None = None
        # P3 warm-session pool: hands a featured ghost a pre-opened session so its browser is
        # already warm (wired post-construction — the pool needs the SDK client + this sema).
        self._warm_pool: Any = None
        self._world_query = create_world_query(self._map)
        # The section runtimes the orchestrator fans work into. Shared runtimes so
        # roster/queue/capacity accumulate across ghosts; keyed by id for lookup.
        self._sections: dict[str, Section] = {s.id: s for s in (sections or [])}

    # -- introspection ---------------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def live_session_cap(self) -> int:
        """The concurrent OPEN-SESSION cap (``me().max_concurrency``); == :pyattr:`max_concurrent`."""
        return self._max_concurrent

    @property
    def visible_workforce_cap(self) -> int:
        """The concurrent WALKING/working-ghost cap — decoupled from (≥) the live-session cap."""
        return self._visible_max

    def set_max_concurrent(self, value: int) -> None:
        """Reset the LIVE-SESSION cap to the entitlement-derived value.

        Called ONCE at startup, BEFORE any ghost is spawned, after the cap is resolved from
        the ghostcrawl ``me().max_concurrency`` (an async context the sync app factory can't
        reach). Rebuilds the LIVE-SESSION semaphore to the new bound so this pool — and every
        mission WorkQueue that follows :pyattr:`max_concurrent` — enforces the tier/self-host
        concurrent-live-session cap as the single source of truth. The VISIBLE workforce cap is
        set independently (:meth:`set_visible_workforce_cap`) so more ghosts can WALK than open
        live sessions. A non-positive cap is rejected (never unbounded)."""
        if value <= 0:
            raise ValueError("max_concurrent must be positive")
        self._max_concurrent = value
        self._live_sema = asyncio.Semaphore(value)

    #: alias — the entitlement handler reads more naturally as "set the live-session cap".
    set_live_session_cap = set_max_concurrent

    @property
    def live_session_semaphore(self) -> asyncio.Semaphore:
        """The CURRENT live-session semaphore (rebuilt at startup by :meth:`set_max_concurrent`).

        The warm-session pool reads this lazily so it always warms against the entitlement cap
        the pool actually enforces (not the provisional boot cap)."""
        return self._live_sema

    def set_warm_pool(self, warm_pool: Any) -> None:
        """Wire the P3 warm-session pool the live gate hands pre-opened sessions from."""
        self._warm_pool = warm_pool

    @property
    def crawl_concurrency(self) -> int:
        """The concurrent data-call cap (the tier's ``max_concurrency``)."""
        return self._crawl_max

    def set_crawl_concurrency(self, value: int) -> None:
        """Set the crawl-concurrency cap to the tier's ``max_concurrency`` (startup).

        The whole workforce then issues up to this many concurrent scrapes/extracts/searches —
        the tier's request concurrency — for fast data throughput, while interactive live sessions
        stay capped separately at ``max_live_sessions``. Applied once before any ghost spawns."""
        if value <= 0:
            raise ValueError("crawl_concurrency must be positive")
        self._crawl_max = value
        self._crawl_sema = asyncio.Semaphore(value)

    def set_selected_probe(self, probe: Callable[[], str | None] | None) -> None:
        """Wire the fanout's selected-ghost probe (P1 watched-hold).

        ``probe()`` returns the ghost id the operator is currently watching (or ``None``). The
        pool threads a per-ghost ``watched`` predicate (``probe() == ghost_id``) into each
        behavior's context so a session-backed behavior can keep its live browser open while it
        is the watched ghost. Called once at startup after the fanout exists."""
        self._selected_probe = probe

    def _watched_predicate(self, ghost_id: str) -> Callable[[], bool]:
        """A ``() -> bool`` predicate: is ``ghost_id`` the operator's currently-watched ghost?"""

        def _watched() -> bool:
            probe = self._selected_probe
            if probe is None:
                return False
            try:
                return probe() == ghost_id
            except Exception:  # noqa: BLE001 - a probe fault must never break a ghost run
                return False

        return _watched

    def set_visible_workforce_cap(self, value: int) -> None:
        """Reset the VISIBLE-WORKFORCE cap — how many ghosts may WALK/work concurrently.

        Decoupled from the live-session cap: a background baton pipeline runs far more visible
        stage ghosts than the small concurrent-live-session budget allows, because stateless
        ghosts never open a session. Called ONCE at startup, before any ghost spawns. A
        non-positive cap is rejected."""
        if value <= 0:
            raise ValueError("visible_workforce_cap must be positive")
        self._visible_max = value
        self._visible_sema = asyncio.Semaphore(value)

    @property
    def sections(self) -> list[Section]:
        """The shared :class:`Section` runtimes work fans into (rosters)."""
        return list(self._sections.values())

    @property
    def world_map(self) -> WorldMap:
        """The authoritative world map (operator commands re-path against it)."""
        return self._map

    def set_world_map(self, world: WorldMap) -> None:
        """Swap the authoritative world map (``map.save``): future re-paths + world
        queries use the new collision/A* grid. In-flight walk animations finish client-side;
        the validated save guarantees every destination stays reachable, so no ghost is trapped."""
        self._map = world
        self._world_query = create_world_query(self._map)

    def section(self, section_id: str) -> Section:
        """Look up a shared :class:`Section` runtime by id (unknown id raises ``KeyError``)."""
        return self._sections[section_id]

    def record(self, ghost_id: str) -> GhostRecord:
        """The live :class:`GhostRecord` for ``ghost_id`` (unknown id raises ``KeyError``).

        The authoritative per-ghost state the runtime management surface
        (:func:`ghostopia_server.management.handle_management_command`) mutates."""
        return self._records[ghost_id]

    def has(self, ghost_id: str) -> bool:
        """True when ``ghost_id`` is a live pool ghost."""
        return ghost_id in self._records

    @property
    def active_count(self) -> int:
        """Ghosts whose run task is still in flight."""
        return sum(
            1 for r in self._records.values() if r.run_task is not None and not r.run_task.done()
        )

    def ghosts_by_section(self) -> dict[str, list[str]]:
        """Section id → the ghost_ids currently rostered there (the fan-out unit)."""
        out: dict[str, list[str]] = {}
        for rec in self._records.values():
            out.setdefault(rec.section_id, []).append(rec.ghost_id)
        return out

    def snapshot(self) -> list[GhostStatus]:
        """A frame-free status snapshot per ghost (consumed by :func:`start_status_poll`)."""
        return [
            GhostStatus(
                ghost_id=r.ghost_id,
                name=r.name,
                section_id=r.section_id,
                behavior_name=r.behavior_name,
                state=r.state,
                task=(r.task.target.get("url") if r.task and r.task.target else None),
                current_url=r.current_url,
                progress=r.progress,
                record_count=r.record_count,
                attention=r.attention,
            )
            for r in self._records.values()
        ]

    # -- spawn / lifecycle -----------------------------------------------------------

    async def spawn(
        self,
        *,
        ghost_id: str,
        name: str,
        section: Section,
        behavior_name: str,
        behavior: Behavior | None = None,
        task: Task | None = None,
        color: int = 0x9B7BFF,
        loop: bool = False,
        on_emit: Broadcast | None = None,
    ) -> GhostRecord:
        """Add a ghost: its own provider + behavior + section, launched on its own task.

        ``behavior`` may be injected (tests); otherwise it is created BY NAME from the
        registry (``registry.create(behavior_name)``) — a FRESH instance per ghost, so no
        two ghosts share behavior state. The ghost joins ``section``'s roster immediately
        (so the orchestrator sees membership) and is removed on completion.

        ``loop``: when True the ghost re-runs its behavior (fresh session + fresh
        instance) with a short roam pause between runs until aborted — the keyless
        workforce keeps 18 ghosts visibly alive + working instead of flash-completing once.
        """
        if ghost_id in self._records:
            raise ValueError(f"ghost {ghost_id!r} already in the pool")
        provider = self._provider_factory()
        beh = behavior if behavior is not None else self._registry.create(behavior_name)
        rec = GhostRecord(
            ghost_id=ghost_id,
            name=name,
            behavior_name=behavior_name,
            section_id=section.id,
            provider=provider,
            behavior=beh,
            section=section,
            task=task,
            color=color,
            loop=loop,
            emit_broadcast=on_emit,
        )
        self._records[ghost_id] = rec
        section.add_ghost(ghost_id)  # roster membership
        rec.run_task = asyncio.ensure_future(self._run(rec, color))
        return rec

    def register_external(
        self,
        *,
        ghost_id: str,
        name: str,
        behavior_name: str,
        section: Section,
    ) -> GhostRecord:
        """Register a ghost the pool does NOT itself execute (unification).

        A mission fan-out (orchestrator) runs each task on the selected brain through
        ``run_real_task`` rather than the pool's executor, so those ghosts were invisible to
        the management surface (``ghost.manage`` only saw ``spawn``'d pool ghosts). This makes
        the pool the ONE authoritative ghost registry: the orchestrator registers each fan-out
        ghost here so pause/resume/cancel/retarget/behavior-override reach it BY ID. The record
        carries no ``provider``/``behavior`` (the runner owns those); ``paused``/``abort_event``
        are the seams ``run_real_task`` honors. Idempotent — an existing id is returned as-is.
        """
        rec = self._records.get(ghost_id)
        if rec is not None:
            return rec
        rec = GhostRecord(
            ghost_id=ghost_id,
            name=name,
            behavior_name=behavior_name,
            section_id=section.id,
            section=section,
            state="WORKING",
        )
        self._records[ghost_id] = rec
        section.add_ghost(ghost_id)
        return rec

    async def despawn(self, ghost_id: str) -> bool:
        """Remove ``ghost_id`` from the pool authoritatively (operator remove).

        Signals its abort event + cancels the in-flight run (the executor / runner fire
        ``on_end(cancelled)`` + release the session on the way out), drops it from its
        section roster, unregisters its frame session, and deletes the record so the roster
        no longer counts it. Returns False for an unknown id (idempotent). The caller
        broadcasts a ``ghost.despawned`` envelope so the thin renderer removes the sprite."""
        rec = self._records.get(ghost_id)
        if rec is None:
            return False
        # Mark the record so the run's teardown skips the grave-sink de-materialize: the
        # caller broadcasts ``ghost.despawned`` and the thin renderer fades the REMOVED sprite via
        # its dissolve, so a sink here would be redundant. A manage-cancel never sets this.
        rec.despawning = True
        rec.abort_event.set()
        if rec.run_task is not None and not rec.run_task.done():
            rec.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rec.run_task
        if rec.section is not None:
            rec.section.remove_ghost(ghost_id)
        if self._session_registry is not None:
            self._session_registry.unregister(ghost_id)
        await self._ensure_released(rec.provider)
        self._records.pop(ghost_id, None)
        return True

    async def _run(self, rec: GhostRecord, color: int) -> None:
        """Run ONE ghost (its own asyncio task); re-run while ``rec.loop``.

        The positioned ``ghost.spawned`` is announced BEFORE the concurrency slot is acquired,
        so a ghost queued behind the cap is visible in the world immediately (the workforce's
        18 ghosts all render at once, not only as slots free). A ``loop`` ghost re-runs its
        behavior (fresh instance + fresh session) with a short roam pause between cycles until
        aborted — keeping the keyless workforce populated + roaming."""
        assert rec.behavior is not None  # spawn always mounts a behavior; external ghosts don't _run
        # a per-ghost command queue bridges the SYNC GhostHandle sink to the ASYNC WS.
        cmd_q: asyncio.Queue[GhostCommand] = asyncio.Queue()
        drain = asyncio.ensure_future(self._drain_commands(rec, cmd_q))
        try:
            # Spread spawns across ALL graves (keyed on ghost id) so ghosts don't pile on
            # graves[0] — a nice spatial spread with sharing, not a designated home.
            grave = _grave_tile(self._map, key=rec.ghost_id)
            rec.last_tile = grave
            handle = create_ghost_handle(
                rec.ghost_id, cmd_q.put_nowait, self._map, start=grave,
                section_id=rec.section_id,
            )
            # server-side ARRIVAL clock: complete each emitted walk after a travel beat so a work
            # behavior's ``at_workstation()`` gate opens (a mission uses the orchestrator's arrival
            # brackets; a directly-spawned pool/workforce ghost has no such driver and would stall
            # mid-walk forever). Concurrent with the executor; cancelled in the finally.
            mover = asyncio.ensure_future(self._drive_arrivals(rec, handle))
            # announce the positioned spawn OUTSIDE the semaphore → visible while queued.
            await self._announce_spawn(rec, grave, color)
            first = True
            while True:
                async with self._visible_sema:  # hard cap on concurrently WALKING ghosts
                    if rec.abort_event.is_set():
                        break
                    if not first:
                        # a looping ghost re-runs: fresh behavior instance + fresh session so
                        # each cycle is a clean run (mock or real), released in the finally.
                        rec.behavior = self._registry.create(rec.behavior_name)
                        rec.provider = self._provider_factory()
                    first = False
                    rec.state = "WORKING"
                    rec.progress = 0.0
                    # 196: announce task.started so the results recorder records this task's
                    # SECTION (the Data Graveyard's per-department grouping LEFT-JOINs
                    # tasks.section) and the mission rollup counts it. A directly-spawned ghost
                    # carrying a real task (workforce/department) emits it here — the mission path
                    # has its own task.started; an ambient/caretaker ghost (no task) has none.
                    if rec.task is not None:
                        await self._announce_task_started(rec)
                    # (R6): the SESSION-OPEN path acquires the LIVE-SESSION semaphore
                    # (only ``create_session`` / ``open``), so live sessions stay capped at the
                    # plan limit no matter how many ghosts WALK. A stateless ghost never opens a
                    # session → never touches the live budget. The gate wraps the real provider,
                    # then the SSRF guard wraps that — the frame fan-out sees the passed-through
                    # ``.session`` / ``.live_frames`` unchanged.
                    live_gated = _LiveSessionGate(
                        rec.provider, self._live_sema, self._warm_pool, self._crawl_sema
                    )
                    if self._session_registry is not None:
                        self._session_registry.register(rec.ghost_id, live_gated)
                    guarded = (
                        guard_browser_provider(live_gated, self._is_url_allowed)
                        if self._is_url_allowed is not None
                        else live_gated
                    )
                    section_ref = self._section_ref(rec)
                    ctx = build_capability_scoped_context(
                        ghost=handle,
                        browser=guarded,
                        world=self._world_query,
                        emit=self._make_emit(rec),
                        task=rec.task,
                        section=section_ref,
                        seed=self._seed + (hash(rec.ghost_id) % 100_000),
                        watched=self._watched_predicate(rec.ghost_id),
                    )
                    try:
                        await self._executor.run(
                            rec.behavior,
                            ctx,
                            self._limits,
                            # real management: a paused ghost stops ticking (no provider calls); a
                            # set abort event stops the run cleanly (on_end(cancelled) + release).
                            paused=lambda: rec.paused,
                            abort=rec.abort_event,
                        )
                    finally:
                        # the executor already fired on_end + released the session; a second
                        # release is a no-op (release is idempotent) but we surface it
                        # explicitly. Releasing through the LIVE gate DRAINS any live-session
                        # slot the ghost still holds (a run that ended without its own release)
                        # so the live budget can never leak.
                        if self._session_registry is not None:
                            self._session_registry.unregister(rec.ghost_id)
                        await self._ensure_released(live_gated)
                # loop control: stop unless this is a looping ghost that's still wanted.
                if not rec.loop or rec.abort_event.is_set():
                    break
                # a brief roam/idle pause between cycles so the graveyard stays alive + moving.
                rec.state = "IDLE"
                rec.progress = 1.0
                try:
                    await asyncio.sleep(self._loop_pause_s)
                except asyncio.CancelledError:
                    break
        finally:
            # completion: leave the section roster, drop the frame registration, mark done.
            # 196 FIX 3: free any workstation seat this ghost still holds so it never leaks a
            # seat on despawn/abort (release is idempotent — a no-op if already vacated). Guarded
            # via locals() since `handle` may be unbound if the try failed before it was built.
            release_seat = getattr(locals().get("handle"), "release_workstation", None)
            if callable(release_seat):
                release_seat()
            if rec.section is not None:
                rec.section.remove_ghost(rec.ghost_id)
            if self._session_registry is not None:
                self._session_registry.unregister(rec.ghost_id)
            rec.state = "COMPLETED"
            rec.progress = 1.0
            # de-materialize on cancel: a ghost whose run was CANCELLED via ``ghost.manage
            # cancel`` — aborted but NOT removed via ``despawn`` (which fades the removed sprite
            # through the client dissolve) — would otherwise freeze in the world at full alpha,
            # because the arrival clock is cancelled below before it can turn the return-home walk
            # into the resting facing. Drive that RETURN + resting facing here so the client
            # reaches ``IDLE`` — the sole trigger of the grave-sink de-materialize animation. A
            # normally-COMPLETED ghost (abort NOT set) keeps the ending its behavior already
            # played (deliver-rest / wander); a despawning ghost is left to the dissolve fade.
            if rec.abort_event.is_set() and not rec.despawning:
                _home = locals().get("handle")
                _walk_home = getattr(_home, "walk_home", None)
                _face_rest = getattr(_home, "face_rest", None)
                if callable(_walk_home):
                    with contextlib.suppress(Exception):
                        _walk_home()
                if callable(_face_rest):
                    with contextlib.suppress(Exception):
                        _face_rest()
            # flush any trailing commands (e.g. the on_end walk_home + the resting facing),
            # then stop drain.
            while not cmd_q.empty():
                await self._broadcast(
                    _command_to_envelope(cmd_q.get_nowait(), self._map.tile_size)
                )
            mover.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await mover
            drain.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain

    async def _drive_arrivals(self, rec: GhostRecord, handle: Any) -> None:
        """Server-side arrival clock for a directly-spawned ghost (no sim/orchestrator driver).

        Polls the handle: when it starts a walk, waits a travel beat (paced to the A* path
        length, capped) and then COMPLETES the walk — a ``workstation`` walk arrives
        ``at_workstation=True`` so the work behavior's gate opens; any other walk (home/idle)
        arrives idle. Without this a pool/workforce ghost stalls forever in its WALKING step."""
        # IN-05: the movement surface this driver uses is is_walking + arrive AND
        # last_walk_tiles + last_walk_mode — guard ALL of them so a partial handle double
        # (one that has is_walking/arrive but not the last_walk_* accessors) is treated as
        # "no movement surface" and returns cleanly instead of AttributeError-ing mid-loop.
        is_walking = getattr(handle, "is_walking", None)
        arrive = getattr(handle, "arrive", None)
        last_walk_tiles = getattr(handle, "last_walk_tiles", None)
        last_walk_mode = getattr(handle, "last_walk_mode", None)
        if (
            is_walking is None
            or arrive is None
            or last_walk_tiles is None
            or last_walk_mode is None
        ):  # a test double without the full movement surface
            return
        while True:
            await asyncio.sleep(0.05)
            if rec.abort_event.is_set():
                return
            if not is_walking():
                continue
            tiles = max(1, last_walk_tiles())
            beat = min(self._walk_arrival_max_s, tiles * self._walk_arrival_per_tile_s)
            await asyncio.sleep(beat)  # cancellable — cancel() (teardown) must stop the mover
            # still the same in-flight walk (the behavior did not already re-issue one)? complete it.
            if is_walking() and not rec.abort_event.is_set():
                mode = last_walk_mode()
                arrive(at_workstation=(mode == "workstation"))
                # On a HOME arrival, emit the RESTING facing so the client transitions the ghost to
                # IDLE and it SINKS back into its grave (the dematerialize beat). Without this the
                # live/workforce ghost stalls at the grave at full alpha and never sinks — the sink
                # animation only fires on client state IDLE, which only face_rest produces (the sim
                # path did this via GhostDriver; the pool path did not). "deliver" (section drop)
                # also lands idle so its ghost settles too.
                if mode in ("home", "deliver"):
                    face_rest = getattr(handle, "face_rest", None)
                    if face_rest is not None:
                        face_rest()

    async def _drain_commands(
        self, rec: GhostRecord, queue: asyncio.Queue[GhostCommand]
    ) -> None:
        """Forward GhostHandle commands → ``ghost.command`` envelopes in FIFO order."""
        tile_size = self._map.tile_size
        while True:
            command = await queue.get()
            await self._broadcast(_command_to_envelope(command, tile_size))

    def _make_emit(self, rec: GhostRecord) -> Callable[[Envelope], Awaitable[None]]:
        """A per-ghost async emit: tag the envelope with the ghost_id, derive status, fan out."""

        async def emit(env: Envelope) -> None:
            tagged = env if env.ghost_id else env.model_copy(update={"ghost_id": rec.ghost_id})
            self._observe(rec, tagged)
            # A relay stage ghost routes its emitted envelopes through the dispatch
            # bridge's capture (which tees ``task.spawned`` into the WorkQueue) instead of the
            # bare broadcast, so the baton hops to the next stage's walking ghost.
            sink = rec.emit_broadcast or self._broadcast
            await sink(tagged)

        return emit

    def _observe(self, rec: GhostRecord, env: Envelope) -> None:
        """Derive the ghost's lightweight roster status from its own emitted envelopes."""
        payload = env.payload if isinstance(env.payload, dict) else {}
        t = env.type
        # liveness: raise operator-attention on a blocking condition; clear it when a
        # resolving event proves the ghost made progress again (set→clear).
        raised = attention_for_event(t, payload)
        if raised is not None:
            rec.attention = raised
        elif t in _ATTENTION_CLEAR_TYPES:
            rec.attention = None
        if t == "browser.session_opened":
            sid = payload.get("session_id")
            rec.session_id = str(sid) if sid else rec.session_id
            rec.state = "OPENING_BROWSER"
        elif t == "browser.navigate":
            url = payload.get("url")
            rec.current_url = str(url) if url else rec.current_url
            rec.state = "NAVIGATING"
        elif t == "task.progress":
            prog = payload.get("progress")
            if isinstance(prog, (int, float)):
                rec.progress = float(prog)
            recs = payload.get("records")
            if isinstance(recs, int):
                rec.record_count = recs
        elif t in ("result.scraped", "result.record_extracted"):
            rec.record_count += 1
            rec.state = "EXTRACTING"
        elif t == "result.verified":
            rec.state = "PROCESSING"
        elif t == "browser.error" or t == "task.retry":
            rec.state = "ERROR"
        elif t == "task.completed":
            rec.state = "COMPLETED"
            rec.progress = 1.0
        elif t == "ghost.wander":
            rec.state = "WALKING"

    def _section_ref(self, rec: GhostRecord) -> SectionRef | None:
        if rec.section is None:
            return None
        return SectionRef(
            id=rec.section.id,
            role=rec.section.role,
            bounds=rec.section.bounds,
            roster=list(rec.section.roster),
        )

    def _spawn_payload(self, rec: GhostRecord, grave: Point, color: int) -> dict[str, Any]:
        """The positioned ``ghost.spawned`` payload for ``rec`` (id/name/section/pos/behavior).

        The ghost's REAL ``section_id`` rides the payload so the world sprite lands in the
        same section as its roster row (no roster↔world divergence)."""
        tile_size = self._map.tile_size
        # No ``home_grave`` — graves are transient shared rest spots chosen nearest-free at
        # return time, never a persisted per-ghost designated home.
        return {
            "id": rec.ghost_id,
            "name": rec.name,
            "section": rec.section_id,
            "color": color,
            "state": "IDLE",
            "position": {
                "x": grave.x * tile_size + tile_size / 2.0,
                "y": grave.y * tile_size + tile_size,
            },
            "behavior": rec.behavior_name,
        }

    async def _announce_spawn(
        self, rec: GhostRecord, grave: Point, color: int
    ) -> None:
        """Broadcast ``ghost.spawned`` so a fresh client renders the ghost at its grave.

        The payload is stashed on the record (``spawn_payload``) so a late-joining client can
        be replayed the EXACT positioned spawn (:meth:`spawn_snapshot`)."""
        rec.color = color
        payload = self._spawn_payload(rec, grave, color)
        rec.spawn_payload = payload
        await self._broadcast(
            serialize_envelope(
                type=_SPAWN_TYPE, ts=time.time(), ghost_id=rec.ghost_id, payload=payload
            )
        )

    async def _announce_task_started(self, rec: GhostRecord) -> None:
        """Broadcast ``task.started`` for a task-bearing ghost so the results recorder inserts
        the task row (mission + section + behavior + url).

        The recorder LEFT-JOINs ``tasks.section`` onto each result for the Data Graveyard's
        per-department grouping, and counts task rows for the mission rollup — so a
        directly-spawned workforce/department ghost must announce its task exactly as the mission
        fan-out does, or its real finds land section-less (196). NAMES/urls only; no key."""
        if rec.task is None:
            return
        url = rec.task.target.get("url") if isinstance(rec.task.target, dict) else None
        await self._broadcast(
            serialize_envelope(
                type="task.started",
                ts=time.time(),
                ghost_id=rec.ghost_id,
                payload={
                    "task_id": rec.task.id,
                    "mission_id": rec.task.mission_id,
                    "kind": rec.task.kind,
                    "section": rec.section_id,
                    "behavior": rec.behavior_name,
                    "url": url,
                },
            )
        )

    def spawn_snapshot(self) -> list[Envelope]:
        """A positioned ``ghost.spawned`` per CURRENT pool ghost (replay-on-connect).

        Replayed to a freshly-connected / refreshing client so it renders the ghosts that
        spawned before it connected (roster-full-but-empty-canvas fix). Idempotent on the
        client (upsert by ghost_id) — a re-sent id updates, never duplicates. An externally
        registered fan-out ghost (:meth:`register_external`, no positioned announce of its own)
        falls back to a freshly-built payload from its record so it is replayed too."""
        grave = _grave_tile(self._map)
        out: list[Envelope] = []
        for rec in self._records.values():
            payload = rec.spawn_payload or self._spawn_payload(rec, grave, rec.color)
            out.append(
                serialize_envelope(
                    type=_SPAWN_TYPE, ts=time.time(), ghost_id=rec.ghost_id, payload=payload
                )
            )
        return out

    @staticmethod
    async def _ensure_released(provider: Any) -> None:
        release = getattr(provider, "release", None)
        if release is None:
            return
        with contextlib.suppress(Exception):
            await release()

    # -- teardown --------------------------------------------------------------------

    def active_by_prefix(self, prefix: str) -> int:
        """Count ghosts whose id starts with ``prefix`` and whose run is still in flight.

        The sustainer top-up uses this to keep a family of ghosts (e.g. the ``stage-*`` baton
        workers) populated at its desired count — a completed/despawned ghost drops out, and
        :meth:`sustain` respawns the shortfall."""
        return sum(
            1
            for gid, r in self._records.items()
            if gid.startswith(prefix) and r.run_task is not None and not r.run_task.done()
        )

    async def sustain(
        self,
        *,
        desired: int,
        prefix: str,
        respawn: Callable[[int], Awaitable[None]],
        should_run: Callable[[], bool],
        interval_s: float = 1.0,
    ) -> None:
        """Top-up loop: keep ``active_by_prefix(prefix)`` at ``desired`` by respawning.

        The background baton stage ghosts finish their wave and sink; the sustainer respawns the
        shortfall so the research/extraction/verify rosters stay populated + working for as long
        as the workforce is running. Respects both concurrency caps implicitly — a respawned ghost
        still queues behind the visible-workforce / live-session semaphores. Stops when
        ``should_run()`` returns False (the workforce was stopped)."""
        while should_run():
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                return
            if not should_run():
                return
            shortfall = desired - self.active_by_prefix(prefix)
            for i in range(max(0, shortfall)):
                with contextlib.suppress(Exception):
                    await respawn(i)

    async def join(self) -> None:
        """Wait for every in-flight ghost run to complete."""
        tasks = [r.run_task for r in self._records.values() if r.run_task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel every in-flight ghost run (server shutdown); on_end + release still fire."""
        for rec in self._records.values():
            if rec.run_task is not None and not rec.run_task.done():
                rec.run_task.cancel()
        await self.join()


__all__ = [
    "GhostPool",
    "GhostRecord",
    "GhostStatus",
    "attention_for_event",
]
