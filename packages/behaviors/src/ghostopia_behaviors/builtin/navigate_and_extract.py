"""``NavigateAndExtract`` — the canonical dynamic behavior.

Walks a ghost to a workstation, opens a GhostCrawl session through the full-primitive
``ctx.browser``, and steps NAVIGATE → READING(dwell) → EXTRACT → NAVIGATE over a url queue,
emitting ``browser.session_opened`` / ``browser.navigate`` / ``result.scraped`` /
``task.progress`` and pushing discovered urls back onto the queue (bounded by ``max_pages``).
A bounded retry honors ``retry_after``. On completion it releases the session and walks the
ghost home.

``on_tick`` is NON-BLOCKING: exactly one awaited browser op advances per tick (the READING
dwell and WAIT_RETRY back-off are pure timers). It touches GhostCrawl ONLY via ``ctx.browser``
and the ghost ONLY via ``ctx.ghost`` — no ``ghostcrawl`` import, no secrets.
"""

from __future__ import annotations

import re
from enum import Enum, auto
from typing import Any
from urllib.parse import urljoin, urlsplit

from ghostopia_shared import EndReason, GhostEvent
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.builtin._async_op import AsyncOp
from ghostopia_behaviors.builtin._session_retry import (
    MAX_SESSION_ATTEMPTS,
    retry_after_floor,
    session_backoff,
)
from ghostopia_behaviors.registry import BehaviorMeta, behaviors

# Pagination chrome: a ``.../page-N.html`` segment or a ``?page=N`` query. Kept generic
# (no per-site literal) — it is a heuristic on url SHAPE, not a books.toscrape special case.
_PAGINATION_RE = re.compile(r"/page-\d+\.html?$|[?&]page=\d+", re.IGNORECASE)


def _keep_detail_url(current: str, candidate: str) -> bool:
    """Decide whether a discovered ``candidate`` url should be FOLLOWED from ``current``.

    This is the SECURITY gate for the behavior's inline discovered-url queue: the
    followed urls are NOT emitted as child tasks, so the orchestrator's SSRF gate never
    re-validates them. The rule below pins the crawl to the host of the seed ``target_url``
    (which the mission gate already SSRF-validated before dispatch) by dropping every
    OFF-HOST candidate — the crawl can therefore never leave that one already-validated
    public host.

    On top of that same-host constraint it applies a generic, per-site-free quality
    heuristic that keeps item/detail pages and drops obvious listing chrome:
      * a candidate equal to the current page is dropped (no self-loop);
      * an off-host candidate is dropped (same-host security constraint);
      * a pagination url (``.../page-N.html`` / ``?page=N``) is dropped;
      * a category/listing index (path contains ``/category/``) is dropped;
      * anything else on the same host is kept as a candidate detail page.

    Relative candidates are resolved against ``current`` first, so a relative link on the
    same page counts as same-host (kept).
    """
    if not candidate or candidate == current:
        return False
    resolved = urljoin(current, candidate)
    cand = urlsplit(resolved)
    cur = urlsplit(current)
    cand_host = (cand.hostname or "").lower()
    cur_host = (cur.hostname or "").lower()
    # SECURITY: same-host only. An empty/mismatched host is refused.
    if not cand_host or cand_host != cur_host:
        return False
    if _PAGINATION_RE.search(resolved):
        return False
    if "/category/" in cand.path.lower():
        return False
    return True


class NavigateAndExtractParams(BaseModel):
    """Parameters an author/AI supplies for a NavigateAndExtract task."""

    urls: list[str] = Field(default_factory=list, description="Seed urls to visit in order.")
    extract_schema: dict[str, Any] | None = Field(
        default=None, description="Field->type map to extract per page (None = default record)."
    )
    dwell_ms: float = Field(default=500.0, ge=0.0, description="Read dwell before extracting.")
    max_pages: int = Field(default=5, ge=1, description="Max pages visited (queue cap).")
    max_retries: int = Field(default=2, ge=0, description="Bounded retries on a browser error.")
    stateless: bool = Field(
        default=False,
        description=(
            "Scrape WITHOUT opening a live session (server-side ``client.scrape``). The extracted "
            "data is identical, but no per-tenant live-session slot is consumed — the workforce "
            "runs this way so N looping department ghosts never churn/leak the small "
            "concurrent-live-session budget (a live session is only worth opening for the frame "
            "inspector on a ghost the operator is actively watching)."
        ),
    )
    repository_section: str = Field(
        default="",
        description=(
            "ORIGIN department every scraped/delivered result is tagged to (empty = the ghost's "
            "rostered section). NEVER the working STAGE the ghost stands in — a background relay "
            "ghost sits at the research/extraction/verify desk but its finds belong to the "
            "department that seeded it. Mirrors pipeline_crawl/verify so by-department grouping "
            "stays correct across every stage role."
        ),
    )


