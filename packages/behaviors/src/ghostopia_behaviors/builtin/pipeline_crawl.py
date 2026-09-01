"""``PipelineCrawl`` — the FEATURED SOLO staged behavior.

ONE ghost walks the ENTIRE best-price flow solo, so the live inspector shows a single ghost
travel the graveyard's stage sections instead of teleporting into the terminal department:

    research desk → extraction desk → verify desk → deliver into ORIGIN department → wander home

Each desk transition is a real cross-section hop (``walk_to_section_workstation``) GATED on
``at_workstation()`` — the ghost is visibly AT a research desk while it searches, AT an
extraction desk while it scrapes, AT a verify desk while it compares — then it carries the
winning offer INTO its origin department (``walk_to_section_drop(repository_section)``) and
wanders off to rest. It reuses the session-open-retry + stateless-degrade of
``search_and_detail`` verbatim (a plan at its live-session cap DEGRADES to sessionless server
scraping — a real population, not a workaround), and touches GhostCrawl ONLY via
``ctx.browser``.

RESULT-TAGGING INVARIANT (highest-risk correctness item): every ``result.scraped`` /
``result.delivered`` tags ``section`` = the ORIGIN DEPARTMENT (``task.params
['repository_section']``, falling back to the ghost's rostered ``ctx.section.id``), NEVER the
working stage section the ghost happens to stand in. Otherwise the Data Graveyard "by
department" grouping breaks for a ghost that walks through research / extraction / verify.

SSRF is LOAD-BEARING: every candidate url is validated with the SHARED
``validate_mission_url`` BEFORE a session is opened / a scrape runs; a loopback / private /
link-local / metadata url is SKIPPED — never opened, never scraped.
"""

from __future__ import annotations

import re
from enum import Enum, auto
from typing import Any
from urllib.parse import urlsplit

from ghostopia_shared import EndReason, GhostEvent
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

# Hub-drilldown (session_extract real-retail): a retail category page is often a HUB — it lists
# subcategory tiles (title + link, NO price), not priced products. Extracting it yields no priced
# product. Rather than require the department to declare a brittle deep leaf URL, the pipeline
# follows a few of the hub's OWN same-host subcategory links until priced products appear. Bounded
# so a deep/looping site can never fan out unboundedly. Structure-only (no-price + same-host link →
# drill), never a per-site rule.
_DRILL_FANOUT = 3  # subcategory links followed per hub
_DRILL_BUDGET = 4  # total hub-drills per ghost (fans out ≤ _DRILL_FANOUT each)


# First grouped/decimal token in EITHER convention: US ``1,299.00`` or European ``1.299,00`` /
# ``9,99``. Must start AND end on a digit so a trailing sentence period is never absorbed.
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")


def _price_number(record: dict[str, Any]) -> float | None:
    """Best-effort numeric price from a record's ``price`` string (comparison ONLY).

    Tolerant of a leading currency symbol / code and of BOTH grouping conventions — US
    ``£1,299.00`` → ``1299.0`` AND European ``€9,99`` → ``9.99`` / ``1.299,00`` → ``1299.0``
    (comma is a decimal point in the European form, not always a thousands separator).
    Returns ``None`` when there is no parseable number, so a record with no usable price never
    wins the min-selection. This is a display-side comparison for the ``result.verified{best}``
    envelope; the authoritative min-price persistence happens on write in the DB layer
    — this behavior never imports it (the boundary), so the normalization is mirrored here."""
    raw = record.get("price")
    if not isinstance(raw, str):
        return None
    m = _NUM_RE.search(raw)
    if m is None:
        return None
    num = m.group(0)
    has_dot = "." in num
    has_comma = "," in num
    if has_dot and has_comma:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif has_comma:
        if num.count(",") == 1 and len(num.rsplit(",", 1)[1]) in (1, 2):
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def _same_host(a: str, b: str) -> bool:
    """True when two urls share a host (so a drill stays on the department's store)."""
    try:
        return (urlsplit(a).hostname or "").lower() == (urlsplit(b).hostname or "").lower()
    except ValueError:
        return False


