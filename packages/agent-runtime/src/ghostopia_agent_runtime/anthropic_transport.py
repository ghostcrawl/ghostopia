"""The injectable server-side Anthropic Messages transport.

The LLM ``AgentProvider`` calls Anthropic DIRECTLY, server-side, via ``httpx`` — NO SDK
dependency and, critically, NO key in the frontend. The key is read
ONLY from ``os.environ["ANTHROPIC_API_KEY"]`` (the same server-side confinement as the
GhostCrawl key) and is never logged.

``AnthropicTransport`` is a Protocol with two methods:

- ``next_action(context)`` — ask the model for the next in-run action; returns the raw
  decoded reply (a dict) which the provider validates against a FIXED ``AgentAction`` union;
- ``structured(schema, prompt)`` — bind the model to a JSON schema (tool-use / structured
  output) and return the raw structured object (used by ``author_task`` to emit a TaskSpec).

Both return RAW data — the caller decode-validates it (never eval'd). The transport is
injectable so tests use a scripted fake and never touch the network.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, runtime_checkable

import httpx

#: The default house Claude model. Overridable per construction; never leaks to the client.
DEFAULT_MODEL = "claude-sonnet-4-5"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOKENS = 1024


@runtime_checkable
class AnthropicTransport(Protocol):
    """The injectable LLM transport seam. Server-side only; returns RAW decoded data."""

    async def next_action(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the model's next action as a raw dict (validated by the caller)."""
        ...

    async def structured(self, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        """Return a structured object bound to ``schema`` (validated by the caller)."""


class _AnthropicHttpTransport:
    """A thin ``httpx``-based Anthropic Messages client (no SDK). Key held server-side."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # The key is retained privately and never logged / echoed back to any caller.
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._client = http_client or httpx.AsyncClient(timeout=60.0)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    async def next_action(self, context: dict[str, Any]) -> dict[str, Any]:
        """Ask the model for the next browser action, bound to the fixed action schema."""
        from ghostopia_agent_runtime.anthropic_agent_provider import agent_action_schema

        prompt = (
            "You are driving a real browser to accomplish a task. Given the current "
            "context, choose the SINGLE next action. Respond only via the tool.\n\n"
            f"Context: {json.dumps(context)}"
        )
        return await self.structured(agent_action_schema(), prompt)

    async def structured(self, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        """Bind the model to ``schema`` via tool-use and return the raw tool input."""
        body = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "tools": [
                {
                    "name": "emit",
                    "description": "Emit the structured result bound to the schema.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "emit"},
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = await self._client.post(
            ANTHROPIC_MESSAGES_URL, headers=self._headers(), json=body
        )
        resp.raise_for_status()
        data = resp.json()
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                out = block.get("input")
                if isinstance(out, dict):
                    return out
        # No tool_use block → return an empty dict; the caller decode-rejects it safely.
        return {}


def create_anthropic_transport(
    api_key: str | None = None,
    *,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AnthropicTransport:
    """Build a server-side Anthropic transport.

    ``api_key`` defaults to ``os.environ["ANTHROPIC_API_KEY"]`` — read ONLY here on the
    server; it never crosses to the frontend and is never logged. ``http_client`` is
    injectable for tests (no real network). Raises ``RuntimeError`` if no key is present.
    """
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the LLM AgentProvider requires a server-side "
            "Anthropic key (never delivered to the frontend)."
        )
    return _AnthropicHttpTransport(key, model=model, http_client=http_client)
