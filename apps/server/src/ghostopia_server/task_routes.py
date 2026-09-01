"""Server task-routes — the Task/mission COMMAND surface over WS + an HTTP mirror.

The management verbs are exposed TWO ways, both applying the SAME
server-authoritative :class:`~ghostopia_task_manager.TaskManager` over validated data:

* **WS** — the inbound ``task.*`` / ``mission.*`` verbs ride the authed WS. They are
  already Pydantic-validated by ``schemas.INBOUND_MODELS`` (``extra='forbid'``) + JWT-gated at
  the gateway before a control handler ever runs, so a handler never sees unauthenticated or
  malformed input; an unknown verb is rejected by the gateway allow-list.
* **HTTP mirror** — the same verbs as FastAPI routes (POST/PATCH/DELETE ``/tasks`` +
  ``/tasks/{id}/{assign,run,pause,resume}``, GET ``/tasks/{id}`` + an SSE monitor) for
  curl/script/CI drivers, JWT-gated by the same secret, with the same validation + effect.

Every spec target/url is SSRF-revalidated BEFORE anything is enqueued;
the verbs carry NAMES only — no key crosses the WS/HTTP body.
The management surface COMPOSES with the orchestrator: it builds a bounded
:class:`~ghostopia_orchestration.WorkQueue` over the orchestrator's own per-task dispatch, so
assign/run enqueue onto the governor-safe queue + fan out by section role — never an ad-hoc
SDK session. It does NOT replace the orchestrator's ghost/section management.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from ghostopia_orchestration import WorkQueue
from ghostopia_sections import Section
from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_shared.task import MissionSpec, TaskSpec, TaskTarget
from ghostopia_task_manager import (
    InMemoryTaskStore,
    RecordNotFoundError,
    TaskManager,
    TaskManagerError,
    TaskStore,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth import get_jwt_secret, verify_token
from .config import DEFAULT_TARGET, build_target_registry
from .frame_fanout import SessionRegistry
from .ssrf import SsrfBlockedError, validate_mission_url
from .ws_gateway import WsGateway

__all__ = ["register_task_routes"]

#: Default roster ghosts seeded per accepting section so the queue can assign work.
_ROSTER_PER_SECTION = 4

Dispatch = Callable[..., Awaitable[Any]]


# --------------------------------------------------------------------------------------
# HTTP request bodies (thin wrappers; the spec models are the shared extra='forbid' types).
# --------------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AssignBody(_Strict):
    section: str | None = None
    ghost_ids: list[str] | None = None


class _UpdateBody(_Strict):
    patch: dict[str, Any] = {}


class _RetargetBody(_Strict):
    target: TaskTarget


class _MissionAssignBody(_Strict):
    section: str | None = None


def _ssrf_validate_spec(
    spec: TaskSpec, allowed_self_host_hosts: tuple[str, ...]
) -> None:
    """Re-validate EVERY url a spec would dispatch against BEFORE it is enqueued
    A blocked url raises :class:`SsrfBlockedError`; nothing is enqueued."""
    for url in spec.inputs.urls or []:
        validate_mission_url(str(url), allowed_self_host_hosts)


def register_task_routes(
    app: FastAPI,
    *,
    gateway: WsGateway,
    sections: Sequence[Section],
    behaviors: Any,
    dispatch: Dispatch,
    session_registry: SessionRegistry | None = None,
    secret: str | None = None,
    store: TaskStore | None = None,
    max_concurrent: int = 5,
    allowed_self_host_hosts: tuple[str, ...] = (),
    roster_per_section: int = _ROSTER_PER_SECTION,
) -> TaskManager:
    """Wire the Task/mission COMMAND surface (WS verbs + HTTP mirror) onto ``app``.

    Builds a :class:`TaskManager` over a bounded :class:`WorkQueue` (the orchestrator's own
    ``dispatch``), the shared ``sections``, the behaviors registry, and the gateway broadcast
    as the status/telemetry sink. Returns the manager (also on ``app.state.task_manager``).
    Composes with the orchestrator — it registers NEW ``task.*``/``mission.*`` verbs, leaving
    ``mission.submit`` / ``ghost.manage`` untouched.
    """
    resolved_secret = secret if secret is not None else get_jwt_secret()
    task_store: TaskStore = store if store is not None else InMemoryTaskStore()
    section_list = list(sections)

    # Seed a small roster on each accepting section so route_task has free ghosts to assign
    # (idempotent add_ghost; shares the runtimes the orchestrator/pool use).
    for section in section_list:
        if section.accepts:
            for i in range(roster_per_section):
                section.add_ghost(f"{section.id}-w{i}")

    queue = WorkQueue(section_list, dispatch, max_concurrent=max_concurrent)

    async def _quota() -> dict[str, Any]:
        """Best-effort wallet/quota probe (me()/usage()) surfaced before a mission runs."""
        try:
            reg = build_target_registry()
            client = reg.client_for(DEFAULT_TARGET)
        except Exception:
            return {}
        me = await _maybe_await(getattr(client, "me", None))
        usage = await _maybe_await(getattr(client, "usage", None))
        return {"me": _safe(me), "usage": _safe(usage)}

    async def _cancel_run(task_id: str, reason: str) -> None:
        """Lifecycle-clean cancel: release any live session bound to the task's ghost(s)
        (on_end(reason) fires through the released run). Best-effort."""
        try:
            record = task_store.get_task(task_id)
        except RecordNotFoundError:
            return
        if session_registry is None:
            return
        for ghost_id in record.ghost_ids or []:
            provider = session_registry.get(ghost_id)
            session_registry.unregister(ghost_id)
            release = getattr(provider, "release", None) if provider is not None else None
            if release is not None:
                try:
                    await release()
                except Exception:
                    pass

    manager = TaskManager(
        task_store,
        work_queue=queue,
        sections=section_list,
        behaviors=behaviors,
        emit=gateway.broadcast,
        quota=_quota,
        cancel_run=_cancel_run,
    )
    app.state.task_manager = manager
    app.state.task_queue = queue

    # keep background run() drains referenced so they are not GC'd mid-flight.
    background: set[asyncio.Task[Any]] = set()

    def _spawn(coro: Awaitable[Any]) -> None:
        fut = asyncio.ensure_future(coro)
        background.add(fut)
        fut.add_done_callback(background.discard)

    # -- WS control verbs ------------------------------------------------------------

    async def _reject(reason: str) -> None:
        await gateway.broadcast(
            serialize_envelope(type="error.rejected", ts=time.time(), payload={"reason": reason})
        )

    def _payload(env: Envelope) -> dict[str, Any]:
        return env.payload if isinstance(env.payload, dict) else {}

    async def _on_task_create(env: Envelope) -> None:
        payload = _payload(env)
        try:
            spec = TaskSpec.model_validate(payload.get("spec") or {})
            _ssrf_validate_spec(spec, allowed_self_host_hosts)
            await manager.create(spec)
        except (TaskManagerError, SsrfBlockedError, ValidationError) as err:
            await _reject(str(err))

    async def _on_task_assign(env: Envelope) -> None:
        payload = _payload(env)
        try:
            await manager.assign(
                str(payload["task_id"]),
                section=payload.get("section"),
                ghost_ids=payload.get("ghost_ids"),
            )
        except (TaskManagerError, RecordNotFoundError, KeyError) as err:
            await _reject(str(err))

    async def _on_task_run(env: Envelope) -> None:
        payload = _payload(env)
        task_id = payload.get("task_id")
        if not task_id:
            await _reject("task.run requires task_id")
            return
        # drain in the background so the WS receive loop stays responsive.
        _spawn(_guard_run(manager.run(str(task_id))))

    async def _on_task_update(env: Envelope) -> None:
        payload = _payload(env)
        try:
            await manager.update(str(payload["task_id"]), payload.get("patch") or {})
        except (TaskManagerError, RecordNotFoundError, KeyError) as err:
            await _reject(str(err))

    async def _on_task_pause(env: Envelope) -> None:
        await _simple_verb(manager.pause, _payload(env))

    async def _on_task_resume(env: Envelope) -> None:
        await _simple_verb(manager.resume, _payload(env))

    async def _on_task_cancel(env: Envelope) -> None:
        await _simple_verb(manager.cancel, _payload(env))

    async def _on_task_retarget(env: Envelope) -> None:
        payload = _payload(env)
        try:
            target = TaskTarget.model_validate(payload.get("target") or {})
            await manager.retarget(str(payload["task_id"]), target)
        except (TaskManagerError, RecordNotFoundError, ValidationError, KeyError) as err:
            await _reject(str(err))

    async def _on_task_monitor(env: Envelope) -> None:
        payload = _payload(env)
        try:
            snap = manager.monitor(str(payload["task_id"]))
        except (RecordNotFoundError, KeyError) as err:
            await _reject(str(err))
            return
        await gateway.broadcast(
            serialize_envelope(type="task.status", ts=time.time(), payload=snap)
        )

    async def _simple_verb(verb: Callable[[str], Awaitable[Any]], payload: dict[str, Any]) -> None:
        task_id = payload.get("task_id")
        if not task_id:
            await _reject("verb requires task_id")
            return
        try:
            await verb(str(task_id))
        except (TaskManagerError, RecordNotFoundError) as err:
            await _reject(str(err))

    async def _guard_run(coro: Awaitable[Any]) -> None:
        try:
            await coro
        except (TaskManagerError, RecordNotFoundError, SsrfBlockedError) as err:
            await _reject(str(err))

    # mission verbs -----------------------------------------------------------------

    async def _on_mission_create(env: Envelope) -> None:
        payload = _payload(env)
        try:
            spec = MissionSpec.model_validate(payload.get("spec") or {})
            for member in spec.tasks:
                _ssrf_validate_spec(member, allowed_self_host_hosts)
            await manager.create_mission(spec)
        except (TaskManagerError, SsrfBlockedError, ValidationError) as err:
            await _reject(str(err))

    async def _on_mission_assign(env: Envelope) -> None:
        payload = _payload(env)
        try:
            await manager.assign_mission(
                str(payload["mission_id"]), section=payload.get("section")
            )
        except (TaskManagerError, RecordNotFoundError, KeyError) as err:
            await _reject(str(err))

    async def _on_mission_run(env: Envelope) -> None:
        payload = _payload(env)
        mission_id = payload.get("mission_id")
        if not mission_id:
            await _reject("mission.run requires mission_id")
            return
        _spawn(_guard_run(manager.run_mission(str(mission_id))))

    async def _on_mission_pause(env: Envelope) -> None:
        await _simple_mission(manager.pause_mission, _payload(env))

    async def _on_mission_resume(env: Envelope) -> None:
        await _simple_mission(manager.resume_mission, _payload(env))

    async def _on_mission_cancel(env: Envelope) -> None:
        await _simple_mission(manager.cancel_mission, _payload(env))

    async def _on_mission_retarget(env: Envelope) -> None:
        payload = _payload(env)
        try:
            target = TaskTarget.model_validate(payload.get("target") or {})
            await manager.retarget_mission(str(payload["mission_id"]), target)
        except (TaskManagerError, RecordNotFoundError, ValidationError, KeyError) as err:
            await _reject(str(err))

    async def _on_mission_monitor(env: Envelope) -> None:
        payload = _payload(env)
        try:
            snap = manager.monitor_mission(str(payload["mission_id"]))
        except (RecordNotFoundError, KeyError) as err:
            await _reject(str(err))
            return
        await gateway.broadcast(
            serialize_envelope(type="mission.status", ts=time.time(), payload=snap)
        )

    async def _simple_mission(
        verb: Callable[[str], Awaitable[Any]], payload: dict[str, Any]
    ) -> None:
        mission_id = payload.get("mission_id")
        if not mission_id:
            await _reject("verb requires mission_id")
            return
        try:
            await verb(str(mission_id))
        except (TaskManagerError, RecordNotFoundError) as err:
            await _reject(str(err))

    for msg_type, handler in {
        "task.create": _on_task_create,
        "task.assign": _on_task_assign,
        "task.run": _on_task_run,
        "task.update": _on_task_update,
        "task.pause": _on_task_pause,
        "task.resume": _on_task_resume,
        "task.retarget": _on_task_retarget,
        "task.cancel": _on_task_cancel,
        "task.monitor": _on_task_monitor,
        "mission.create": _on_mission_create,
        "mission.assign": _on_mission_assign,
        "mission.run": _on_mission_run,
        "mission.pause": _on_mission_pause,
        "mission.resume": _on_mission_resume,
        "mission.retarget": _on_mission_retarget,
        "mission.cancel": _on_mission_cancel,
        "mission.monitor": _on_mission_monitor,
    }.items():
        gateway.register_control(msg_type, handler)

    # -- HTTP command mirror ---------------------------------------------------------

    def _require_auth(
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
    ) -> None:
        raw = token or _bearer(authorization)
        if not raw:
            raise HTTPException(status_code=401, detail="missing token")
        try:
            verify_token(raw, secret=resolved_secret)
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="invalid token") from None

    auth = Depends(_require_auth)

    def _bad_request(err: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(err))

    @app.post("/tasks", dependencies=[auth])
    async def _http_create(spec: TaskSpec) -> dict[str, Any]:
        try:
            _ssrf_validate_spec(spec, allowed_self_host_hosts)
            task_id = await manager.create(spec)
        except (TaskManagerError, SsrfBlockedError) as err:
            raise _bad_request(err) from None
        return {"task_id": task_id}

    @app.post("/tasks/{task_id}/assign", dependencies=[auth])
    async def _http_assign(task_id: str, body: _AssignBody) -> dict[str, Any]:
        try:
            return await manager.assign(
                task_id, section=body.section, ghost_ids=body.ghost_ids
            )
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        except TaskManagerError as err:
            raise _bad_request(err) from None

    @app.post("/tasks/{task_id}/run", dependencies=[auth])
    async def _http_run(task_id: str) -> dict[str, Any]:
        try:
            return await manager.run(task_id)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        except (TaskManagerError, SsrfBlockedError) as err:
            raise _bad_request(err) from None

    @app.post("/tasks/{task_id}/pause", dependencies=[auth])
    async def _http_pause(task_id: str) -> dict[str, Any]:
        return await _http_simple(manager.pause, task_id)

    @app.post("/tasks/{task_id}/resume", dependencies=[auth])
    async def _http_resume(task_id: str) -> dict[str, Any]:
        return await _http_simple(manager.resume, task_id)

    @app.patch("/tasks/{task_id}", dependencies=[auth])
    async def _http_update(task_id: str, body: _UpdateBody) -> dict[str, Any]:
        try:
            return await manager.update(task_id, body.patch)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        except TaskManagerError as err:
            raise _bad_request(err) from None

    @app.delete("/tasks/{task_id}", dependencies=[auth])
    async def _http_cancel(task_id: str) -> dict[str, Any]:
        return await _http_simple(manager.cancel, task_id)

    @app.get("/tasks/{task_id}", dependencies=[auth])
    async def _http_monitor(task_id: str) -> dict[str, Any]:
        try:
            return manager.monitor(task_id)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None

    @app.get("/tasks/{task_id}/stream", dependencies=[auth])
    async def _http_stream(task_id: str) -> StreamingResponse:
        try:
            manager.monitor(task_id)  # 404 fast if unknown.
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        return StreamingResponse(
            _sse_monitor(manager, task_id), media_type="text/event-stream"
        )

    @app.post("/missions", dependencies=[auth])
    async def _http_mission_create(spec: MissionSpec) -> dict[str, Any]:
        try:
            for member in spec.tasks:
                _ssrf_validate_spec(member, allowed_self_host_hosts)
            mission_id = await manager.create_mission(spec)
        except (TaskManagerError, SsrfBlockedError) as err:
            raise _bad_request(err) from None
        return {"mission_id": mission_id}

    @app.post("/missions/{mission_id}/run", dependencies=[auth])
    async def _http_mission_run(mission_id: str) -> dict[str, Any]:
        try:
            return await manager.run_mission(mission_id)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        except TaskManagerError as err:
            raise _bad_request(err) from None

    @app.get("/missions/{mission_id}", dependencies=[auth])
    async def _http_mission_monitor(mission_id: str) -> dict[str, Any]:
        try:
            return manager.monitor_mission(mission_id)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None

    async def _http_simple(
        verb: Callable[[str], Awaitable[Any]], task_id: str
    ) -> dict[str, Any]:
        try:
            return await verb(task_id)
        except RecordNotFoundError as err:
            raise HTTPException(status_code=404, detail=str(err)) from None
        except TaskManagerError as err:
            raise _bad_request(err) from None

    return manager


async def _sse_monitor(
    manager: TaskManager, task_id: str, *, interval: float = 0.2, max_ticks: int = 600
) -> Any:
    """Yield ``text/event-stream`` status frames until the task is terminal (or a cap).

    Reads the AUTHORITATIVE store snapshot each tick + emits it when it changes — the same
    task.* telemetry the WS event bus carries, mirrored to an HTTP SSE driver (§4.3)."""
    last: str | None = None
    terminal = {"completed", "failed", "cancelled"}
    for _ in range(max_ticks):
        try:
            snap = manager.monitor(task_id)
        except RecordNotFoundError:
            return
        frame = json.dumps(snap)
        if frame != last:
            yield f"data: {frame}\n\n"
            last = frame
        if snap["status"] in terminal:
            return
        await asyncio.sleep(interval)


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _maybe_await(fn: Any) -> Any:
    if fn is None:
        return None
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, dict, list)):
        return value
    return str(value)