def _drill_links(records: list[dict[str, Any]], base_url: str) -> list[str]:
    """Same-host subcategory links from a HUB page's records, to follow for priced products.

    A hub's extracted records carry a ``link`` per tile (the subcategory page) but no price. Return
    those http(s) links that are on the same host as the page and are not the page itself, in order,
    de-duplicated — the candidates a drill follows one level deeper. Structure-only, no per-site rule.
    """
    out: list[str] = []
    seen: set[str] = set()
    base_key = base_url.split("#", 1)[0].rstrip("/")
    for record in records:
        link = record.get("link") or record.get("url")
        if not isinstance(link, str):
            continue
        url = link.split("#", 1)[0].strip()
        if not url.startswith(("http://", "https://")):
            continue
        if url.rstrip("/") == base_key or url in seen:
            continue
        if base_url and not _same_host(url, base_url):
            continue
        seen.add(url)
        out.append(url)
    return out


class PipelineCrawlParams(BaseModel):
    """Parameters an author/AI supplies for a featured solo pipeline task."""

    query: str = Field(default="", description="Search query the research stage runs.")
    engine: str = Field(
        default="duckduckgo",
        description="Keyless search engine the research stage queries (duckduckgo default).",
    )
    target_url: str = Field(
        default="",
        description=(
            "The department's declared listing/category page. When set, the research stage "
            "DISCOVERS candidates from it (scrape → product links) instead of running a keyless "
            "web search — the deterministic path for a department that knows its own source."
        ),
    )
    seeds: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit candidate URLs. When non-empty they are used DIRECTLY as the candidate "
            "list (no discovery/search) — the most deterministic source of all."
        ),
    )
    extract_schema: dict[str, Any] | None = Field(
        default=None, description="Field->type map scraped per candidate (None = title+price)."
    )
    max_results: int = Field(default=5, ge=1, description="Max candidates carried downstream.")
    dwell_ms: float = Field(default=500.0, ge=0.0, description="Read dwell before extracting.")
    stateless: bool = Field(
        default=False,
        description=(
            "Scrape each candidate WITHOUT a live session (server-side ``client.scrape``) so a "
            "looping workforce ghost never churns the small concurrent-live-session budget."
        ),
    )
    session_extract: bool = Field(
        default=False,
        description=(
            "Extract from the page RENDERED in the live session (``scrape_rendered``) instead of a "
            "keyless re-fetch. Real protected retail blocks keyless scraping (CAPTCHA); the ghost's "
            "session already rendered the real page through the managed browser fleet, so read THAT. Set "
            "for advanced real-retail departments (masks/costumes); books stay keyless (faster)."
        ),
    )
    repository_section: str = Field(
        default="",
        description=(
            "The ORIGIN department every scraped/delivered result is tagged to (empty = the "
            "ghost's rostered section). NEVER the working stage — keeps by-department grouping."
        ),
    )
    research_section: str = Field(default="research", description="Research stage section id.")
    extraction_section: str = Field(
        default="extraction", description="Extraction stage section id."
    )
    verify_section: str = Field(default="verify", description="Verify stage section id.")


class _Step(Enum):
    WALK_RESEARCH = auto()
    AT_RESEARCH = auto()
    SEARCH = auto()
    WALK_EXTRACTION = auto()
    AT_EXTRACTION = auto()
    SELECT = auto()
    OPENING = auto()
    OPENING_WAIT = auto()
    OPEN_NAV = auto()
    NAVIGATE = auto()
    READING = auto()
    EXTRACT = auto()
    WALK_VERIFY = auto()
    AT_VERIFY = auto()
    COMPARE = auto()
    DELIVER = auto()
    DELIVERING = auto()
    WATCH_BROWSE = auto()
    RELEASE_WATCH = auto()
    FINISHED = auto()


