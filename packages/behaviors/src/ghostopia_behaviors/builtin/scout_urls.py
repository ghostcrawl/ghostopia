"""``ScoutUrls`` — a research/seed behavior.

Searches/seeds candidate urls through the full-primitive ``ctx.browser`` and emits
``task.spawned {kind:"extract", url}`` per discovered url. The Orchestrator routes
each spawned task to the section whose ``accepts`` includes its kind — a ScoutUrls ghost in a
``research`` section feeds an ``extraction`` section. Discovered urls are DATA; they are
SSRF-validated upstream before any fetch.

``on_tick`` kicks the search off ONCE as a non-blocking :class:`AsyncOp` and polls it across
ticks (the ``Behavior`` contract: ``on_tick`` MUST be non-blocking so a slow search never
stalls the executor tick loop, trips ``tick_deadline_ms``, or blocks the ghost's pause/abort
seams); once it completes it emits the spawned tasks and finishes.
"""

from __future__ import annotations

from enum import Enum, auto

from ghostopia_shared import EndReason, GhostEvent
from ghostopia_shared.search_filter import filter_visitable_urls
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.builtin._async_op import AsyncOp
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class ScoutUrlsParams(BaseModel):
    """Parameters for the ScoutUrls behavior."""

    query: str = Field(default="", description="Search query to seed candidate urls.")
    seeds: list[str] = Field(default_factory=list, description="Extra seed urls to spawn.")
    spawn_kind: str = Field(default="extract", description="Task kind the spawned tasks carry.")
    stage_section: str = Field(
        default="",
        description="Stage desk the ghost sits at (empty = its rostered section).",
    )


class _Step(Enum):
    WALKING = auto()
    SEARCH = auto()
    DONE = auto()


class ScoutUrls:
    """Walk a research desk → search/seed → emit ``task.spawned`` per candidate url."""

    name = "scout_urls"

    def __init__(self) -> None:
        self._params = ScoutUrlsParams()
        self._step = _Step.WALKING
        self._search_op: AsyncOp | None = None

    @property
    def is_done(self) -> bool:
        return self._step is _Step.DONE

    def _mission_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.mission_id if ctx.task is not None else None

    def _stage_section(self, ctx: BehaviorContext) -> str | None:
        """The stage desk this ghost works at — its OWN rostered section by default."""
        if self._params.stage_section:
            return self._params.stage_section
        return ctx.section.id if ctx.section is not None else None

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = ScoutUrlsParams.model_validate(ctx.task.params)
        ctx.ghost.set_overlay("work")
        # Walk to the OWN stage desk so a research ghost is VISIBLE at a research desk;
        # the search is gated on arrival so it never runs mid-walk.
        stage = self._stage_section(ctx)
        if stage is not None:
            ctx.ghost.walk_to_section_workstation(stage)
        self._step = _Step.WALKING

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        if self._step is _Step.WALKING:
            if ctx.ghost.at_workstation():
                ctx.ghost.face_browser()
                ctx.ghost.play_work()
                self._step = _Step.SEARCH
            return
        if self._step is not _Step.SEARCH:
            return
        urls: list[str] = list(self._params.seeds)
        if self._params.query:
            # Kick the search off ONCE as a non-blocking op and poll across ticks so a
            # slow search never stalls the tick loop / trips the tick deadline. The op is a
            # sibling task, so an overrunning tick's cancel never cancels it mid-flight.
            if self._search_op is None:
                self._search_op = AsyncOp(ctx.browser.search({"query": self._params.query}))
                return
            if not self._search_op.done():
                return
            op, self._search_op = self._search_op, None
            results = op.result()  # re-raises a search error → normal ERROR path
            # Drop ad / tracking redirect urls (DDG y.js / l/?, bing aclick, …) so scouted
            # candidates are real destination pages, not ad click-throughs.
            urls.extend(filter_visitable_urls([str(r.get("url")) for r in results if r.get("url")]))
        for url in urls:
            await ctx.emit_event(
                "task.spawned",
                {"kind": self._params.spawn_kind, "url": url, "mission_id": self._mission_id(ctx)},
            )
        ctx.ghost.play_success()
        self._step = _Step.DONE

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        if self._search_op is not None:
            self._search_op.cancel()
            self._search_op = None
        self._step = _Step.DONE


behaviors.register(
    "scout_urls",
    ScoutUrls,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Scout Urls",
        param_schema=ScoutUrlsParams,
        examples=[
            {"title": "seed a company list", "params": {"query": "top saas companies 2026"}}
        ],
        overlay="work",
    ),
)
