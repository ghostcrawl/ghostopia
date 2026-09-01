"""ghostopia-agent-runtime — the provider-agnostic agent seam + both v0 brains.

``AgentProvider`` is the ONE agent abstraction: ``run_task(task, provider, emit)``.
Two fulfilments ship behind it — ``DeterministicRunner`` (scripted) and
``AnthropicAgentProvider`` (the LLM brain, default Anthropic/Claude) — selectable per
mission via ``select_agent_provider``. ``author_task`` is the AI-authoring path: the
model emits a validated declarative ``TaskSpec`` composed of vetted behaviors (decode-only,
never code). ``AgentBehavior`` wraps EITHER brain unchanged.
"""

from __future__ import annotations

from ghostopia_agent_runtime.agent_provider import AgentProvider, Emit
from ghostopia_agent_runtime.anthropic_agent_provider import (
    AgentAction,
    AnthropicAgentProvider,
    agent_action_schema,
)
from ghostopia_agent_runtime.anthropic_transport import (
    AnthropicTransport,
    create_anthropic_transport,
)
from ghostopia_agent_runtime.author_task import (
    AuthorCatalog,
    AuthorDeps,
    AuthorResult,
    BehaviorSpec,
    author_task,
)
from ghostopia_agent_runtime.deterministic_runner import DeterministicRunner
from ghostopia_agent_runtime.select_agent_provider import (
    AgentMode,
    SelectDeps,
    select_agent_provider,
)

__all__ = [
    "AgentAction",
    "AgentMode",
    "AgentProvider",
    "AnthropicAgentProvider",
    "AnthropicTransport",
    "AuthorCatalog",
    "AuthorDeps",
    "AuthorResult",
    "BehaviorSpec",
    "DeterministicRunner",
    "Emit",
    "SelectDeps",
    "agent_action_schema",
    "author_task",
    "create_anthropic_transport",
    "select_agent_provider",
]
