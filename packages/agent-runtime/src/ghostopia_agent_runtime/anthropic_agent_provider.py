"""``AnthropicAgentProvider`` — the LLM brain, behind the ``AgentProvider`` seam.

A MINIMAL Anthropic-backed ``AgentProvider``: a bounded decide→act loop that asks the
injected transport for the next action, DECODES it against a FIXED ``AgentAction`` Pydantic
union, drives the injected ``BrowserProvider``, and emits the SAME normalized ``Envelope``
sequence the ``DeterministicRunner`` emits — so the world looks identical whichever brain
runs the mission, and the ``AgentBehavior`` wraps EITHER unchanged.

Safety:

- model output is DECODED against ``AgentAction`` (navigate | extract | scroll | done) →
  an allowlisted ``BrowserProvider`` call only — NEVER ``eval``'d, never an out-of-band op;
- the loop is bounded by ``max_steps``; an unparseable / oversized reply falls to a safe
  terminal (release the session + ``task.completed``) rather than running away.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

from ghostopia_browser_provider import BrowserProvider
from ghostopia_shared import EventType, GhostState, Task
from ghostopia_shared.envelope import serialize_envelope
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ghostopia_agent_runtime.agent_provider import Emit
from ghostopia_agent_runtime.anthropic_transport import AnthropicTransport

# --------------------------------------------------------------------------------------
# The FIXED action union the model output is decoded against (a closed allowlist).
# --------------------------------------------------------------------------------------


class _ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavigateAction(_ActionBase):
    action: Literal["navigate"]
    url: str


class ExtractAction(_ActionBase):
    """``scrape``/``extract`` — pull records off the current page."""

    action: Literal["extract"]
    extract_schema: dict[str, Any] | None = None


class ScrollAction(_ActionBase):
    action: Literal["scroll"]
    dx: float = 0.0
    dy: float = 0.0


class DoneAction(_ActionBase):
    action: Literal["done"]


AgentAction = Annotated[
    NavigateAction | ExtractAction | ScrollAction | DoneAction,
    Field(discriminator="action"),
]
_ACTION_ADAPTER: TypeAdapter[Any] = TypeAdapter(AgentAction)


def agent_action_schema() -> dict[str, Any]:
    """The JSON schema of the fixed ``AgentAction`` union — bound to the model as the
    structured-output tool so it can only pick from the closed action set."""
    return _ACTION_ADAPTER.json_schema()


class AnthropicAgentProvider:
    """The LLM ``AgentProvider`` (default Anthropic/Claude). ``kind = 'llm'``.

    Structurally fulfils the ``AgentProvider`` Protocol — ``run_task`` has the
    identical signature and emits the identical normalized sequence as ``DeterministicRunner``.
    """

    #: Advertised for the composition layer's ``select_agent_provider`` (Agent.kind).
    kind = "llm"

    def __init__(
        self,
        transport: AnthropicTransport,
        *,
        max_steps: int = 12,
        max_reply_bytes: int = 16_384,
    ) -> None:
        self._transport = transport
        self._max_steps = max_steps
        self._max_reply_bytes = max_reply_bytes

    def _decode(self, raw: object) -> Any | None:
        """Decode a raw model reply against ``AgentAction`` — returns ``None`` (safe
        terminal) if it is oversized, non-JSON-serializable, or fails validation."""
        try:
            if len(json.dumps(raw)) > self._max_reply_bytes:
                return None
        except (TypeError, ValueError):
            return None
        try:
            return _ACTION_ADAPTER.validate_python(raw)
        except ValidationError:
            return None

    async def run_task(self, task: Task, provider: BrowserProvider, emit: Emit) -> None:
        ghost_id = task.params.get("ghost_id")
        target_url = task.target.get("url", "")

        async def _emit(event_type: EventType, payload: dict[str, object]) -> None:
            await emit(
                serialize_envelope(
                    type=str(event_type),
                    ts=time.time(),
                    payload=payload,
                    ghost_id=ghost_id,
                )
            )

        # 1. task picked up
        await _emit(EventType.TASK_ASSIGNED, {"task_id": task.id, "ghost_id": ghost_id})

        # 2. open the real browser session for this ghost/task
        handle = await provider.open(target_url, profile=ghost_id)
        await _emit(
            EventType.BROWSER_SESSION_OPENED,
            {"session_id": handle.session_id, "target": handle.target},
        )

        current_url = target_url
        state = GhostState.OPENING_BROWSER

        # 3. bounded decide→act loop — decode-only, allowlisted actions only
        for _ in range(self._max_steps):
            context = {
                "task_id": task.id,
                "goal": task.params.get("goal"),
                "target_url": target_url,
                "current_url": current_url,
            }
            try:
                raw = await self._transport.next_action(context)
            except Exception:  # any transport failure → safe terminal
                break
            action = self._decode(raw)
            if action is None:
                break  # malformed / oversized reply → safe terminal

            if isinstance(action, NavigateAction):
                await _emit(
                    EventType.GHOST_STATUS_CHANGED,
                    {"from_state": str(state), "to_state": str(GhostState.NAVIGATING)},
                )
                state = GhostState.NAVIGATING
                await provider.nav.goto(action.url)
                await _emit(EventType.BROWSER_NAVIGATE, {"url": action.url})
                current_url = action.url

            elif isinstance(action, ExtractAction):
                await _emit(
                    EventType.GHOST_STATUS_CHANGED,
                    {"from_state": str(state), "to_state": str(GhostState.EXTRACTING)},
                )
                state = GhostState.EXTRACTING
                schema = action.extract_schema or task.params.get("extract_schema")
                result = await provider.scrape(handle, current_url, extract_schema=schema)
                for record in result.records:
                    await _emit(
                        EventType.RESULT_RECORD_EXTRACTED,
                        {
                            "task_id": task.id,
                            "mission_id": task.mission_id,
                            "url": current_url,
                            "record": record,
                        },
                    )

            elif isinstance(action, ScrollAction):
                state = GhostState.SCROLLING
                await provider.page.scroll(dx=action.dx, dy=action.dy)
                await _emit(
                    EventType.BROWSER_ACTION,
                    {"action": "scroll", "dx": action.dx, "dy": action.dy},
                )

            else:  # DoneAction
                break

        # 4. safe terminal — ALWAYS release the session and complete (bounded, decode-only)
        await provider.release()
        await _emit(EventType.TASK_COMPLETED, {"task_id": task.id, "mission_id": task.mission_id})
