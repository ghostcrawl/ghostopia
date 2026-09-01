"""``AgentBehavior`` — the adapter that runs ANY ``AgentProvider`` behind ONE contract.

Deterministic runner AND LLM — same contract: the deterministic runner
and the LLM ``AgentProvider`` BOTH express as a Behavior through this
one adapter, so the world cannot tell which brain runs — identical ``ctx.browser``/
``ctx.ghost`` calls, identical normalized event stream.

``on_start`` launches the injected provider's ``run_task(task, ctx.browser, ctx.emit)`` as a
SINGLE in-flight decision pipeline; ``on_tick`` is a non-blocking guard that reacts when the
pipeline finishes (walk home + surface any error). The concrete provider is chosen by the
composition layer via ``select_agent_provider`` and injected here — NEVER inside
a behavior. The default registry factory wires the deterministic brain; the LLM brain is
injected by constructing ``AgentBehavior(llm_provider)`` directly.
"""

from __future__ import annotations

import asyncio

from ghostopia_agent_runtime import AgentProvider
from ghostopia_agent_runtime.deterministic_runner import DeterministicRunner
from ghostopia_shared import EndReason, GhostEvent
from pydantic import BaseModel

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class AgentBehaviorParams(BaseModel):
    """AgentBehavior carries no authoring params of its own — the wrapped provider decides
    at runtime (the LLM brain reads the task target/inputs). Kept for meta uniformity."""


class AgentBehavior:
    """Adapts an injected ``AgentProvider`` (deterministic OR llm) into the Behavior contract."""

    name = "agent"

    def __init__(self, agent: AgentProvider | None = None) -> None:
        # Default to the deterministic brain; the composition layer injects the LLM provider.
        self._agent: AgentProvider = agent if agent is not None else DeterministicRunner()
        self._pipeline: asyncio.Task[None] | None = None
        self._done = False

    @property
    def is_done(self) -> bool:
        return self._done

    async def on_start(self, ctx: BehaviorContext) -> None:
        if ctx.task is None:
            self._done = True
            return
        # Launch ONE decision pipeline; run_task emits the normalized sequence through the
        # SAME ctx.emit and drives the SAME ctx.browser — non-blocking (we don't await it).
        self._pipeline = asyncio.ensure_future(
            self._agent.run_task(ctx.task, ctx.browser, ctx.emit)
        )

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        if self._done:
            return
        # Yield to the loop so the in-flight decision pipeline can advance one step — we
        # never block the tick awaiting the whole pipeline (one decision in flight).
        await asyncio.sleep(0)
        pipeline = self._pipeline
        if pipeline is not None and pipeline.done():
            self._done = True
            ctx.ghost.walk_home()
            pipeline.result()  # surface any exception raised inside run_task

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        if self._pipeline is not None and not self._pipeline.done():
            self._pipeline.cancel()
        self._done = True


behaviors.register(
    "agent",
    AgentBehavior,
    BehaviorMeta(
        kind="llm",
        needs=["agent", "browser"],
        label="Agent (LLM or deterministic)",
        param_schema=AgentBehaviorParams,
        examples=[{"title": "let the agent decide", "params": {}}],
        overlay="work",
    ),
)
