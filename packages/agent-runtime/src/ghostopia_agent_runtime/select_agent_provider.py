"""``select_agent_provider(mode)`` — the per-mission brain factory.

The composition layer (orchestrator) picks a brain PER MISSION without
knowing the concrete type: both returned values satisfy the ONE ``AgentProvider``
Protocol, so the call site is provider-agnostic and the ``AgentBehavior`` wraps
either. The 'llm' branch is the REAL ``AnthropicAgentProvider`` wired to a transport built
from server-side config (injected for tests). An unknown mode RAISES — no silent fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from ghostopia_agent_runtime.agent_provider import AgentProvider
from ghostopia_agent_runtime.anthropic_agent_provider import AnthropicAgentProvider
from ghostopia_agent_runtime.anthropic_transport import (
    AnthropicTransport,
    create_anthropic_transport,
)
from ghostopia_agent_runtime.deterministic_runner import DeterministicRunner

AgentMode = Literal["deterministic", "llm"]


@dataclass
class SelectDeps:
    """Injected wiring for the factory. ``transport`` lets tests (and callers holding a
    pre-built transport) supply the LLM transport directly; when absent, the 'llm' branch
    builds one from the server-side ``ANTHROPIC_API_KEY`` (never the frontend)."""

    transport: AnthropicTransport | None = None
    max_steps: int = 12


def select_agent_provider(
    mode: AgentMode, deps: SelectDeps | None = None
) -> AgentProvider:
    """Return the ``AgentProvider`` for ``mode``.

    - ``'deterministic'`` → a ``DeterministicRunner`` (scripted v0 brain);
    - ``'llm'`` → an ``AnthropicAgentProvider`` (default Anthropic/Claude) wired to
      ``deps.transport`` or, if none, a transport built from the server-side Anthropic key.

    Raises ``ValueError`` on an unknown mode (no silent fallback).
    """
    deps = deps or SelectDeps()
    if mode == "deterministic":
        return DeterministicRunner()
    if mode == "llm":
        transport = deps.transport or create_anthropic_transport()
        return AnthropicAgentProvider(transport, max_steps=deps.max_steps)
    raise ValueError(
        f"unknown agent mode {mode!r} — expected one of {list(get_args(AgentMode))}"
    )
