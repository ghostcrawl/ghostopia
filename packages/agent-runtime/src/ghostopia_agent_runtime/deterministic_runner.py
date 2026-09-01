"""``DeterministicRunner`` — the v0 scripted ``AgentProvider``.

A deterministic navigate→scrape→store pipeline driven ENTIRELY through the
``BrowserProvider`` Protocol (works with ``FakeBrowserProvider`` now and
``GhostCrawlProvider`` later — it never imports the SDK). It emits the normalized
``Envelope`` sequence the GhostDriver maps to visual behavior:

    task.assigned
    → browser.session_opened
    → ghost.status_changed(→ NAVIGATING)  → browser.navigate
    → ghost.status_changed(→ EXTRACTING)
    → result.record_extracted (per record)
    → task.completed

The same sequence is produced regardless of the concrete provider, so the
``AgentBehavior`` wraps this unchanged and the LLM provider slots in behind the
same seam with an identical emit shape.
"""

from __future__ import annotations

import time

from ghostopia_browser_provider import BrowserProvider
from ghostopia_shared import EventType, GhostState, Task
from ghostopia_shared.envelope import serialize_envelope

from ghostopia_agent_runtime.agent_provider import Emit


class DeterministicRunner:
    """A scripted, provider-agnostic ``AgentProvider`` (fulfils the seam structurally)."""

    #: Advertised for the composition layer's ``select_agent_provider`` (Agent.kind).
    kind = "deterministic"

    async def run_task(self, task: Task, provider: BrowserProvider, emit: Emit) -> None:
        ghost_id = task.params.get("ghost_id")
        url = task.target.get("url", "")
        extract_schema = task.params.get("extract_schema")
        # STAGE-7: carry the mission linkage on every result/lifecycle envelope so the server
        # result store (ghostopia_server.results) persists each real record against its mission
        # + url with no cross-envelope bookkeeping (REAL-NOT-MOCK).
        mission_id = task.mission_id

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
        await _emit(
            EventType.TASK_ASSIGNED,
            {"task_id": task.id, "ghost_id": ghost_id, "mission_id": mission_id},
        )

        # 2. open the real browser session for this ghost/task
        handle = await provider.open(url, profile=ghost_id)
        await _emit(
            EventType.BROWSER_SESSION_OPENED,
            {"session_id": handle.session_id, "target": handle.target},
        )

        # 3. navigate
        await _emit(
            EventType.GHOST_STATUS_CHANGED,
            {"from_state": str(GhostState.OPENING_BROWSER), "to_state": str(GhostState.NAVIGATING)},
        )
        await provider.nav.goto(url)
        await _emit(EventType.BROWSER_NAVIGATE, {"url": url})

        # 4. extract
        await _emit(
            EventType.GHOST_STATUS_CHANGED,
            {"from_state": str(GhostState.NAVIGATING), "to_state": str(GhostState.EXTRACTING)},
        )
        result = await provider.scrape(handle, url, extract_schema=extract_schema)

        # 5. store — one result.record_extracted per record (with mission + url linkage)
        for record in result.records:
            await _emit(
                EventType.RESULT_RECORD_EXTRACTED,
                {"task_id": task.id, "mission_id": mission_id, "url": url, "record": record},
            )

        # 6. done — release the session and complete
        await provider.release()
        await _emit(EventType.TASK_COMPLETED, {"task_id": task.id, "mission_id": mission_id})
