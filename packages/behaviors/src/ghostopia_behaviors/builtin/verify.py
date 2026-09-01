"""``Verify`` — a QA behavior.

Re-scrapes a sample url through the full-primitive ``ctx.browser`` and compares the result
against an expected field map, emitting ``result.verified {ok}`` and playing
``play_success`` on a match or ``play_error`` on a mismatch.

Every long GhostCrawl op (open session, re-scrape, release) is kicked off ONCE as a
non-blocking :class:`AsyncOp` and polled across ticks — the ``Behavior`` contract requires
``on_tick`` to be non-blocking so a slow session/scrape never stalls the executor tick loop,
trips ``tick_deadline_ms``, or blocks the ghost's pause/abort seams.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from ghostopia_shared import EndReason, GhostEvent
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.builtin._async_op import AsyncOp
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class VerifyParams(BaseModel):
    """Parameters for the Verify behavior."""

    url: str = Field(default="", description="Sample url to re-scrape.")
    expect: dict[str, Any] = Field(
        default_factory=dict, description="Expected field->value subset the re-scrape must match."
    )
    extract_schema: dict[str, Any] | None = Field(
        default=None, description="Schema to re-extract with (None = default record)."
    )
    repository_section: str = Field(
        default="",
        description=(
            "ORIGIN department the verified result is delivered to + tagged with (empty = the "
            "ghost's rostered section). NEVER the verify stage — keeps by-department grouping."
        ),
    )
    stage_section: str = Field(
        default="",
        description="Verify stage desk the ghost sits at (empty = its rostered section).",
    )


class _Step(Enum):
    WALKING = auto()
    OPEN = auto()
    VERIFY = auto()
    RELEASING = auto()
    DELIVER = auto()
    DELIVERING = auto()
    DONE = auto()


class Verify:
    """Walk a verify desk → re-scrape → deliver the verdict INTO the origin department."""

    name = "verify"

    def __init__(self) -> None:
        self._params = VerifyParams()
        self._step = _Step.WALKING
        self._handle: Any = None
        self._open_op: AsyncOp | None = None
        self._scrape_op: AsyncOp | None = None
        self._release_op: AsyncOp | None = None

    @property
    def is_done(self) -> bool:
        return self._step is _Step.DONE

    def _task_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.id if ctx.task is not None else None

    def _mission_id(self, ctx: BehaviorContext) -> str | None:
        return ctx.task.mission_id if ctx.task is not None else None

    def _ghost_id(self, ctx: BehaviorContext) -> str | None:
        if ctx.task is None:
            return None
        gid = ctx.task.params.get("ghost_id")
        return gid if isinstance(gid, str) else None

    def _department(self, ctx: BehaviorContext) -> str | None:
        """The ORIGIN department the verdict is delivered to (never the verify stage)."""
        if self._params.repository_section:
            return self._params.repository_section
        return ctx.section.id if ctx.section is not None else None

    def _stage_section(self, ctx: BehaviorContext) -> str | None:
        if self._params.stage_section:
            return self._params.stage_section
        return ctx.section.id if ctx.section is not None else None

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = VerifyParams.model_validate(ctx.task.params)
            if not self._params.url:
                url = ctx.task.target.get("url")
                if isinstance(url, str):
                    self._params.url = url
        ctx.ghost.set_overlay("work")
        # Walk to the verify desk so the verify section is VISIBLE on the map; the re-scrape
        # is gated on arrival.
        stage = self._stage_section(ctx)
        if stage is not None:
            ctx.ghost.walk_to_section_workstation(stage)
        self._step = _Step.WALKING

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        if self._step is _Step.WALKING:
            if ctx.ghost.at_workstation():
                ctx.ghost.face_browser()
                self._step = _Step.OPEN
            return

        if self._step is _Step.OPEN:
            # open the session as a non-blocking op polled across ticks.
            if self._open_op is None:
                self._open_op = AsyncOp(
                    ctx.browser.create_session(
                        self._params.url or "about:blank", profile_name=self._ghost_id(ctx)
                    )
                )
                return
            if not self._open_op.done():
                return
            op, self._open_op = self._open_op, None
            self._handle = op.result()  # re-raises an open error → normal ERROR path
            ctx.ghost.face_browser()
            self._step = _Step.VERIFY
            return

        if self._step is _Step.VERIFY:
            # re-scrape as a non-blocking op polled across ticks.
            if self._scrape_op is None:
                self._scrape_op = AsyncOp(
                    ctx.browser.scrape(
                        self._handle,
                        self._params.url,
                        extract_schema=self._params.extract_schema,
                    )
                )
                return
            if not self._scrape_op.done():
                return
            op, self._scrape_op = self._scrape_op, None
            result = op.result()  # re-raises a scrape error → normal ERROR path
            record = result.records[0] if result.records else {}
            ok = all(record.get(k) == v for k, v in self._params.expect.items())
            await ctx.emit_event(
                "result.verified",
                {
                    "task_id": self._task_id(ctx),
                    "section": self._department(ctx),
                    "url": self._params.url,
                    "ok": ok,
                },
            )
            if ok:
                ctx.ghost.play_success()
            else:
                ctx.ghost.play_error()
            self._step = _Step.RELEASING
            return

        if self._step is _Step.RELEASING:
            # release the session as a non-blocking op polled across ticks.
            if self._release_op is None:
                self._release_op = AsyncOp(ctx.browser.release())
                return
            if not self._release_op.done():
                return
            op, self._release_op = self._release_op, None
            op.result()  # re-raise a release error → normal ERROR path
            self._handle = None
            self._step = _Step.DELIVER
            return

        if self._step is _Step.DELIVER:
            # Carry the verdict INTO the ORIGIN department (a baton ghost delivers to a section
            # it is not rostered to) and tag the delivered result with that department.
            ctx.ghost.walk_to_section_drop(self._department(ctx))
            ctx.ghost.play_success()
            await ctx.emit_event(
                "result.delivered",
                {
                    "task_id": self._task_id(ctx),
                    "mission_id": self._mission_id(ctx),
                    "section": self._department(ctx),
                    "url": self._params.url,
                },
            )
            self._step = _Step.DELIVERING
            return

        if self._step is _Step.DELIVERING:
            if ctx.ghost.is_idle():
                ctx.ghost.wander()
                self._step = _Step.DONE
            return

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        for op in (self._open_op, self._scrape_op, self._release_op):
            if op is not None:
                op.cancel()
        self._open_op = self._scrape_op = self._release_op = None
        if self._handle is not None:
            await ctx.browser.release()
            self._handle = None
        self._step = _Step.DONE


behaviors.register(
    "verify",
    Verify,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Verify",
        param_schema=VerifyParams,
        examples=[
            {
                "title": "verify a scraped title",
                "params": {"url": "https://example.com", "expect": {"title": "Example Domain"}},
            }
        ],
        overlay="work",
    ),
)
