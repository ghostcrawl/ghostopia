"""Server orchestrator — mission fan-out across sections/ghosts (STAGE 6 milestone).

The front door to the bounded execution engine: an authed
``mission.submit`` is SSRF-validated, :func:`~ghostopia_orchestration.split_mission`-split
into ``N`` kind-tagged tasks, and fanned out to sections BY ROLE (``route_task``),
each task assigned to a free roster ghost. Every task's real run flows back through the
:class:`~ghostopia_orchestration.WorkQueue` (backoff on retryable / fail on non-retryable),
emitting ``task.retry`` / ``browser.error`` / ``task.completed`` — the SDK error map drives
the ghost error/retry visuals.

Each mission runs on the operator-selected brain — the deterministic runner OR the real
Anthropic ``AgentProvider`` — via :func:`~ghostopia_agent_runtime.select_agent_provider`
(``mission.agent_mode``), composed with the section role's behavior
(:func:`~ghostopia_sections.resolve_behavior`); both brains drive the SAME normalized stream
through :func:`~ghostopia_server.gc_event_source.run_real_task`.

Security: targets are SSRF-validated before dispatch; a scout's discovered
urls (``task.spawned``) are re-validated on their own dispatch; the Anthropic/GhostCrawl keys
never cross the WS — the form sends MODE/section NAMES + urls only. The queue is
the SINGLE choke point — no per-ghost ad-hoc SDK call bypasses it (governor safety).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ghostopia_agent_runtime import SelectDeps, select_agent_provider
from ghostopia_ghostcrawl_provider import MappedError, TargetRegistry
from ghostopia_orchestration import (
    DispatchResult,
    MissionRequest,
    WorkQueue,
    split_mission,
)
from ghostopia_sections import RouteResult, Section, load_default_sections, resolve_behavior
from ghostopia_shared import Envelope, Ghost, Task
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_shared.ssrf import SsrfBlockedError as SharedSsrfBlockedError
from ghostopia_shared.ssrf import validate_mission_url as shared_validate_mission_url

from .config import DEFAULT_TARGET, build_target_registry
from .frame_fanout import SessionRegistry
from .gc_event_source import ProviderFactory, run_real_task
from .ghost_pool import GhostPool
from .ssrf import SsrfBlockedError, validate_mission_url
from .ws_gateway import WsGateway

__all__ = [
    "ONBOARDING_UNCONFIGURED_REASON",
    "Orchestrator",
    "make_pool_stage_dispatch",
]


def _spawn_id(parent_id: str, validated: str) -> str:
    """Deterministic, collision-resistant child-task id for a discovered url.

    The salted builtin ``hash()`` is non-reproducible across process restarts/tests AND
    collides in a 1M space — and ``WorkQueue._attempts`` + the SQLite result rows are keyed on
    ``task.id``, so a collision cross-links retry accounting / results between two unrelated
    child tasks. A wide stable digest (matching ``gc_event_source._grave_tile``'s sha1 spread)
    fixes both."""
    digest = hashlib.sha1(validated.encode("utf-8")).hexdigest()[:12]
    return f"{parent_id}-spawn-{digest}"


def _stage_child_task(parent: Task, env: Envelope) -> Task | None:
    """Build the next-stage baton task from a walking stage ghost's ``task.spawned``.

    The scout stage emits ``task.spawned{kind:extract,url}`` per discovered candidate; this
    turns each into a child task the WorkQueue routes to the accepting (extraction) section —
    where the Option-A bridge spawns ANOTHER walking pool stage ghost. The child re-validates
    the discovered url (SSRF) and CARRIES the parent's ``repository_section`` +
    ``stateless`` so every downstream result stays tagged to the ORIGIN department and no baton
    ghost ever opens a live session on the stateless background path."""
    payload = env.payload if isinstance(env.payload, dict) else {}
    url = payload.get("url")
    if not url:
        return None
    try:
        validated = shared_validate_mission_url(str(url))
    except SharedSsrfBlockedError:
        return None
    kind = str(payload.get("kind") or "extract")
    inherited: dict[str, Any] = {
        k: parent.params[k]
        for k in ("repository_section", "stateless", "extract_schema", "dwell_ms")
        if k in parent.params
    }
    return Task(
        id=_spawn_id(parent.id, validated),
        kind=kind,
        mission_id=parent.mission_id,
        target={"url": validated},
        params={**inherited, "url": validated, "urls": [validated]},
        inputs={"urls": [validated]},
    )


def make_pool_stage_dispatch(
    pool: Any,
    sections: list[Section],
    broadcast: Callable[[Envelope], Awaitable[None]],
    *,
    id_prefix: str = "stage",
    palette: tuple[int, ...] = (0x7AD7FF, 0xFFB347, 0x8BE04A, 0xFF5AA8),
) -> Callable[[Task, RouteResult], Awaitable[DispatchResult]]:
    """The Option-A baton dispatch bridge (PIPELINE-DESIGN): route_task → a WALKING
    pool stage ghost, NOT a static ``register_external``.

    For each routed task the WorkQueue hands us, we ``pool.spawn`` a real walking ``stage-*``
    ghost that runs the routed SECTION'S role behavior against the task — so the ghost visibly
    travels to that stage's desk and works it (the intermediate research/extraction sections
    finally receive ghosts). We capture the ghost's ``task.spawned`` envelopes (teeing them into
    the returned ``spawned`` children) so the WorkQueue hops the baton to the next stage's
    section — reusing WorkQueue/route_task/routes_to UNCHANGED. The finished stage ghost is
    despawned (sinks + frees its seat). Every emitted envelope is still broadcast to the world."""
    by_id = {s.id: s for s in sections}
    seq = itertools.count()

    async def dispatch(task: Task, route: RouteResult) -> DispatchResult:
        section = by_id.get(route.section or "")
        if section is None:
            return DispatchResult(ok=True, spawned=[])
        gid = f"{id_prefix}-{route.section}-{next(seq)}"
        children: list[Task] = []

        async def capture(env: Envelope) -> None:
            if env.type == "task.spawned":
                child = _stage_child_task(task, env)
                if child is not None:
                    children.append(child)
            await broadcast(env)

        # deterministic across restarts/tests: salted hash() would repaint the same
        # stage ghost a different color every process.
        color = palette[int(hashlib.sha1(gid.encode("utf-8")).hexdigest(), 16) % len(palette)]
        try:
            rec = await pool.spawn(
                ghost_id=gid,
                name=_ghost_name(gid),
                section=section,
                behavior_name=section.role,
                task=task,
                color=color,
                on_emit=capture,
            )
        except ValueError:
            # a duplicate id race — the same baton hop already has a walking ghost.
            return DispatchResult(ok=True, spawned=children)
        if rec.run_task is not None:
            with contextlib.suppress(Exception):
                await rec.run_task
        # the stage ghost finished its hop → despawn so it sinks + releases its seat, then let
        # the next-stage children (if any) route to their accepting section's walking ghost.
        with contextlib.suppress(Exception):
            if await pool.despawn(gid):
                await broadcast(
                    serialize_envelope(
                        type="ghost.despawned", ts=time.time(),
                        ghost_id=gid, payload={"ghost_id": gid},
                    )
                )
        return DispatchResult(ok=True, spawned=children)

    return dispatch

#: Customer-safe onboarding copy shown when a mission is submitted before GhostCrawl is
#: configured (no ``GHOSTOPIA_GC_TOKEN``). On-brand + surface-safe: it names "GhostCrawl key
#: and endpoint" and never leaks a token ref / stacktrace / internal word.
ONBOARDING_UNCONFIGURED_REASON = (
    "Connect your GhostCrawl key and endpoint to summon the workforce."
)

#: The default number of worker roster ghosts seeded per fan-out section.
_ROSTER_PER_SECTION = 4


class Orchestrator:
    """Receives ``mission.submit`` and fans it out across sections/ghosts via the WorkQueue."""

    def __init__(
        self,
        gateway: WsGateway,
        *,
        sections: list[Section] | None = None,
        provider_factory: ProviderFactory | None = None,
        registry: TargetRegistry | None = None,
        select_deps: SelectDeps | None = None,
        session_registry: SessionRegistry | None = None,
        pool: GhostPool | None = None,
        max_concurrent: int = 5,
        roster_per_section: int = _ROSTER_PER_SECTION,
    ) -> None:
        self._gateway = gateway
        self._sections = sections if sections is not None else load_default_sections()
        self._by_id = {s.id: s for s in self._sections}
        self._provider_factory = provider_factory
        self._registry = registry
        self._select_deps = select_deps
        self._session_registry = session_registry
        # The ONE authoritative ghost registry: every fan-out ghost is registered here
        # so the management surface (ghost.manage) reaches a mission-spawned ghost BY ID, not
        # only ambient pool ghosts. None keeps the legacy behavior (tests that don't manage).
        self._pool = pool
        self._max_concurrent = max_concurrent
        self._roster_per_section = roster_per_section
        self._tasks: set[asyncio.Task[None]] = set()

    def _effective_max_concurrent(self) -> int:
        """The dispatch concurrency cap for a mission's WorkQueue.

        When a :class:`GhostPool` is wired, its entitlement-derived cap is the ONE source of
        truth — every mission's WorkQueue follows it, so a mission can never exceed the
        tier/self-host concurrency the pool enforces (no drifting second number). The ctor
        ``max_concurrent`` is only a fallback for the legacy poolless tests."""
        if self._pool is not None:
            return self._pool.max_concurrent
        return self._max_concurrent

    # -- runtime section/department CRUD ------------------------------
    # Today the sections load ONCE at ``__init__``; these give the operator a runtime path
    # (add / edit / remove a department while the world is live) that the JWT-gated
    # ``section.save`` / ``section.remove`` verbs drive through the SectionEditor. The list is
    # mutated IN PLACE so the SAME list the pool + catalog relay alias stays consistent.

    def upsert_section(self, defn: Any) -> Section:
        """Add a new department, or REPLACE an existing one by id.

        ``defn`` is a :class:`~ghostopia_sections.section.SectionDef` (a new :class:`Section`
        is built) OR a ready :class:`Section` (used as-is). On REPLACE of an existing id, the
        prior section's live roster + working set are carried forward onto the replacement so
        mission-spawned ghosts are never orphaned. ``_sections`` is mutated in place (its list
        identity is preserved) and ``_by_id`` is rebuilt. Returns the live section.
        """
        section = defn if isinstance(defn, Section) else Section(defn)
        section_id = section.id
        prior = self._by_id.get(section_id)
        if prior is not None:
            # replace: carry the live roster/working/queue forward (unless the caller handed a
            # pre-populated Section of its own).
            if not isinstance(defn, Section):
                section.roster = prior.roster
                section.working = prior.working
                section.queue = prior.queue
            idx = self._sections.index(prior)
            self._sections[idx] = section
        else:
            self._sections.append(section)
        self._by_id = {s.id: s for s in self._sections}
        return section

    def remove_section(self, section_id: str) -> bool:
        """Remove a department by id; ``True`` if it existed, ``False`` (no-op) otherwise.

        The built-in pipeline sections are only ever dropped by EXPLICIT id — a missing id
        changes nothing. ``_sections`` is mutated in place; ``_by_id`` is rebuilt.
        """
        prior = self._by_id.get(section_id)
        if prior is None:
            return False
        self._sections.remove(prior)
        self._by_id = {s.id: s for s in self._sections}
        return True

    # -- install ---------------------------------------------------------------------

    def install(self) -> None:
        """Register the authed ``mission.submit`` control verb (overrides the stage-3 one)."""
        self._gateway.register_control("mission.submit", self._on_submit)

    def _get_registry(self) -> TargetRegistry | None:
        if self._provider_factory is not None:
            return None  # a mock factory supplies the provider; no registry needed.
        if self._registry is None:
            self._registry = build_target_registry()
        return self._registry

    # -- inbound ---------------------------------------------------------------------

    async def _on_submit(self, envelope: Envelope) -> None:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        # ONBOARDING GUARD: the real workforce runs on REAL GhostCrawl. When no key is
        # configured (real provider path + unset GHOSTOPIA_GC_TOKEN), reject the mission with a
        # single customer-safe onboarding message instead of letting the lazy registry build
        # raise a token-ref ValueError. A configured run (token set) or a mock provider_factory
        # (tests) skips this and keeps its normal path; the SSRF gate below is unaffected.
        if self._provider_factory is None and not os.environ.get("GHOSTOPIA_GC_TOKEN"):
            await self._reject(ONBOARDING_UNCONFIGURED_REASON)
            return
        urls = payload.get("urls")
        query = payload.get("query")
        if (isinstance(urls, list) and urls) or query:
            fut = asyncio.ensure_future(self._run_mission(payload))
        else:
            # legacy stage-3 single session (target_name + url) — unchanged behavior.
            url = str(payload.get("url", ""))
            target = str(payload.get("target_name") or DEFAULT_TARGET)
            fut = asyncio.ensure_future(self._run_single(url, target))
        self._tasks.add(fut)
        fut.add_done_callback(self._tasks.discard)

    async def _run_single(self, url: str, target: str) -> None:
        try:
            await run_real_task(
                url,
                target,
                self._gateway.broadcast,
                registry=self._get_registry(),
                provider_factory=self._provider_factory,
                session_registry=self._session_registry,
            )
        except SsrfBlockedError as err:
            await self._reject(str(err))

    async def _run_mission(self, payload: dict[str, Any]) -> None:
        """Split + fan out a multi-target mission across sections through the WorkQueue."""
        entry_section = str(payload.get("entry_section") or "horror-books")
        section = self._by_id.get(entry_section)
        entry_kind = section.accepts[0] if section and section.accepts else "extract"

        # SSRF-validate EVERY seed target before dispatch.
        raw_urls = payload.get("urls") or []
        validated: list[str] = []
        blocked = False
        for u in raw_urls:
            try:
                validated.append(validate_mission_url(str(u), ()))
            except SsrfBlockedError as err:
                blocked = True
                await self._reject(f"blocked target {u!r}: {err}")

        # ONE explicit policy — fail the WHOLE mission if ANY seed is blocked (fail
        # -closed). Do NOT run a partial mission that mixes a rejected SSRF target with a
        # silently-running mission for the remaining seeds (a confusing "blocked" message
        # alongside a mission that runs anyway). An SSRF attempt in a batch fails the batch.
        if blocked:
            return
        # Nothing to run: no valid seeds AND no free-text query → an empty mission, so return
        # rather than fall through to split_mission's silent empty no-op.
        if not validated and not payload.get("query"):
            await self._reject("mission has no runnable target")
            return

        mission = MissionRequest(
            id=f"mission-{int(time.time() * 1000)}",
            title=str(payload.get("title") or "mission"),
            entry_kind=entry_kind,
            urls=validated,
            query=str(query) if (query := payload.get("query")) else None,
            agent_mode=payload.get("agent_mode") or "deterministic",
        )
        tasks = split_mission(mission)
        if not tasks:
            return

        # STAGE-7: announce the mission so the result store persists its title + task total
        # (the Data Graveyard groups completed results by mission, dashboard shows progress).
        await self._gateway.broadcast(
            serialize_envelope(
                type="mission.created",
                ts=time.time(),
                payload={
                    "mission_id": mission.id,
                    "title": mission.title,
                    "total": len(tasks),
                },
            )
        )

        self._seed_rosters(mission.id)
        queue = WorkQueue(
            self._sections,
            self._make_dispatch(),
            max_concurrent=self._effective_max_concurrent(),
        )
        queue.enqueue_all(tasks, from_section=section)
        # surface remaining quota so a mission can't silently exhaust it.
        await self._surface_quota()
        await queue.run()

    # -- dispatch: run one task on the selected brain, through run_real_task ----------

    def make_dispatch(self) -> Any:
        """Public accessor for the per-task dispatch (composed over).

        The Task/mission management API (:mod:`ghostopia_server.task_routes`) builds its own
        bounded :class:`~ghostopia_orchestration.WorkQueue` over the SAME dispatch — each task
        runs on the selected brain through :func:`run_real_task` (SSRF-revalidated, section
        role composed, one normalized stream), so the management surface never opens an ad-hoc
        SDK session or bypasses the queue."""
        return self._make_dispatch()

    def _make_dispatch(self) -> Any:
        async def dispatch(task: Task, route: RouteResult) -> DispatchResult:
            section = self._by_id.get(route.section or "")
            ghost_id = route.ghost_id or f"{route.section}-w0"
            ghost = Ghost(
                id=ghost_id,
                name=_ghost_name(ghost_id),
                home_grave="grave-1",
                section=route.section,
            )
            # compose the brain with the section role's behavior (precedence-resolved +
            # registry-validated; a bad role/hint fails fast rather than silently idling).
            behavior_name = resolve_behavior(ghost, section, task)
            mode = task.params.get("agent_mode", "deterministic")
            runner = select_agent_provider(mode, self._select_deps)

            # unification: register this fan-out ghost in the authoritative pool registry
            # so ghost.manage (pause/resume/cancel/retarget/behavior-override) reaches it by id.
            # A behavior_override pinned via management wins over the resolved role behavior.
            rec = None
            if self._pool is not None and section is not None:
                rec = self._pool.register_external(
                    ghost_id=ghost_id,
                    name=_ghost_name(ghost_id),
                    behavior_name=behavior_name,
                    section=section,
                )
                if rec.behavior_override is not None:
                    behavior_name = rec.behavior_override
                rec.abort_event = asyncio.Event()  # a fresh abort seam per dispatch
                rec.behavior_name = behavior_name
                rec.state = "WORKING"

            # Dispatch fallback: a task that carries no url of its own scrapes the
            # department's own target_url. When the url is SOURCED FROM THE SECTION (the task
            # carried none) it MUST pass the SAME SSRF gate before any session opens — a
            # section-supplied target is a fetch target too (never bypass).
            task_url = task.target.get("url") or task.params.get("url") or ""
            section_url = section.defn.target_url if section else None
            url = task_url or section_url or ""
            if not task_url and section_url:
                try:
                    url = validate_mission_url(url, ())
                except SsrfBlockedError:
                    # a blocked department target fails the task cleanly (terminal,
                    # non-retryable) — no GhostCrawl session opens.
                    return DispatchResult(
                        ok=False,
                        error=MappedError(
                            code="ssrf_blocked",
                            visual="error",
                            event_type="browser.error",
                            retryable=False,
                            retry_after=None,
                        ),
                        spawned=[],
                    )

            target = task.target.get("gc_target") or (
                section.gc_target if section else None
            ) or DEFAULT_TARGET

            # surface the composition (brain × section role) on the stream so the visuals
            # reflect WHICH brain/behavior drives this ghost (both brains emit identically).
            # Carries mission_id + url so the STAGE-7 result store persists the task row with
            # its section/behavior/url before any record arrives.
            await self._gateway.broadcast(
                serialize_envelope(
                    type="task.started",
                    ts=time.time(),
                    ghost_id=ghost_id,
                    payload={
                        "task_id": task.id,
                        "mission_id": task.mission_id,
                        "kind": task.kind,
                        "section": route.section,
                        "behavior": behavior_name,
                        "agent_mode": mode,
                        "url": url,
                    },
                )
            )

            # capture a scout's discovered urls (task.spawned) → child extract tasks the
            # queue re-routes to the accepting section (SSRF re-validated on their dispatch).
            children: list[Task] = []

            async def capture(env: Envelope) -> None:
                if env.type == "task.spawned":
                    child = self._child_task(task, env)
                    if child is not None:
                        children.append(child)
                await self._gateway.broadcast(env)

            mapped = await run_real_task(
                url,
                str(target),
                capture,
                registry=self._get_registry(),
                provider_factory=self._provider_factory,
                runner=runner,
                ghost_id=ghost_id,
                ghost_name=_ghost_name(ghost_id),
                # Carry the fan-out route's REAL section into the positioned
                # ghost.spawned so the world sprite matches the roster row (no divergence).
                section=route.section or (section.id if section else "horror-books"),
                # Fall back to the department's own extract_schema when the task
                # carries none (its target + its schema define what it brings back).
                extract_schema=task.params.get("extract_schema")
                or (section.defn.extract_schema if section else None),
                session_registry=self._session_registry,
                mission_id=task.mission_id,
                task_id=task.id,
                # management seams: a paused ghost holds before opening a session; a cancel
                # signals the abort event → the in-flight run stops + releases.
                is_paused=(lambda: bool(rec.paused)) if rec is not None else None,
                abort=rec.abort_event if rec is not None else None,
            )
            if rec is not None and rec.abort_event.is_set():
                # a management cancel aborted this run — report success (terminal, no retry).
                rec.state = "CANCELLED"
                return DispatchResult(ok=True, spawned=children)
            if mapped is None:
                return DispatchResult(ok=True, spawned=children)
            return DispatchResult(ok=False, error=mapped, spawned=children)

        return dispatch

    def _child_task(self, parent: Task, env: Envelope) -> Task | None:
        payload = env.payload if isinstance(env.payload, dict) else {}
        url = payload.get("url")
        if not url:
            return None
        try:
            validated = validate_mission_url(str(url), ())  # re-validate discovered urls.
        except SsrfBlockedError:
            return None
        kind = str(payload.get("kind") or "extract")
        return Task(
            id=_spawn_id(parent.id, validated),
            kind=kind,
            mission_id=parent.mission_id,
            target={"url": validated},
            params={"url": validated, "agent_mode": parent.params.get("agent_mode", "deterministic")},
            inputs={"urls": [validated]},
        )

    # -- roster seeding + quota + rejection ------------------------------------------

    def _seed_rosters(self, mission_id: str) -> None:
        """Seed each fan-out section with free roster ghosts the queue assigns work to."""
        for section in self._sections:
            if not section.accepts:
                continue
            for i in range(self._roster_per_section):
                section.add_ghost(f"{section.id}-w{i}")

    async def _surface_quota(self) -> None:
        """Broadcast remaining wallet/quota (me()/usage()) so a mission can't silently
        exhaust it. Best-effort: a missing/erroring provider is non-fatal."""
        registry = self._registry
        if registry is None:
            return
        try:
            client = registry.client_for(DEFAULT_TARGET)
            me = await _maybe_await(getattr(client, "me", None))
            usage = await _maybe_await(getattr(client, "usage", None))
        except Exception:
            return
        await self._gateway.broadcast(
            serialize_envelope(
                type="result.mission_progress",
                ts=time.time(),
                payload={"quota": {"me": _safe(me), "usage": _safe(usage)}},
            )
        )

    async def _reject(self, reason: str) -> None:
        await self._gateway.broadcast(
            serialize_envelope(type="error.rejected", ts=time.time(), payload={"reason": reason})
        )


def _ghost_name(ghost_id: str) -> str:
    return ghost_id.replace("-", " ").title()


async def _maybe_await(fn: Any) -> Any:
    if fn is None:
        return None
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


def _safe(value: Any) -> Any:
    """Coerce an SDK response to a JSON-safe primitive (best-effort, never raises)."""
    if value is None or isinstance(value, (str, int, float, bool, dict, list)):
        return value
    return str(value)