class _Step(Enum):
    WALKING = auto()
    OPENING = auto()
    OPENING_WAIT = auto()
    NAVIGATE = auto()
    READING = auto()
    EXTRACT = auto()
    WAIT_RETRY = auto()
    DONE = auto()
    DELIVERING = auto()
    FINISHED = auto()


class NavigateAndExtract:
    """A stepwise navigate→read→extract crawl behavior over a bounded url queue."""

    name = "navigate_and_extract"

    def __init__(self) -> None:
        self._params = NavigateAndExtractParams()
        self._step = _Step.WALKING
        self._queue: list[str] = []
        #: the department's LISTING/category seed urls — a page in this set that links onward to
        #: detail pages is a listing, not a product, so its own scrape is NOT recorded (recording
        #: it would duplicate the real product cards with the listing's first item).
        self._seed_urls: set[str] = set()
        self._handle: Any = None
        self._current: str | None = None
        self._pages_started = 0
        self._records = 0
        self._retries = 0
        self._dwell_remaining = 0.0
        self._retry_remaining = 0.0
        # Bounded, retry_after-aware backoff for OPENING the session (a rate-limited
        # ``sessions.create`` at the plan's concurrency edge WAITS + retries, never dies).
        self._session_attempts = 0
        self._session_retry_remaining = 0.0
        # In-flight browser ops, started once and polled across ticks so a live op that runs
        # longer than the executor tick deadline is NEVER cancelled mid-flight (else the step
        # would never advance and no ``result.scraped`` would ever flow).
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
        ghost sitting at a stage desk), falling back to the ghost's rostered section for a
        directly-rostered department ghost / mission run. Mirrors pipeline_crawl/verify so the
        Data Graveyard "by department" grouping is correct no matter which stage the ghost is on.
        """
        if self._params.repository_section:
            return self._params.repository_section
        return self._section_id(ctx)

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = NavigateAndExtractParams.model_validate(ctx.task.params)
            self._queue = list(self._params.urls)
            seed = ctx.task.target.get("url")
            if isinstance(seed, str) and seed not in self._queue:
                self._queue.insert(0, seed)
            self._seed_urls = {u for u in self._queue if isinstance(u, str)}
        # Stateless departments never open a live session (no slot churn / leak); they scrape
        # server-side. The visual navigate/work beats still play, so the ghost looks identical.
        self._sessionless = self._params.stateless
        ctx.ghost.set_overlay("work")
        ctx.ghost.walk_to_workstation()
        self._step = _Step.WALKING

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        step = self._step
        if step is _Step.WALKING:
            if ctx.ghost.at_workstation():
                # Stateless departments skip the live-session open entirely (no slot consumed);
                # a session-backed run opens one for the inspector.
                if self._sessionless:
                    ctx.ghost.face_browser()
                    self._step = _Step.NAVIGATE
                else:
                    self._step = _Step.OPENING
            return

        if step is _Step.OPENING:
            # Start the session-open ONCE, then poll it across ticks: a successful open advances
            # to NAVIGATE immediately (next tick), a retryable rate-limit WAITS + retries, any
            # other error re-raises to the normal ERROR path. The op runs to completion in the
            # background even when it takes longer than one tick deadline.
            if self._open_op is None:
                self._open_op = AsyncOp(
                    ctx.browser.create_session(
                        self._queue[0] if self._queue else "about:blank",
                        profile_name=self._ghost_id(ctx),
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
                    # The bounded rate-limit retry is spent: the account is at its live-session
                    # cap, so DEGRADE to sessionless stateless scraping (client.scrape needs no
                    # live session) — the department still delivers its finds rather than dying
                    # with an empty graveyard. NOT a workaround: a plan at its live-session cap
                    # is a real population, and the crawl result is identical.
                    self._sessionless = True
                    self._handle = None
                    ctx.ghost.face_browser()
                    self._step = _Step.NAVIGATE
                    return
                delay = session_backoff(self._session_attempts, floor)
                self._session_retry_remaining = delay * 1000.0
                # surface-safe transient cooldown (task.retry → curated "cooldown" tooltip,
                # never a raw vendor/limit string); NOT a blocking operator alert.
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
            if self._nav_op is None:
                if not self._queue or self._pages_started >= self._params.max_pages:
                    self._step = _Step.DONE
                    return
                self._current = self._queue.pop(0)
                self._pages_started += 1
                if self._sessionless:
                    # No live session → no session-scoped live nav; the stateless scrape below
                    # navigates + extracts server-side. Still surface the visual navigate beat.
                    await ctx.emit_event("browser.navigate", {"url": self._current})
                    ctx.ghost.play_work()
                    self._dwell_remaining = self._params.dwell_ms
                    self._step = _Step.READING
                    return
                self._nav_op = AsyncOp(ctx.browser.nav.goto(self._current))
                return
            if not self._nav_op.done():
                return
            op, self._nav_op = self._nav_op, None
            op.result()  # re-raise a navigation error → normal ERROR / browser.error path
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
                self._extract_op = AsyncOp(
                    ctx.browser.scrape(
                        self._handle,
                        self._current or "",
                        extract_schema=self._params.extract_schema,
                    )
                )
                return
            if not self._extract_op.done():
                return
            op, self._extract_op = self._extract_op, None
            result = op.result()
            # Push discovered urls as DATA onto the queue (bounded by max_pages). The
            # same-host _keep_detail_url filter drops every off-host candidate, so
            # the crawl stays pinned to the already-SSRF-validated seed host, and drops
            # pagination/category chrome so it follows book-detail pages only.
            current = self._current or ""
            followable = [u for u in (result.discovered_urls or []) if _keep_detail_url(current, u)]
            # Queue the discovered product pages within the page budget.
            queued_any = False
            for url in followable:
                if len(self._queue) + self._pages_started < self._params.max_pages:
                    self._queue.append(url)
                    queued_any = True
            # A department's LISTING/category SEED that links onward to product pages is NOT itself
            # a product — recording it would duplicate the real product cards with the listing's
            # first item (the "same book twice" the Data Graveyard showed). Skip its own record
            # ONLY when we actually followed products from it; a seed with no products to follow
            # (a single-product target, or a budget with no room) is still recorded so data is
            # never lost.
            if not (current in self._seed_urls and queued_any):
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
            await ctx.emit_event(
                "task.progress",
                {"task_id": self._task_id(ctx), "pages_done": self._pages_started},
            )
            self._step = _Step.NAVIGATE
            return

        if step is _Step.WAIT_RETRY:
            self._retry_remaining -= dt_ms
            if self._retry_remaining <= 0.0:
                self._step = _Step.NAVIGATE
            return

        if step is _Step.DONE:
            # Deliver the finished result BACK to the ghost's department (a visible deliver beat)
            # instead of abandoning it to a grave: release the session, walk into the section,
            # play the success cue, and emit an internal deliver envelope with the record count.
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
        # A browser error mid-crawl: honor retry_after with a bounded back-off, requeueing
        # the current url at the front of the queue.
        if event.type == "browser.error" and self._retries < self._params.max_retries:
            self._retries += 1
            if self._current is not None:
                self._queue.insert(0, self._current)
                self._pages_started = max(0, self._pages_started - 1)
            retry_after = event.payload.get("retry_after", 0.0)
            self._retry_remaining = float(retry_after) * 1000.0
            ctx.ghost.play_error()
            self._step = _Step.WAIT_RETRY

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        for op in (self._open_op, self._nav_op, self._extract_op, self._release_op):
            if op is not None:
                op.cancel()
        self._open_op = self._nav_op = self._extract_op = self._release_op = None
        if self._handle is not None:
            await ctx.browser.release()
            self._handle = None
        if reason != "completed":
            ctx.ghost.walk_home()
        self._step = _Step.FINISHED


behaviors.register(
    "navigate_and_extract",
    NavigateAndExtract,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Navigate & Extract",
        param_schema=NavigateAndExtractParams,
        examples=[
            {
                "title": "crawl a docs site",
                "params": {
                    "urls": ["https://example.com/docs"],
                    "extract_schema": {"title": "str", "body": "str"},
                    "dwell_ms": 800.0,
                    "max_pages": 10,
                },
            }
        ],
        overlay="work",
    ),
)
