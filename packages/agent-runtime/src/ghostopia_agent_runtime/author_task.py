"""``author_task`` — the AI-authoring path (decode-only).

A natural-language goal + the LIVE behavior catalog (names + param_schema + examples, a
CLOSED set) → a structured model reply bound to the ``TaskSpec`` JSON schema → a validated
declarative ``TaskSpec`` composed of vetted behaviors. The reply is DATA:

    TaskSpec.model_validate()  (extra='forbid' — rejects hallucinated fields)
    → behavior exists in the catalog
    → behavior.param_schema.model_validate(params)  (extra='forbid')
    → DRY-RUN: every target URL passes the injected SSRF gate; concurrency ≤ cap
    → ok / spec   |   ok=False / errors     (never eval'd, never assigned here)

An invalid / hallucinated / unknown-behavior / over-cap spec is REJECTED, never executed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ghostopia_shared.task import TaskSpec
from pydantic import BaseModel, ValidationError

from ghostopia_agent_runtime.anthropic_transport import AnthropicTransport


@dataclass
class BehaviorSpec:
    """One vetted behavior the model may compose: a name, an optional Pydantic
    ``param_schema`` the authored ``params`` must satisfy, and illustrative ``examples``."""

    name: str
    param_schema: type[BaseModel] | None = None
    examples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AuthorCatalog:
    """The CLOSED set the model authors from: available behaviors + sections + targets."""

    behaviors: list[BehaviorSpec]
    sections: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)

    def behavior(self, name: str) -> BehaviorSpec | None:
        return next((b for b in self.behaviors if b.name == name), None)


@dataclass
class AuthorDeps:
    """Injected server-side dependencies: the LLM transport, an SSRF URL validator, and
    the dry-run concurrency cap (a stricter operational ceiling than the TaskSpec's 1..50)."""

    transport: AnthropicTransport
    ssrf_validator: Callable[[str], bool]
    concurrency_cap: int = 50


@dataclass
class AuthorResult:
    """The outcome. ``ok`` + ``spec`` on success; ``ok=False`` + ``errors`` on rejection."""

    ok: bool
    spec: TaskSpec | None = None
    errors: list[str] = field(default_factory=list)


def _build_prompt(goal: str, catalog: AuthorCatalog) -> str:
    """Give the model the EXACT closed set — behavior names + param_schema + examples —
    so it picks among safe building blocks rather than authoring code."""
    lines = [
        "Author a declarative TaskSpec that accomplishes the goal by composing ONLY the "
        "vetted behaviors below. Respond ONLY via the structured tool bound to the "
        "TaskSpec schema. Do not invent behaviors or fields.",
        f"\nGoal: {goal}\n",
        "Available behaviors (closed set):",
    ]
    for b in catalog.behaviors:
        schema = b.param_schema.model_json_schema() if b.param_schema else {}
        lines.append(
            f"- {b.name}: param_schema={schema} examples={b.examples}"
        )
    lines.append(f"\nAvailable sections: {catalog.sections}")
    lines.append(f"Available targets: {catalog.targets}")
    return "\n".join(lines)


async def author_task(
    goal: str, catalog: AuthorCatalog, deps: AuthorDeps
) -> AuthorResult:
    """Ask the model for a ``TaskSpec`` and validate it decode-only against the catalog.

    Returns ``AuthorResult(ok=True, spec=...)`` on a clean dry-run, or
    ``AuthorResult(ok=False, errors=[...])`` on any rejection. The model output is never
    eval'd and never assigned here.
    """
    prompt = _build_prompt(goal, catalog)
    raw = await deps.transport.structured(TaskSpec.model_json_schema(), prompt)

    # 1. parse against TaskSpec (extra='forbid' rejects hallucinated top-level keys)
    try:
        spec = TaskSpec.model_validate(raw)
    except ValidationError as exc:
        return AuthorResult(ok=False, errors=[f"TaskSpec validation failed: {exc}"])

    errors: list[str] = []

    # 2. the named behavior MUST exist in the closed catalog
    behavior = catalog.behavior(spec.behavior)
    if behavior is None:
        errors.append(
            f"unknown behavior '{spec.behavior}' — not in the vetted catalog "
            f"({[b.name for b in catalog.behaviors]})"
        )
        return AuthorResult(ok=False, errors=errors)

    # 3. params MUST satisfy the behavior's own param_schema (extra='forbid')
    if behavior.param_schema is not None:
        try:
            behavior.param_schema.model_validate(spec.params)
        except ValidationError as exc:
            errors.append(f"params fail behavior '{behavior.name}' param_schema: {exc}")

    # 4. DRY-RUN: every target URL passes the injected SSRF gate
    for url in spec.inputs.urls or []:
        if not deps.ssrf_validator(url):
            errors.append(f"target rejected by SSRF gate: {url}")

    # 5. DRY-RUN: concurrency within the operational cap
    if spec.concurrency > deps.concurrency_cap:
        errors.append(
            f"concurrency {spec.concurrency} exceeds cap {deps.concurrency_cap}"
        )

    if errors:
        return AuthorResult(ok=False, errors=errors)
    return AuthorResult(ok=True, spec=spec)
