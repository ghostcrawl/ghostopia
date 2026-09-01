"""``WhiteboardDraw`` — the collaborative-drawing behavior.

Each ghost opens its OWN chromium session on a drawing site and draws its assigned strokes
with a real HELD mouse drag — ``mouse.down`` → N interpolated ``mouse.move`` → ``mouse.up``
over the CDP-WS relay. This is the forcing function that proves the primitive
layer is genuinely full-fidelity (not just scrape/extract): the step route (nav/click/type)
CANNOT express a held stroke.

Full-fidelity held drag is **chromium-only** (paid tier + ``cdp_passthrough_enabled``). On a
non-chromium / non-entitled session the behavior DEGRADES to discrete ``mouse.click`` at each
vertex AND emits an explicit ``browser.action {action:"draw_degraded"}`` signal — never a
silent no-op and never a blocking tick. ``on_tick`` draws at most ONE stroke per tick.

Reaches GhostCrawl ONLY through the full-primitive ``ctx.browser`` and the ghost ONLY through
``ctx.ghost`` — no ``ghostcrawl`` import, no secrets (capability-scoped).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from enum import Enum, auto
from typing import Any

from ghostopia_shared import Button, EndReason, GhostEvent, Point
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class WhiteboardDrawParams(BaseModel):
    """Parameters an author/AI supplies for a whiteboard-draw task.

    ``strokes`` is a list of strokes; each stroke is an ordered list of ``Point`` vertices the
    pen travels through while held down. ``step_px`` controls the interpolation granularity of
    the held glide (§1.3 ~8–16 px steps).
    """

    strokes: list[list[Point]] = Field(
        default_factory=list, description="Per-ghost strokes; each a list of pen-path vertices."
    )
    step_px: float = Field(
        default=12.0, gt=0.0, description="Interpolation step for the held glide (px)."
    )


class _Step(Enum):
    WALKING = auto()
    OPENING = auto()
    DRAWING = auto()
    DONE = auto()
    FINISHED = auto()


class WhiteboardDraw:
    """Draw each stroke as a HELD mouse drag on a chromium session; degrade elsewhere."""

    name = "whiteboard_draw"

    def __init__(self) -> None:
        self._params = WhiteboardDrawParams()
        self._step = _Step.WALKING
        self._handle: Any = None
        self._stroke_idx = 0
        self._degraded = False

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

    def _url(self, ctx: BehaviorContext) -> str:
        if ctx.task is not None:
            urls = ctx.task.inputs.get("urls")
            if isinstance(urls, list) and urls:
                return str(urls[0])
            target = ctx.task.target.get("url")
            if isinstance(target, str):
                return target
        return "about:blank"

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is not None:
            self._params = WhiteboardDrawParams.model_validate(ctx.task.params)
        ctx.ghost.set_overlay("work")
        ctx.ghost.walk_to_workstation()
        self._step = _Step.WALKING

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        step = self._step
        if step is _Step.WALKING:
            if ctx.ghost.at_workstation():
                self._step = _Step.OPENING
            return

        if step is _Step.OPENING:
            self._handle = await ctx.browser.create_session(
                self._url(ctx), profile_name=self._ghost_id(ctx)
            )
            ctx.ghost.face_browser()
            engine = (self._handle.engine or "").lower()
            self._degraded = engine != "chromium"
            await ctx.emit_event(
                "browser.session_opened",
                {
                    "session_id": self._handle.session_id,
                    "target": self._handle.target,
                    "engine": self._handle.engine,
                },
            )
            if self._degraded:
                # Explicit unsupported-draw signal (never a silent no-op): the relay held-drag
                # is chromium-only; this session falls back to discrete clicks.
                await ctx.emit_event(
                    "browser.action",
                    {"action": "draw_degraded", "reason": "engine_not_chromium", "engine": engine},
                )
            self._step = _Step.DRAWING
            return

        if step is _Step.DRAWING:
            if self._stroke_idx >= len(self._params.strokes):
                self._step = _Step.DONE
                return
            stroke = self._params.strokes[self._stroke_idx]
            self._stroke_idx += 1
            if stroke:
                if self._degraded:
                    await self._draw_degraded(ctx, stroke)
                else:
                    await self._draw_held(ctx, stroke)
                ctx.ghost.play_work()
                await ctx.emit_event(
                    "browser.action",
                    {
                        "action": "stroke",
                        "index": self._stroke_idx - 1,
                        "points": len(stroke),
                        "held": not self._degraded,
                    },
                )
            return

        if step is _Step.DONE:
            await ctx.browser.release()
            self._handle = None
            ctx.ghost.play_success()
            ctx.ghost.walk_home()
            await ctx.emit_event(
                "task.completed", {"task_id": self._task_id(ctx), "strokes": self._stroke_idx}
            )
            self._step = _Step.FINISHED
            return

    async def _draw_held(self, ctx: BehaviorContext, stroke: list[Point]) -> None:
        """A full-fidelity HELD stroke: press at the first vertex, glide through interpolated
        interior points while held, release at the last vertex (§1.3)."""
        mouse = ctx.browser.mouse
        await mouse.move(stroke[0])
        await mouse.down(Button.LEFT)
        prev = stroke[0]
        for vertex in stroke[1:]:
            for point in self._interpolate(prev, vertex):
                await mouse.move(point)
            prev = vertex
        await mouse.up(Button.LEFT)

    async def _draw_degraded(self, ctx: BehaviorContext, stroke: list[Point]) -> None:
        """Non-chromium fallback: a discrete click at each vertex (no held drag path)."""
        mouse = ctx.browser.mouse
        for vertex in stroke:
            await mouse.click(vertex, Button.LEFT)

    def _interpolate(self, a: Point, b: Point) -> Iterator[Point]:
        dist = math.hypot(b.x - a.x, b.y - a.y)
        steps = max(1, int(dist // self._params.step_px))
        for i in range(1, steps + 1):
            t = i / steps
            yield Point(x=a.x + (b.x - a.x) * t, y=a.y + (b.y - a.y) * t)

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        if self._handle is not None:
            await ctx.browser.release()
            self._handle = None
        if reason != "completed":
            ctx.ghost.walk_home()
        self._step = _Step.FINISHED


behaviors.register(
    "whiteboard_draw",
    WhiteboardDraw,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="Whiteboard Draw",
        param_schema=WhiteboardDrawParams,
        examples=[
            {
                "title": "draw a diagonal line",
                "params": {
                    "strokes": [[{"x": 120, "y": 200}, {"x": 300, "y": 260}]],
                    "step_px": 12.0,
                },
            }
        ],
        overlay="work",
    ),
)
