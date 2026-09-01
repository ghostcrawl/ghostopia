"""``SearchAndDetail`` — the keyless searcher department behavior (flagship path).

Fuses scout + navigate_and_extract into ONE behavior: ``ctx.browser.search(query)`` finds a
bounded list of result urls, then for EACH result it opens a session, navigates, and scrapes a
``{title, price}`` schema, emitting one ``result.scraped`` per page tagged to the CALLING
department (``ctx.section.id``). This lets a single "searcher" department both FIND and surface
its own priced detail list, instead of splitting across research/extraction sections.

SSRF is LOAD-BEARING here. Because this behavior visits off-host result urls INLINE
(it spawns NO child task, so the orchestrator's ``_child_task`` gate never fires on them), it
calls the SHARED ``validate_mission_url`` on EVERY result url BEFORE opening a session; any url
resolving to loopback/private/link-local/metadata raises ``SsrfBlockedError`` and is SKIPPED —
never opened, never navigated, never scraped. Mediated egress alone is explicitly INSUFFICIENT,
so this in-behavior gate is load-bearing.

``on_tick`` is NON-BLOCKING: at most one awaited browser op advances per tick (the READING
dwell is a pure timer). It touches GhostCrawl ONLY via ``ctx.browser`` — no ``ghostcrawl``
import, no secret; the only new import is the shared SSRF validator.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from ghostopia_shared import EndReason, GhostEvent
from ghostopia_shared.search_filter import filter_visitable_urls
from ghostopia_shared.ssrf import SsrfBlockedError, validate_mission_url
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.builtin._async_op import AsyncOp
from ghostopia_behaviors.builtin._session_retry import (
    MAX_SESSION_ATTEMPTS,
    retry_after_floor,
    session_backoff,
)
from ghostopia_behaviors.registry import BehaviorMeta, behaviors

_DEFAULT_SCHEMA: dict[str, Any] = {"title": "string", "price": "string"}


class SearchAndDetailParams(BaseModel):
    """Parameters an author/AI supplies for a SearchAndDetail task."""

    query: str = Field(default="", description="Search query the department runs.")
    engine: str = Field(
        default="duckduckgo",
        description=(
            "Keyless search engine the department queries. Defaults to duckduckgo, which "
            "has no persistent per-exit block wall (unlike google's /sorry/), so cold "
            "keyless queries return results far more reliably for the workforce. Authors may "
            "override per department."
        ),
    )
    extract_schema: dict[str, Any] | None = Field(
        default=None, description="Field->type map scraped per result (None = title+price)."
    )
    max_results: int = Field(default=5, ge=1, description="Max search results visited.")
    dwell_ms: float = Field(default=500.0, ge=0.0, description="Read dwell before extracting.")
    stateless: bool = Field(
        default=False,
        description=(
            "Scrape each result WITHOUT opening a live session (server-side ``client.scrape``). "
            "Same extracted data, but no per-tenant live-session slot is consumed — the workforce "
            "runs this way so looping department ghosts never churn/leak the small "
            "concurrent-live-session budget."
        ),
    )
    repository_section: str = Field(
        default="",
        description=(
            "ORIGIN department every scraped/delivered result is tagged to (empty = the ghost's "
            "rostered section). NEVER the working STAGE the ghost stands in. Mirrors "
            "pipeline_crawl/verify so by-department grouping stays correct across every stage role."
        ),
    )


class _Step(Enum):
    WALKING = auto()
    SEARCH = auto()
    SELECT = auto()
    OPENING = auto()
    OPENING_WAIT = auto()
    NAVIGATE = auto()
    READING = auto()
    EXTRACT = auto()
    DONE = auto()
    DELIVERING = auto()
    FINISHED = auto()


class SearchAndDetail:
    """Search a query → SSRF-validate → visit → scrape(title,price) → emit per department."""

    name = "search_and_detail"

    def __init__(self) -> None:
        self._params = SearchAndDetailParams()
        self._step = _Step.WALKING
        self._results: list[str] = []
        self._idx = 0
        self._handle: Any = None
        self._current: str | None = None
        self._dwell_remaining = 0.0
        self._records = 0
        # Bounded, retry_after-aware backoff for OPENING the session (a rate-limited
        # ``sessions.create`` at the plan's concurrency edge WAITS + retries, never dies).
        self._session_attempts = 0
        self._session_retry_remaining = 0.0
        # In-flight browser ops, started once and polled across ticks so a live op that runs
        # longer than the executor tick deadline is NEVER cancelled mid-flight (else the step
        # would never advance and no ``result.scraped`` would ever flow).
        self._search_op: AsyncOp | None = None
        self._open_op: AsyncOp | None = None
        self._nav_op: AsyncOp | None = None
        self._extract_op: AsyncOp | None = None
        self._release_op: AsyncOp | None = None
        # When the account is at its live-session cap (the bounded rate-limit retry is spent),
        # the department DEGRADES to sessionless stateless scraping — ``ctx.browser.scrape``
        # needs no live session — so it still delivers its finds instead of dying with no data.
        self._sessionless = False

    @property
    def is_done(self) -> bool:
        return self._step is _Step.FINISHED

    def _ghost_id(self, ctx: BehaviorContext) -> str | None:
        if ctx.task is None:
            return None
        gid = ctx.task.params.get("ghost_id")
        return gid if isinstance(gid, str) else None

    def _task_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.id if ctx.task is not None else None

    def _mission_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.mission_id if ctx.task is not None else None

    def _section_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.section.id if ctx.section is not None else None

    def _department(self, ctx: BehaviorContext) -> str | None:
        """The ORIGIN department a result is tagged to (never the working stage).

        Prefers the task's ``repository_section`` (the department that seeded a background relay
        ghost), falling back to the ghost's rostered section. Mirrors pipeline_crawl/verify so the
        Data Graveyard "by department" grouping is correct regardless of the stage the ghost sits.
        """
        if self._params.repository_section:
            return self._params.repository_section
        return self._section_id(ctx)

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = SearchAndDetailParams.model_validate(ctx.task.params)
        # Stateless departments never open a live session (no slot churn/leak) — the per-result
        # OPENING step short-circuits to NAVIGATE and the scrape runs server-side.
        self._sessionless = self._params.stateless
        ctx.ghost.set_overlay("work")
        ctx.ghost.walk_to_workstation()
        self._step = _Step.WALKING

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        step = self._step

        if step is _Step.WALKING:
            if ctx.ghost.at_workstation():
                self._step = _Step.SEARCH
            return

        if step is _Step.SEARCH:
            if self._search_op is None:
                self._search_op = AsyncOp(
                    ctx.browser.search(
                        {
                            "query": self._params.query,
                            "limit": self._params.max_results,
                            "engine": self._params.engine,
                        }
                    )
                )
                return
            if not self._search_op.done():
                return
            op, self._search_op = self._search_op, None
            results = op.result()
            # Keep only VISITABLE destinations — drop ad / tracking redirect urls (DDG
            # ``y.js``/``l/?``, bing ``aclick``, …) so the department scrapes real product
            # pages, not ad click-throughs. SSRF is still enforced per-candidate below.
            urls = filter_visitable_urls([str(r.get("url")) for r in results if r.get("url")])
            self._results = urls[: self._params.max_results]
            self._idx = 0
            self._step = _Step.SELECT
            return

        if step is _Step.SELECT:
            # Advance to the next SSRF-ALLOWED result url. Blocked urls are skipped here
            # (validation is synchronous — no awaited browser op), so a loopback/private/
            # metadata result is NEVER opened/navigated/scraped.
            self._current = None
            while self._idx < len(self._results):
                candidate = self._results[self._idx]
                self._idx += 1
                try:
                    validate_mission_url(candidate)
                except SsrfBlockedError:
                    continue  # skip — never fetched
                self._current = candidate
                break
            self._step = _Step.OPENING if self._current is not None else _Step.DONE
            return

        if step is _Step.OPENING:
            if self._handle is not None or self._sessionless:
                self._step = _Step.NAVIGATE
                return
            # Start the session-open ONCE, then poll it across ticks (a success advances to
            # NAVIGATE immediately, a retryable rate-limit WAITS + retries, any other error
            # re-raises); the op completes in the background even past a tick deadline.
            if self._open_op is None:
                self._open_op = AsyncOp(
                    ctx.browser.create_session(
                        self._current or "about:blank", profile_name=self._ghost_id(ctx)
                    )
                )
                return
            if not self._open_op.done():
                return
            op, self._open_op = self._open_op, None
            try:
                self._handle = op.result()
            except Exception as err:  # noqa: BLE001 - classified below; re-raised if terminal
                floor = retry_after_floor(err)
                if floor is None:
                    raise  # a non-retryable, real error → normal ERROR path
                self._session_attempts += 1
                if self._session_attempts >= MAX_SESSION_ATTEMPTS:
                    # Bounded rate-limit retry spent → the account is at its live-session cap;
                    # DEGRADE to sessionless stateless scraping (client.scrape needs no live
                    # session) so the department still delivers its priced finds. NOT a
                    # workaround: a plan at its live-session cap is a real population.
                    self._sessionless = True
                    self._handle = None
                    ctx.ghost.face_browser()
                    self._step = _Step.NAVIGATE
                    return
                delay = session_backoff(self._session_attempts, floor)
                self._session_retry_remaining = delay * 1000.0
                # surface-safe transient cooldown (curated "cooldown" tooltip); not blocking.
                await ctx.emit_event(
                    "task.retry",
                    {
                        "task_id": self._task_id(ctx),
                        "code": "rate_limited",
                        "retryable": True,
                        "retry_after": floor,
                    },
                )
                self._step = _Step.OPENING_WAIT
                return
            ctx.ghost.face_browser()
            await ctx.emit_event(
                "browser.session_opened",
                {"session_id": self._handle.session_id, "target": self._handle.target},
            )
            self._step = _Step.NAVIGATE
            return

        if step is _Step.OPENING_WAIT:
            # Non-blocking cool-down between session-open attempts (a pure timer across ticks,
            # so on_tick stays under the executor deadline — no in-tick sleep, no spin-hammer).
            self._session_retry_remaining -= dt_ms
            if self._session_retry_remaining <= 0.0:
                self._step = _Step.OPENING
            return

        if step is _Step.NAVIGATE:
            if self._sessionless:
                # No live session → the stateless scrape below navigates + extracts server-side.
                await ctx.emit_event("browser.navigate", {"url": self._current})
                ctx.ghost.play_work()
                self._dwell_remaining = self._params.dwell_ms
                self._step = _Step.READING
                return
            if self._nav_op is None:
                self._nav_op = AsyncOp(ctx.browser.nav.goto(self._current or ""))
                return
            if not self._nav_op.done():
                return
            op, self._nav_op = self._nav_op, None
            op.result()  # re-raise a navigation error → normal ERROR path
            await ctx.emit_event("browser.navigate", {"url": self._current})
            ctx.ghost.play_work()
            self._dwell_remaining = self._params.dwell_ms
            self._step = _Step.READING
            return

        if step is _Step.READING:
            self._dwell_remaining -= dt_ms
            if self._dwell_remaining <= 0.0:
                self._step = _Step.EXTRACT
            return

        if step is _Step.EXTRACT:
            if self._extract_op is None:
                schema = self._params.extract_schema or _DEFAULT_SCHEMA
                self._extract_op = AsyncOp(
                    ctx.browser.scrape(self._handle, self._current or "", extract_schema=schema)
                )
                return
            if not self._extract_op.done():
                return
            op, self._extract_op = self._extract_op, None
            result = op.result()
            for record in result.records:
                self._records += 1
                await ctx.emit_event(
                    "result.scraped",
                    {
                        "task_id": self._task_id(ctx),
                        "mission_id": self._mission_id(ctx),
                        "section": self._department(ctx),
                        "url": self._current,
                        "fields": record,
                    },
                )
            self._step = _Step.SELECT  # on to the next result
            return

        if step is _Step.DONE:
            # Deliver the finished result BACK to this department (a visible deliver beat)
            # rather than abandoning it to a grave: release, walk into the section, play the
            # success cue, and emit an internal deliver envelope with the record count.
            if self._handle is not None:
                if self._release_op is None:
                    self._release_op = AsyncOp(ctx.browser.release())
                    return
                if not self._release_op.done():
                    return
                op, self._release_op = self._release_op, None
                op.result()  # surface a release error rather than silently swallowing it
                self._handle = None
            ctx.ghost.walk_to_section_drop()
            ctx.ghost.play_success()
            await ctx.emit_event(
                "result.delivered",
                {
                    "task_id": self._task_id(ctx),
                    "mission_id": self._mission_id(ctx),
                    "section": self._department(ctx),
                    "records": self._records,
                },
            )
            self._step = _Step.DELIVERING
            return

        if step is _Step.DELIVERING:
            # Wait for the ghost to reach its department drop (mode "deliver" lands it idle),
            # then celebrate, walk home to rest, and complete.
            if ctx.ghost.is_idle():
                ctx.ghost.play_success()
                ctx.ghost.walk_home()
                await ctx.emit_event("task.completed", {"task_id": self._task_id(ctx)})
                self._step = _Step.FINISHED
            return

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        for op in (
            self._search_op,
            self._open_op,
            self._nav_op,
            self._extract_op,
            self._release_op,
        ):
            if op is not None:
                op.cancel()
        self._search_op = self._open_op = self._nav_op = None
        self._extract_op = self._release_op = None
        if self._handle is not None:
            await ctx.browser.release()
            self._handle = None
        if reason != "completed":
            ctx.ghost.walk_home()
        self._step = _Step.FINISHED


behaviors.register(
    "search_and_detail",
    SearchAndDetail,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Search & Detail",
        param_schema=SearchAndDetailParams,
        examples=[{"title": "spooky masks", "params": {"query": "spooky masks"}}],
        overlay="work",
    ),
)
