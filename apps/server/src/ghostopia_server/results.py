"""Result recorder — persist the REAL envelope stream + broadcast live progress (STAGE 7).

A :class:`ResultRecorder` sits IN the outbound fan-out: it wraps the gateway's ``broadcast``
so every server-authoritative envelope passes through it on the way to the thin client. It
persists the mission/task/result envelopes the orchestrator + runners emit
(:mod:`ghostopia_server.db`) and, after each extracted record / task completion, broadcasts a
derived ``result.mission_progress`` (progress rollup + a data preview) so the Data Graveyard +
dashboard update live — records come from REAL ``extract_schema`` output, never a canned
counter (REAL-NOT-MOCK).

Envelope → effect:

| type                        | effect                                                        |
|-----------------------------|---------------------------------------------------------------|
| ``mission.created``         | ``insert_mission(id, title, total)``                          |
| ``task.started``            | ``insert_task(id, mission_id, kind, section, behavior, url)`` |
| ``result.record_extracted`` | ``insert_result`` + emit ``result.mission_progress``          |
| ``result.scraped``          | ``insert_result`` (record under ``fields``) + emit progress   |
| ``task.completed``          | ``update_task(status='completed')`` + emit progress           |
| ``task.failed``             | ``update_task(status='failed')`` + emit progress              |

The recorder NEVER re-persists its own derived ``result.mission_progress`` (it is emitted via
the raw inner broadcast, and progress envelopes carry no ``task_id`` to persist), so there is
no feedback loop. Persistence uses only parameterized statements.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope

from . import db

__all__ = ["ResultRecorder"]

#: The async fan-out sink the recorder wraps (``WsGateway.broadcast`` in production).
Broadcast = Callable[[Envelope], Awaitable[None]]

# The maximum preview rows shipped alongside a progress update (the Data Graveyard paginates).
# 196: raised from 25 so the newest-N window covers EVERY department — the workforce spreads its
# finds across ~9 sections, and the "by department" view filters this one preview by section, so
# too small a window would leave a clicked department looking empty while it has real finds.
_PREVIEW_LIMIT = 150


class ResultRecorder:
    """Wraps ``broadcast`` to persist the real envelope stream + emit live progress."""

    def __init__(self, conn: sqlite3.Connection, inner: Broadcast) -> None:
        self._conn = conn
        self._inner = inner

    async def broadcast(self, envelope: Envelope | dict[str, Any]) -> None:
        """Persist ``envelope`` (if a mission/task/result event) then fan it out.

        The single choke point every outbound envelope flows through: the wrapped
        (``inner``) broadcast still validates + sends to the client, so wrapping is
        transparent. Any derived progress envelope is emitted via ``inner`` (never back
        through this method) so persistence never re-enters itself.
        """
        env = envelope if isinstance(envelope, Envelope) else Envelope.model_validate(envelope)
        mission_id = await self._persist(env)
        await self._inner(env)
        if mission_id is not None:
            await self._emit_progress(mission_id)

    async def _persist(self, env: Envelope) -> str | None:
        """Persist a mission/task/result envelope. Returns the mission id to emit progress
        for (when a record/completion changed a mission's rollup), else ``None``."""
        payload = env.payload if isinstance(env.payload, dict) else {}
        t = env.type

        if t == "mission.created":
            mid = _s(payload.get("mission_id"))
            if mid:
                db.insert_mission(
                    self._conn, mid, _s(payload.get("title")) or "",
                    _i(payload.get("total")),
                )
            return None

        if t == "task.started":
            task_id = _s(payload.get("task_id"))
            if task_id:
                db.insert_task(
                    self._conn,
                    task_id,
                    _s(payload.get("mission_id")),
                    _s(payload.get("kind")) or "",
                    _s(payload.get("section")),
                    _s(payload.get("behavior")),
                    _s(payload.get("url")),
                )
            return None

        # A mission-fan-out run emits ``result.record_extracted`` (record under ``record``); a
        # pool/workforce behavior (navigate_and_extract / search_and_detail) emits
        # ``result.scraped`` (record under ``fields``). Both are ONE extracted record — persist
        # them identically so workforce/department finds land in the Data Graveyard, not only
        # mission-path results (196: the "nothing shows" root cause).
        if t in ("result.record_extracted", "result.scraped"):
            task_id = _s(payload.get("task_id"))
            mid = _s(payload.get("mission_id"))
            record = payload.get("record") if "record" in payload else payload.get("fields")
            # Persist the ORIGIN DEPARTMENT the behavior tagged (repository_section), NOT the
            # ghost's rostered stage section — so a background relay ghost's find groups under its
            # department, not the research/extraction/verify desk it sits at. A record whose event
            # carries no section falls back (inside insert_result) to the owning task's section.
            db.insert_result(
                self._conn, task_id, mid, _s(payload.get("url")), record,
                section=_s(payload.get("section")),
            )
            return mid

        if t in ("task.completed", "task.failed"):
            task_id = _s(payload.get("task_id"))
            status = "completed" if t == "task.completed" else "failed"
            if task_id:
                db.update_task(self._conn, task_id, status=status)
            return _s(payload.get("mission_id"))

        return None

    async def _emit_progress(self, mission_id: str) -> None:
        """Broadcast the DB-computed progress rollup + a data preview for one mission.

        Emitted via the RAW inner broadcast (never ``self.broadcast``) so it is not
        re-persisted and cannot loop. The client's Dashboard + Data Graveyard render the
        rollup + preview straight from real persisted records."""
        progress = db.mission_progress(self._conn, mission_id)
        # GLOBAL preview (mission_id=None), never a single-mission slice: the client applies a
        # keyed merge over one growing global view, so a per-mission payload must not be able to
        # replace it with just this mission's rows. The rollup above stays mission-scoped.
        preview = db.result_preview(self._conn, mission_id=None, limit=_PREVIEW_LIMIT)
        await self._inner(
            serialize_envelope(
                type="result.mission_progress",
                ts=time.time(),
                payload={
                    "mission_id": mission_id,
                    "progress": progress,
                    "preview": preview,
                    "sections": db.section_throughput(self._conn),
                    "completed_missions": db.completed_missions(self._conn),
                    # The winning min-price offer per product (best-price selection, R5)
                    # — surfaced for the export + Data Graveyard "best" badge. Global (all
                    # sections), cheapest-first; the client groups/marks by section + url.
                    "best_offers": db.best_offers(self._conn),
                },
            )
        )


def _s(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _i(value: Any) -> int:
    return value if isinstance(value, int) else 0