class PipelineCrawl:
    """One ghost: research → extraction → verify → deliver-to-department → wander home."""

    name = "pipeline_crawl"

    def __init__(self) -> None:
        self._params = PipelineCrawlParams()
        self._step = _Step.WALK_RESEARCH
        self._results: list[str] = []
        self._idx = 0
        self._current: str | None = None
        self._handle: Any = None
        self._dwell_remaining = 0.0
        self._records: list[dict[str, Any]] = []
        self._sessionless = False
        # hub-drilldown budget + de-dup of urls already visited/queued (session_extract only).
        self._drill_budget = _DRILL_BUDGET
        self._seen_urls: set[str] = set()
        # -- P1 watched-hold: keep the session OPEN + re-browse candidates while the operator
        #    watches this ghost, so the live inspector shows a continuous, moving browser.
        self._watch_idx = 0
        self._watch_dwell = 0.0
        self._session_attempts = 0
        self._session_retry_remaining = 0.0
        self._search_op: AsyncOp | None = None
        self._open_op: AsyncOp | None = None
        self._nav_op: AsyncOp | None = None
        self._extract_op: AsyncOp | None = None
        self._release_op: AsyncOp | None = None

    @property
    def is_done(self) -> bool:
        return self._step is _Step.FINISHED

    # -- context helpers -------------------------------------------------------
    def _ghost_id(self, ctx: BehaviorContext) -> str | None:
        if ctx.task is None:
            return None
        gid = ctx.task.params.get("ghost_id")
        return gid if isinstance(gid, str) else None

    def _task_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.id if ctx.task is not None else None

    def _mission_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.mission_id if ctx.task is not None else None

    def _department(self, ctx: BehaviorContext) -> str | None:
        """The ORIGIN department a result is tagged to (never the working stage)."""
        if self._params.repository_section:
            return self._params.repository_section
        return ctx.section.id if ctx.section is not None else None

    @staticmethod
    def _is_watched(ctx: BehaviorContext) -> bool:
        """True when the operator is CURRENTLY watching this ghost in the live inspector (P1).

        Read through the capability-scoped ``ctx.watched`` predicate (a plain ``() -> bool``,
        no host reach). Tolerant of an older context that lacks it (→ never watched), so the
        behavior degrades to the prior ephemeral-session flow outside the operator app."""
        fn = getattr(ctx, "watched", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001 - a predicate fault must never break the crawl
                return False
        return False

    async def _extract_op_for(self, ctx: BehaviorContext, url: str) -> Any:
        """Get a scrape result for ``url`` — STRUCTURED product extraction (session_extract, the
        real-retail path) or a keyless server ``scrape`` (books, faster).

        For a real-retail (``session_extract``) department, prefer ``extract_products``: GhostCrawl's
        ``/v1/extract`` renders the listing through the managed fleet (solving the challenge) and
        returns the FULL priced product LIST — by DEFAULT via GhostCrawl's OWN native
        structured-data extractor (schema.org JSON-LD / microdata / Open Graph — no LLM, no third
        party), or the operator's connected model when one is set. This is a SERVER-SIDE call, so it
        does NOT need the ghost to hold a live session — the previous ``self._handle is not None``
        gate made a session-starved featured ghost fall back to a keyless re-fetch (which a protected
        store CAPTCHAs → a degraded single record). Calling ``extract_products`` regardless of the
        session gives the full grid whether or not a live session is held; it degrades to the
        rendered/keyless read INSIDE the provider only when the extract itself yields nothing.
        """
        schema = self._params.extract_schema or _DEFAULT_SCHEMA
        if self._params.session_extract:
            structured = getattr(ctx.browser, "extract_products", None)
            if callable(structured):
                return await structured(self._handle, url, schema)
            fn = getattr(ctx.browser, "scrape_rendered", None)
            if callable(fn):
                return await fn(self._handle, url)
        return await ctx.browser.scrape(self._handle, url, extract_schema=schema)

    def _scraped_payload(self, ctx: BehaviorContext, record: dict[str, Any]) -> dict[str, Any]:
        """The ``result.scraped`` envelope for one record, tagged to the ORIGIN department."""
        return {
            "task_id": self._task_id(ctx),
            "mission_id": self._mission_id(ctx),
            # ORIGIN department, NOT the extraction stage the ghost stands in.
            "section": self._department(ctx),
            "url": self._current,
            "fields": record,
        }

    async def _discover(self, ctx: BehaviorContext) -> list[str]:
        """Discover the candidate URLs the extraction stage will crawl (research stage).

        Preference order — the department's OWN declared source wins over a web search:
          1. ``seeds`` — explicit candidate URLs, used verbatim (most deterministic);
          2. ``target_url`` — the department's listing/category page: scrape it and expand its
             product links (``discovered_urls`` from the page's citations); a leaf page with no
             onward links falls back to the target itself;
          3. otherwise a keyless web ``search`` of the query (the search-department path).

        This is why a book department with a real ``target_url`` deterministically visits its
        actual product pages and compares real prices, instead of web-searching a junk label
        string that returns nothing/ads (the empty-department bug). No per-site logic — the same
        listing→detail expansion for every department that declares a source."""
        seeds = [str(u) for u in self._params.seeds if u]
        if seeds:
            return seeds
        target = self._params.target_url.strip()
        if target and self._params.session_extract:
            # A session_extract (real-retail) department reads the LISTING page ITSELF through
            # structured extraction — the connected model lifts the whole priced product list from
            # that one page — so the listing IS the candidate. Do NOT pre-scrape to expand its
            # links: on a real store those resolve to more category pages, never priced leaves, so
            # crawling them found no prices. The keyless book departments below still expand.
            return [target]
        if target:
            try:
                result = await self._extract_op_for(ctx, target)
            except Exception:  # noqa: BLE001 - a bad listing scrape → crawl the target itself
                return [target]
            found = [str(u) for u in (getattr(result, "discovered_urls", None) or []) if u]
            return found or [target]
        results = await ctx.browser.search(
            {
                "query": self._params.query,
                "limit": self._params.max_results,
                "engine": self._params.engine,
            }
        )
        return [str(r.get("url")) for r in results if r.get("url")]

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = PipelineCrawlParams.model_validate(ctx.task.params)
        self._sessionless = self._params.stateless
        ctx.ghost.set_overlay("work")
        ctx.ghost.walk_to_section_workstation(self._params.research_section)
        self._step = _Step.WALK_RESEARCH

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:  # noqa: C901,PLR0911,PLR0912,PLR0915
        step = self._step

        # ---- research desk --------------------------------------------------
        if step is _Step.WALK_RESEARCH:
            if ctx.ghost.at_workstation():
                ctx.ghost.face_browser()
                # P1/P3 fast live view: a SESSION-backed ghost opens its browser HERE (at the
                # research desk) and shows the department's target page BEFORE the slow keyless
                # discovery runs — so the operator sees a live browser within ~1-2s of selecting
                # it, not only after discovery + two desk walks. A stateless (background) ghost
                # never opens a session, so it discovers straight away.
                self._step = _Step.SEARCH if self._sessionless else _Step.OPENING
            return

        if step is _Step.SEARCH:
            if self._search_op is None:
                # DISCOVER candidates from the department's own declared source (seeds /
                # target_url listing) when it has one, else a keyless web search (P2).
                self._search_op = AsyncOp(self._discover(ctx))
                return
            if not self._search_op.done():
                return
            op, self._search_op = self._search_op, None
            urls = op.result()
            self._results = urls[: self._params.max_results]
            self._idx = 0
            ctx.ghost.play_success()
            ctx.ghost.walk_to_section_workstation(self._params.extraction_section)
            self._step = _Step.WALK_EXTRACTION
            return

        # ---- extraction desk ------------------------------------------------
        if step is _Step.WALK_EXTRACTION:
            if ctx.ghost.at_workstation():
                ctx.ghost.face_browser()
                self._step = _Step.SELECT
            return

        if step is _Step.SELECT:
            # advance to the next SSRF-ALLOWED candidate; blocked urls are never fetched.
            self._current = None
            while self._idx < len(self._results):
                candidate = self._results[self._idx]
                self._idx += 1
                try:
                    validate_mission_url(candidate)
                except SsrfBlockedError:
                    continue
                self._current = candidate
                break
            if self._current is None:
                ctx.ghost.walk_to_section_workstation(self._params.verify_section)
                self._step = _Step.WALK_VERIFY
            else:
                # The session was already opened at the research desk (EARLY open), so a candidate
                # goes straight to navigation — no per-candidate session open. A degraded/stateless
                # ghost has no handle and NAVIGATE takes its sessionless branch.
                self._step = _Step.NAVIGATE
            return

        if step is _Step.OPENING:
            # EARLY open (at the research desk) — one session, opened before discovery, reused
            # for every candidate. A ghost already holding a session (a re-run) skips straight to
            # discovery; a sessionless/degraded ghost never reaches this step.
            if self._handle is not None:
                self._step = _Step.OPEN_NAV
                return
            if self._open_op is None:
                self._open_op = AsyncOp(
                    ctx.browser.create_session(
                        self._params.target_url or "about:blank",
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
                    raise
                self._session_attempts += 1
                if self._session_attempts >= MAX_SESSION_ATTEMPTS:
                    # at the live-session cap → DEGRADE to sessionless server scraping so the
                    # department still delivers its finds (a real population, not a workaround).
                    self._sessionless = True
                    self._handle = None
                    ctx.ghost.face_browser()
                    self._step = _Step.SEARCH
                    return
                delay = session_backoff(self._session_attempts, floor)
                self._session_retry_remaining = delay * 1000.0
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
            self._step = _Step.OPEN_NAV
            return

        if step is _Step.OPENING_WAIT:
            self._session_retry_remaining -= dt_ms
            if self._session_retry_remaining <= 0.0:
                self._step = _Step.OPENING
            return

        if step is _Step.OPEN_NAV:
            # Show the department's target page in the live session IMMEDIATELY (before the slow
            # keyless discovery), so the operator's live view fills fast. No target / a blocked
            # target → straight to discovery (the session still renders, so frames still flow).
            target = self._params.target_url.strip()
            if not target or self._handle is None:
                self._step = _Step.SEARCH
                return
            try:
                validate_mission_url(target)
            except SsrfBlockedError:
                self._step = _Step.SEARCH
                return
            if self._nav_op is None:
                self._nav_op = AsyncOp(ctx.browser.nav.goto(target))
                return
            if not self._nav_op.done():
                return
            op, self._nav_op = self._nav_op, None
            try:
                op.result()
            except Exception:  # noqa: BLE001 - a target-preview nav hiccup never blocks discovery
                pass
            await ctx.emit_event("browser.navigate", {"url": target})
            ctx.ghost.play_work()
            self._step = _Step.SEARCH
            return

        if step is _Step.NAVIGATE:
            if self._sessionless:
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
                # session_extract reads the CAPTCHA-solved page the ghost just navigated (real
                # retail); the default keyless path re-fetches server-side (books, faster).
                self._extract_op = AsyncOp(self._extract_op_for(ctx, self._current or ""))
                return
            if not self._extract_op.done():
                return
            op, self._extract_op = self._extract_op, None
            result = op.result()
            records = list(result.records)
            if self._current:
                self._seen_urls.add(self._current)
            # session_extract (real-retail): a page is either a priced-product grid or a category
            # HUB (tiles with links, no prices). Emit only PRICED products; when a page yields none
            # but has same-host subcategory links, DRILL — queue a few of those links so the crawl
            # descends to the priced leaves instead of recording unpriced hub tiles as "products".
            if self._params.session_extract:
                priced = [r for r in records if _price_number(r) is not None]
                if priced:
                    for record in priced:
                        self._records.append(record)
                        await ctx.emit_event("result.scraped", self._scraped_payload(ctx, record))
                elif self._drill_budget > 0:
                    added = 0
                    for url in _drill_links(records, self._current or ""):
                        if added >= _DRILL_FANOUT:
                            break
                        if url in self._seen_urls or url in self._results:
                            continue
                        self._results.append(url)  # SELECT will pick it up (SSRF-validated there)
                        added += 1
                    if added:
                        self._drill_budget -= 1
            else:
                for record in records:
                    self._records.append(record)
                    await ctx.emit_event("result.scraped", self._scraped_payload(ctx, record))
            self._step = _Step.SELECT  # on to the next candidate
            return

        # ---- verify desk ----------------------------------------------------
        if step is _Step.WALK_VERIFY:
            # P1 watched-hold: while the operator is watching this ghost, KEEP the live session
            # open through verify/deliver (and into WATCH_BROWSE) so the inspector shows a
            # continuous browser instead of "waking a browser…". An unwatched ghost releases here
            # exactly as before, so it never churns the small live-session budget.
            if self._handle is not None and not self._is_watched(ctx):
                if self._release_op is None:
                    self._release_op = AsyncOp(ctx.browser.release())
                    return
                if not self._release_op.done():
                    return
                op, self._release_op = self._release_op, None
                op.result()
                self._handle = None
            if ctx.ghost.at_workstation():
                ctx.ghost.face_browser()
                self._step = _Step.COMPARE
            return

        if step is _Step.COMPARE:
            # pick the MINIMUM-price record as "best" (display-side; DB owns persistence).
            best: dict[str, Any] | None = None
            best_num: float | None = None
            for record in self._records:
                num = _price_number(record)
                if num is None:
                    if best is None:
                        best = record
                    continue
                if best_num is None or num < best_num:
                    best, best_num = record, num
            await ctx.emit_event(
                "result.verified",
                {
                    "task_id": self._task_id(ctx),
                    "mission_id": self._mission_id(ctx),
                    "section": self._department(ctx),
                    "best": best,
                    "candidates": len(self._records),
                },
            )
            ctx.ghost.play_success()
            self._step = _Step.DELIVER
            return

        # ---- deliver into the origin department + rest ----------------------
        if step is _Step.DELIVER:
            ctx.ghost.walk_to_section_drop(self._department(ctx))
            ctx.ghost.play_success()
            await ctx.emit_event(
                "result.delivered",
                {
                    "task_id": self._task_id(ctx),
                    "mission_id": self._mission_id(ctx),
                    "section": self._department(ctx),
                    "records": len(self._records),
                },
            )
            self._step = _Step.DELIVERING
            return

        if step is _Step.DELIVERING:
            if ctx.ghost.is_idle():
                ctx.ghost.play_success()
                # P1: if the operator is watching this ghost AND its live session is still open,
                # keep browsing the finds for as long as they watch (WATCH_BROWSE) so the live
                # inspector shows a continuous, moving browser. Otherwise finish normally.
                if self._is_watched(ctx) and self._handle is not None and self._results:
                    self._watch_idx = 0
                    self._watch_dwell = 0.0
                    self._step = _Step.WATCH_BROWSE
                else:
                    ctx.ghost.wander()
                    await ctx.emit_event("task.completed", {"task_id": self._task_id(ctx)})
                    self._step = _Step.FINISHED
            return

        # ---- watched-hold: keep the live session moving while the operator watches ----------
        if step is _Step.WATCH_BROWSE:
            # Re-navigate the finds one at a time with a read dwell so the inspector shows the
            # ghost actively browsing its results. The moment the operator stops watching (or the
            # session drops) → release the session + finish (RELEASE_WATCH). The data was already
            # delivered on the first pass, so this loop re-emits NO result/verified envelopes —
            # it is purely the live view staying alive.
            if not self._is_watched(ctx) or self._handle is None:
                self._step = _Step.RELEASE_WATCH
                return
            if self._nav_op is not None:
                if not self._nav_op.done():
                    return
                op, self._nav_op = self._nav_op, None
                try:
                    op.result()
                except Exception:  # noqa: BLE001 - a nav hiccup mid-watch never ends the stream
                    pass
                await ctx.emit_event("browser.navigate", {"url": self._current})
                self._watch_dwell = self._params.dwell_ms
                return
            if self._watch_dwell > 0.0:
                self._watch_dwell -= dt_ms
                return
            self._current = self._results[self._watch_idx % len(self._results)]
            self._watch_idx += 1
            ctx.ghost.face_browser()
            self._nav_op = AsyncOp(ctx.browser.nav.goto(self._current or ""))
            return

        if step is _Step.RELEASE_WATCH:
            if self._handle is not None:
                if self._release_op is None:
                    self._release_op = AsyncOp(ctx.browser.release())
                    return
                if not self._release_op.done():
                    return
                op, self._release_op = self._release_op, None
                try:
                    op.result()
                except Exception:  # noqa: BLE001 - release best-effort on watch exit
                    pass
                self._handle = None
            ctx.ghost.wander()
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
    "pipeline_crawl",
    PipelineCrawl,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Pipeline Crawl",
        param_schema=PipelineCrawlParams,
        examples=[
            {
                "title": "best price across editions",
                "params": {"query": "spooky masks", "repository_section": "horror-books"},
            }
        ],
        overlay="work",
    ),
)
