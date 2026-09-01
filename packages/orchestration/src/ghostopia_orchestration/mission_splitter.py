"""Mission → N task records tagged by kind (STAGE 6).

One mission ("find 500 SaaS companies, extract pricing") splits into many
:class:`~ghostopia_shared.types.Task` records, each TAGGED BY ``kind`` (``scout`` /
``extract`` / ``verify`` / …). The ``kind`` is what the section fan-out routes on
(``accepts``/``routes_to``) — never a per-ghost or per-site route.

The split is data-only: it produces persisted-ready ``Task`` records and assigns none of
them (the :class:`~ghostopia_orchestration.work_queue.WorkQueue` + section fan-out own
assignment). A mission that seeds a *query* (no explicit urls) becomes a single ``scout``
task whose behavior discovers urls and emits ``task.spawned`` — the WorkQueue re-routes
each spawned task to the accepting (extraction) section.
"""

from __future__ import annotations

from typing import Any, Literal

from ghostopia_shared.types import Task
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MissionRequest", "split_mission"]

AgentMode = Literal["deterministic", "llm"]
GcTarget = Literal["cloud", "selfhost"]


class MissionRequest(BaseModel):
    """The operator's mission submission — what the thin TS form sends (NAMES only, no key).

    A mission enters the world at a section whose role accepts ``entry_kind`` (e.g.
    ``scout`` → research, ``extract`` → extraction). It carries EITHER an explicit ``urls``
    list (one task per url) OR a ``query`` seed (a single ``scout`` task that discovers
    urls). ``agent_mode`` selects the per-mission brain (deterministic runner vs the real
    Anthropic provider); it rides on each task's ``params`` so the orchestrator picks
    the brain at dispatch. ``extra='forbid'`` rejects hallucinated fields (AI-safety).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    entry_kind: str = "extract"
    urls: list[str] = Field(default_factory=list)
    query: str | None = None
    agent_mode: AgentMode = "deterministic"
    behavior_hint: str | None = None
    gc_target: GcTarget | None = None
    extract_schema: dict[str, Any] | None = None


def split_mission(mission: MissionRequest) -> list[Task]:
    """Split ``mission`` into ``N`` :class:`Task` records tagged by ``kind``.

    * A mission with ``urls`` → one task per url, each ``kind = entry_kind`` (default
      ``extract``), carrying the url in ``target``/``inputs``.
    * A mission with only a ``query`` (a seed, or ``entry_kind == "scout"``) → a SINGLE
      ``scout`` task carrying the query; its behavior discovers urls and emits
      ``task.spawned`` which the WorkQueue re-routes to the extraction section.

    Every task carries ``agent_mode`` (+ any ``gc_target``/``extract_schema``) so dispatch
    picks the brain per-mission; none are assigned here.
    """
    target_base: dict[str, Any] = {}
    if mission.gc_target is not None:
        target_base["gc_target"] = mission.gc_target

    common_params: dict[str, Any] = {"agent_mode": mission.agent_mode}
    if mission.extract_schema is not None:
        common_params["extract_schema"] = mission.extract_schema

    # Seed mode: no explicit urls (or an explicit scout entry) → a single scout task that
    # discovers urls and fans out via task.spawned.
    if not mission.urls or mission.entry_kind == "scout":
        return [
            Task(
                id=f"{mission.id}-scout",
                kind="scout",
                mission_id=mission.id,
                behavior_hint=mission.behavior_hint,
                target=dict(target_base),
                params={**common_params, "query": mission.query},
                inputs={"query": mission.query} if mission.query else {},
            )
        ]

    tasks: list[Task] = []
    for i, url in enumerate(mission.urls):
        tasks.append(
            Task(
                id=f"{mission.id}-{i}",
                kind=mission.entry_kind,
                mission_id=mission.id,
                behavior_hint=mission.behavior_hint,
                target={**target_base, "url": url},
                params={**common_params, "url": url},
                inputs={"urls": [url]},
            )
        )
    return tasks
