"""The documented ONE-FILE Behavior template.

COPY THIS FILE, rename the class + ``name``, fill in the lifecycle, adjust the
``param_schema``/``examples``, and drop the copy under
``src/ghostopia_behaviors/builtin/`` — the auto-discovery loader registers it on the
next import with ZERO edit to any renderer or core-loop file (proven
by ``tests/test_dx_property.py``).

A ``Behavior`` is the decision unit ONE ghost runs. It drives the visible ghost ONLY through
the narrow ``ctx.ghost`` (:class:`GhostHandle`) and reaches GhostCrawl ONLY through the
full-primitive ``ctx.browser`` (:class:`BrowserProvider`) — never the SDK, never a
secret. ``on_tick`` MUST be NON-BLOCKING: kick off at most one awaited op per tick and react
to its completion in ``on_event`` (never block the tick on a long GhostCrawl call).

This template self-registers as ``"template"`` (guarded against duplicate registration) so the
DX property test can import it as a live one-file example; a real behavior uses its own unique
name.
"""

from __future__ import annotations

from ghostopia_shared import EndReason, GhostEvent
from pydantic import BaseModel, Field

from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class TemplateParams(BaseModel):
    """The typed params an author/AI supplies for this behavior.

    This model IS the machine-readable contract the management UI and AI author against — the
    registry carries it in ``meta.param_schema``, and a ``TaskSpec.params`` is validated
    against it before the behavior ever runs (decode-only). Replace these fields.
    """

    message: str = Field(default="hello", description="An example string param.")
    dwell_ms: float = Field(default=500.0, ge=0.0, description="An example numeric param.")


class TemplateBehavior:
    """A minimal, copy-paste starting point implementing the full lifecycle.

    Rename this class and the ``name`` string, then implement each hook. Keep ``on_tick``
    non-blocking: advance ONE step (or await ONE browser op) per call.
    """

    #: Unique registry name. MUST match the string passed to ``behaviors.register`` below and
    #: (if this behavior is a section's default) the section's ``role`` in the map data.
    name = "template"

    def __init__(self) -> None:
        self._params = TemplateParams()
        self._done = False

    @property
    def is_done(self) -> bool:
        return self._done

    async def on_start(self, ctx: BehaviorContext) -> None:
        """Set up: parse params, walk to a workstation, seed internal state."""
        if ctx.task is not None:
            self._params = TemplateParams.model_validate(ctx.task.params)
        ctx.ghost.set_overlay("work")
        ctx.ghost.walk_to_workstation()

    async def on_tick(self, ctx: BehaviorContext, dt_ms: float) -> None:
        """Advance ONE step. NON-BLOCKING: at most one awaited browser op per tick."""
        if self._done:
            return
        # Example: say the message once, then finish. A real behavior would open a session
        # via ``ctx.browser`` and step through work here.
        ctx.ghost.say(self._params.message)
        await ctx.emit_event("task.progress", {"note": self._params.message})
        self._done = True

    async def on_event(self, ctx: BehaviorContext, event: GhostEvent) -> None:
        """React to a normalized op-completion event (e.g. a ``browser.error`` retry)."""
        return None

    async def on_end(self, ctx: BehaviorContext, reason: EndReason) -> None:
        """Tear down: release any session and walk the ghost home."""
        if reason != "completed":
            ctx.ghost.walk_home()
        self._done = True


# Self-register (guarded so re-import / other tests never raise a duplicate). A real authored
# behavior copies this ``register`` call with its own name + params + examples.
if "template" not in behaviors.names():
    behaviors.register(
        "template",
        TemplateBehavior,
        BehaviorMeta(
            kind="deterministic",
            needs=["browser"],
            label="Template Behavior",
            param_schema=TemplateParams,
            examples=[
                {"title": "say hi and finish", "params": {"message": "hi", "dwell_ms": 300.0}}
            ],
            overlay="work",
        ),
    )
