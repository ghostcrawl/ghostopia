"""The provider-agnostic ``AgentProvider`` seam — the SINGLE agent abstraction.

An ``AgentProvider`` decides + drives what a ghost does for one task:
``run_task(task, provider, emit)`` calls the injected ``BrowserProvider`` and emits
normalized ``Envelope`` objects through the injected async ``emit``. The interface is
deliberately provider-agnostic — NO LLM vendor appears in the Protocol:

- the ``DeterministicRunner`` (this package) is the v0 scripted brain;
- a first LLM ``AgentProvider`` (default Anthropic/Claude) is delivered
  behind THIS SAME seam — the composition layer (via
  ``select_agent_provider``) chooses which one, never a behavior;
- the ``AgentBehavior`` ADAPTS any ``AgentProvider`` into the ONE ``Behavior``
  contract — ``run_task`` is exactly what ``AgentBehavior`` drives, so the emit shape here
  MUST stay stable.

There is no parallel decision abstraction. Keep this interface minimal and stable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from ghostopia_browser_provider import BrowserProvider
from ghostopia_shared import Envelope, Task

# The async sink the runner emits normalized envelopes into. The composition layer wires
# this to the EventBus (``bus.publish``); tests wire it to a list collector. Keeping the
# signature ``(Envelope) -> Awaitable[None]`` matches ``EventBus.publish`` directly.
Emit = Callable[[Envelope], Awaitable[None]]


@runtime_checkable
class AgentProvider(Protocol):
    """The one agent seam. An implementation drives a task through the browser provider
    and emits the normalized event stream — the world looks identical whichever brain
    (deterministic now, LLM later) runs the task."""

    async def run_task(self, task: Task, provider: BrowserProvider, emit: Emit) -> None: ...
