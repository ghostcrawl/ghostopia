"""Server-side REAL task runner — one real GhostCrawl session drives one ghost (STAGE 3).

This is the stage-3 milestone seam: it runs the provider-agnostic
:class:`~ghostopia_agent_runtime.DeterministicRunner` against the REAL
:class:`~ghostopia_ghostcrawl_provider.GhostCrawlProvider` (the full-primitive provider,
backed by the Python SDK) and forwards the presentation the
:class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver` derives from the runner's
normalized envelopes to the authed WS. The SAME server-authoritative envelope / GhostDriver
path the sim uses carries the REAL work — the ONLY thing that changes between stage 2
and stage 3 is the source (``FakeBrowserProvider`` → ``GhostCrawlProvider``). The thin renderer is unchanged: it still applies ``ghost.spawned`` + ``ghost.command``.

Security:
* the mission URL is SSRF-validated (``validate_mission_url``) BEFORE any provider/SDK call;
* the GhostCrawl key lives only in :mod:`ghostopia_server.config` server-side — the inbound
  ``mission.submit`` carries only a target NAME + URL;
* one :class:`GhostCrawlProvider` session per task, released in a ``finally``;
* an SDK failure is normalized through ``map_sdk_error`` into a ``browser.error`` /
  ``task.retry`` envelope (never a raw exception fanned out).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from ghostcrawl.facade import GhostCrawlError
from ghostopia_agent_runtime import DeterministicRunner
from ghostopia_agent_runtime.agent_provider import AgentProvider
from ghostopia_ghost_runtime.ghost_driver import GhostDriver
from ghostopia_ghost_runtime.surface_vocab import sanitize_code, sanitize_text
from ghostopia_ghostcrawl_provider import (
    GhostCrawlProvider,
    MappedError,
    ProviderCallError,
    TargetRegistry,
    map_sdk_error,
)
from ghostopia_shared import Envelope, GhostCommand, Point, Task
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_world import WorldMap, load_default_map

from .config import DEFAULT_TARGET, build_target_registry
from .frame_fanout import FrameFanout, SessionRegistry
from .ssrf import Resolver, SsrfBlockedError, validate_mission_url
from .ws_gateway import WsGateway


def _byo_extract_key() -> str | None:
    """The operator's/user's OWN BYO structured-extraction key, from the environment.

    ``GHOSTOPIA_BYO_EXTRACT_KEY`` is a SELF-configured key in the user's own ``.env`` — their
    own LLM/MCP/AI. When set, GhostCrawl runs THEIR extraction (structured fields for arbitrary
    / irregular targets). When unset (the shipped keyless default) scrapes stay keyless and the
    deterministic extractor lifts the content. This is never an operator-key inferred for a
    customer request (harness-not-AI): it is the running instance's own configured key.
    """
    return (os.environ.get("GHOSTOPIA_BYO_EXTRACT_KEY") or "").strip() or None


#: Default model id per provider type when ``GHOSTOPIA_BYO_EXTRACT_MODEL`` is unset — a current,
#: capable model for each connectable provider so the operator only has to supply their key.
_BYO_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "openai_compat": "gpt-4o-mini",
    "google": "gemini-2.0-flash",
}


def _byo_model_provider() -> dict[str, str] | None:
    """The operator's OWN connected extraction MODEL, assembled from the environment.

    GhostCrawl's structured ``/v1/extract`` capability runs the caller's connected model, so it
    needs a ``{type, api_key, model, base_url?}`` credential — not a bare key. Built from:

    * ``GHOSTOPIA_BYO_EXTRACT_KEY``  — the api key (required to enable structured extraction);
    * ``GHOSTOPIA_BYO_EXTRACT_TYPE`` — ``anthropic`` (default) | ``openai`` | ``openai_compat`` |
      ``google``;
    * ``GHOSTOPIA_BYO_EXTRACT_MODEL``— the model id (defaults per type, see above);
    * ``GHOSTOPIA_BYO_EXTRACT_BASE_URL`` — required only for the openai/openai_compat types.

    Returns ``None`` when no key is set (the shipped keyless default — advanced real-retail
    departments then degrade to the rendered read). This is the running instance's OWN configured
    model, never a key inferred for a request (harness-not-AI)."""
    key = _byo_extract_key()
    if not key:
        return None
    ptype = (os.environ.get("GHOSTOPIA_BYO_EXTRACT_TYPE") or "anthropic").strip() or "anthropic"
    model = (os.environ.get("GHOSTOPIA_BYO_EXTRACT_MODEL") or "").strip() or _BYO_DEFAULT_MODELS.get(
        ptype, "claude-sonnet-4-5"
    )
    provider: dict[str, str] = {"type": ptype, "api_key": key, "model": model}
    base_url = (os.environ.get("GHOSTOPIA_BYO_EXTRACT_BASE_URL") or "").strip()
    if base_url:
        provider["base_url"] = base_url
    return provider

# The outbound envelope types the thin renderer applies to its store (identical to sim).
_COMMAND_TYPE = "ghost.command"
_SPAWN_TYPE = "ghost.spawned"

# The runner's normalized envelopes the driver consumes for the VISUAL, but which must ALSO
# reach the fan-out raw so the STAGE-7 result store persists the real records/completions and
# the dashboard metrics count real navigations/sessions. ghost.status_changed is NOT relayed
# (its {from,to} shape would clobber the roster status the frame-free poll owns).
_RELAY_RAW = frozenset(
    {
        "result.record_extracted",
        "result.scraped",
        "task.completed",
        "task.failed",
        "browser.session_opened",
        "browser.navigate",
        "browser.action",
    }
)

def build_catalog_sections_envelope(sections: Any) -> Envelope:
    """The ONE ``catalog.sections`` relay builder.

    Relays each department's NAMES + what-to-scrape target only — ``targetUrl`` / ``query`` /
    ``category`` + a ``hasSchema`` presence flag + the explicit ``kind`` tag (``"department"``
    for a real result repository, else ``null``). The ``extract_schema`` BODY is server-side
    scrape config and NEVER crosses the wire (thin-frontend, no key/schema on the client). Both
    the ``catalog.request`` handler AND the runtime ``section.save``/``section.remove`` editor
    rebroadcast through this one builder so the shape can never diverge. The client
    gates map result-clicks on ``kind === "department"`` — never inferred from ``targetUrl``
    presence: the server tag is the single source of truth.
    """
    return serialize_envelope(
        type="catalog.sections",
        ts=time.time(),
        payload={
            "sections": [
                {
                    "id": s.id,
                    "label": s.defn.label,
                    "role": s.role,
                    "capacity": s.capacity,
                    "accepts": list(s.accepts),
                    "kind": s.defn.kind,
                    # the opt-in real-retail flag so the UI can offer the toggle.
                    # An advanced department is OFF by default (spends the user's key).
                    "advanced": bool(getattr(s.defn, "advanced", False)),
                    "targetUrl": s.defn.target_url,
                    "query": s.defn.query,
                    "category": s.defn.category,
                    "hasSchema": bool(s.defn.extract_schema),
                }
                for s in sections
            ]
        },
    )


#: An async fan-out sink (``WsGateway.broadcast`` in production; a list collector in tests).
Broadcast = Callable[[Envelope], Awaitable[None]]

#: Builds the BrowserProvider a task runs against (injected as a MOCK in tests).
ProviderFactory = Callable[[], Any]

#: Governor-safe FALLBACK concurrency cap — used ONLY when the ghostcrawl entitlement is
#: unavailable (the keyless mode, an offline boot, a token-less deploy). It is NOT a tier
#: number and NOT the env hack it replaces: the real cap is the SDK ``me().max_concurrency``
#: (the account tier cap on cloud, or the operator-configured self-host cap), both
#: surfaced on the same ``/v1/me`` field. Kept modest so a mis-derivation can never
#: hammer the governor.
_DEFAULT_POOL_CAP = 8

#: A coroutine that fetches the caller's ``/v1/me`` entitlement dict (the SDK ``me()``).
MeProvider = Callable[[], Awaitable[Mapping[str, Any]]]


async def resolve_pool_cap(
    me: MeProvider | None,
    *,
    fallback: int = _DEFAULT_POOL_CAP,
) -> int:
    """Derive the pool/queue concurrency cap from the ghostcrawl entitlement.

    The SINGLE source of truth is the SDK ``me().max_concurrency`` — the account tier cap
    (cloud) or the operator-configured self-host cap, both carried on the same
    ``/v1/me`` field. There is NO local tier→number map here: the
    value is read from the product at runtime. When the entitlement is unavailable (no client
    / offline / a malformed value) the governor-safe ``fallback`` is used so the pool is never
    left unbounded.
    """
    limit = await read_me_max_concurrency(me)
    return limit if limit is not None else fallback


async def read_me_max_concurrency(me: MeProvider | None) -> int | None:
    """The account's concurrent-live-SESSION limit, or ``None`` when unknown.

    The live-session cap is ``me().max_live_sessions`` (e.g. scale = 3) — the number of
    OPEN interactive browser sessions the plan allows at once. This is DISTINCT from
    ``max_concurrency`` (the larger request/crawl concurrency, e.g. scale = 12): a featured
    workforce ghost opens a live session to be watchable, and the pool's live-session semaphore
    must be bounded by ``max_live_sessions`` so it locally WAITS for a real slot instead of
    letting an over-cap ``sessions.create`` 429 at the server and degrade the ghost to
    sessionless (which left the live browser inspector empty — the featured ghost never held a
    watchable session). Falls back to ``max_concurrency`` for older / self-host ``me()``
    responses that predate the ``max_live_sessions`` field.

    Returns ``None`` (limit UNKNOWN) when there is no client, the call fails, or both fields are
    absent/malformed — so a caller can distinguish "unknown" from a derived fallback and apply
    a governor-safe default of its own (the workforce's :data:`WORKFORCE_SAFE_DEFAULT`)."""
    if me is None:
        return None
    try:
        info = await me()
    except Exception:  # noqa: BLE001 - any client/network failure means "unknown limit"
        return None
    if not isinstance(info, Mapping):
        return None
    for field in ("max_live_sessions", "max_concurrency"):
        value = info.get(field)
        if isinstance(value, bool):  # a bool is an int subclass — never a cap.
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None


async def read_me_crawl_concurrency(me: MeProvider | None) -> int | None:
    """The account's concurrent-REQUEST limit (``me().max_concurrency``), or ``None`` if unknown.

    This is the tier's DATA-throughput budget (e.g. scale = 12) — how many scrapes/extracts/
    searches the workforce may run at once — DISTINCT from ``max_live_sessions`` (the small
    interactive-browser cap, e.g. 3). The workforce crawls at this concurrency so a department's
    products come back fast, while live sessions stay capped separately. Falls back to
    ``max_live_sessions`` for an older ``me()`` that lacks the field; ``None`` when unknown."""
    if me is None:
        return None
    try:
        info = await me()
    except Exception:  # noqa: BLE001 - any failure means "unknown"
        return None
    if not isinstance(info, Mapping):
        return None
    for field in ("max_concurrency", "max_live_sessions"):
        value = info.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None


def _tile_ground(x: float, y: float, tile_size: int) -> dict[str, float]:
    """Tile → world-pixel ground point (tile bottom-centre) — matches the renderer's seed
    convention so the server sends pixel coords the client uses directly (no client map)."""
    return {"x": x * tile_size + tile_size / 2.0, "y": y * tile_size + tile_size}


def _command_to_envelope(command: GhostCommand, tile_size: int) -> Envelope:
    """A driver :class:`GhostCommand` → a ``ghost.command`` envelope (tile→pixel converted).

    Byte-identical shape to :meth:`ghostopia_server.sim_runtime.SimRuntime._command_to_envelope`
    so ``liveClient`` is a true drop-in for ``simClient`` (same envelope contract)."""
    payload: dict[str, Any] = {"kind": command.kind, "args": dict(command.args)}
    if command.kind == "walk":
        args = payload["args"]
        dest = args.get("destination")
        if isinstance(dest, dict):
            args["destination"] = _tile_ground(dest["x"], dest["y"], tile_size)
        path = args.get("path")
        if isinstance(path, list):
            args["path"] = [
                _tile_ground(pt[0], pt[1], tile_size)
                for pt in path
                if isinstance(pt, (list, tuple)) and len(pt) >= 2
            ]
    return serialize_envelope(
        type=_COMMAND_TYPE, ts=time.time(), payload=payload, ghost_id=command.ghost_id
    )


def _grave_tile(world_map: WorldMap, key: str | None = None) -> Point:
    """A grave tile to spawn a ghost at.

    With ``key=None`` the first grave by id (a single live ghost / snapshot fallback). With a
    ``key`` (the ghost id) the ghosts are spread deterministically across ALL graves by a
    stable hash — a nice spatial spread with sharing when there are more ghosts than graves
    (dynamic graves), never every ghost piled on ``graves[0]``. Graves stay transient
    shared rest spots (no persisted per-ghost home)."""
    graves = sorted(world_map.graves.values(), key=lambda g: g.id)
    if not graves:
        return Point(x=0.0, y=0.0)
    if key is None:
        g = graves[0]
    else:
        # a STABLE hash (not the salted builtin ``hash``) so the spread is deterministic
        # across process restarts + tests.
        digest = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
        g = graves[digest % len(graves)]
    return Point(x=float(g.x), y=float(g.y))


async def run_real_task(
    mission_target: str,
    target: str,
    broadcast: Broadcast,
    *,
    registry: TargetRegistry | None = None,
    provider_factory: ProviderFactory | None = None,
    runner: AgentProvider | None = None,
    world_map: WorldMap | None = None,
    ghost_id: str = "ghost-live",
    ghost_name: str = "Vega",
    ghost_color: int = 0x9B7BFF,
    section: str = "horror-books",  # a real seeded department (extract-accepting)
    home_grave: str = "grave-1",
    extract_schema: dict[str, Any] | None = None,
    resolver: Resolver | None = None,
    allowed_self_host_hosts: tuple[str, ...] = (),
    session_registry: SessionRegistry | None = None,
    mission_id: str | None = None,
    task_id: str | None = None,
    is_paused: Callable[[], bool] | None = None,
    abort: asyncio.Event | None = None,
) -> MappedError | None:
    """Run ONE real GhostCrawl session end-to-end and animate a ghost through the real work.

    ``mission_target`` is the mission URL (user-supplied, untrusted); ``target`` is the
    registry target NAME (``"cloud"`` / ``"selfhost"``); ``broadcast`` is the authed WS
    fan-out. The URL is SSRF-validated FIRST (raising ``SsrfBlockedError`` before any SDK
    call), then a :class:`GhostCrawlProvider` for ``target`` is driven by a
    :class:`DeterministicRunner`; the runner's normalized envelopes flow through a
    :class:`GhostDriver` whose visual commands are broadcast as ``ghost.command`` (with a
    leading ``ghost.spawned``). An SDK failure is normalized into a ``browser.error`` /
    ``task.retry`` envelope + the ghost returns to the graveyard.

    Returns the :class:`MappedError` when the run failed (so the orchestrator's
    :class:`~ghostopia_orchestration.WorkQueue` can decide retry-with-backoff vs fail), or
    ``None`` on success. Legacy stage-3 callers may ignore the return.
    """
    # 1. SSRF gate — refuse a private/loopback/metadata target BEFORE any provider/SDK call.
    validated_url = validate_mission_url(
        mission_target, allowed_self_host_hosts, resolver=resolver
    )

    world_map = world_map if world_map is not None else load_default_map()
    tile_size = world_map.tile_size
    grave = _grave_tile(world_map)

    # 2. driver + an ordered command flush → broadcast (sync sink, async fan-out).
    pending: list[GhostCommand] = []

    def _sink(command: GhostCommand) -> None:
        pending.append(command)

    async def _flush() -> None:
        while pending:
            await broadcast(_command_to_envelope(pending.pop(0), tile_size))

    driver = GhostDriver(world_map, _sink)
    driver.handle_for(ghost_id).set_position(grave)

    # 3. announce the spawn so a fresh client renders the ghost at its grave.
    await broadcast(
        serialize_envelope(
            type=_SPAWN_TYPE,
            ts=time.time(),
            ghost_id=ghost_id,
            payload={
                "id": ghost_id,
                "name": ghost_name,
                # The spawn carries the ghost's REAL section (was hard-coded
                # "research"), so the world sprite lands in the same section as its roster
                # row — no roster↔world divergence. The orchestrator passes
                # the fan-out route's section; the legacy single-session path keeps research.
                "home_grave": home_grave,
                "section": section,
                "color": ghost_color,
                "state": "IDLE",
                "position": _tile_ground(grave.x, grave.y, tile_size),
            },
        )
    )

    # 4. an emit wrapper: every normalized envelope → the driver (→ presentation), and the
    #    lifecycle brackets the runner does NOT emit (walk / arrive) are injected around it so
    #    the coarse FSM drives IDLE→WALKING→AT_WORKSTATION→…→COMPLETED→RETURNING_HOME→IDLE.
    async def _drive(envelope: Envelope) -> None:
        driver.dispatch(envelope)
        await _flush()

    def _bracket(msg_type: str, payload: dict[str, Any]) -> Envelope:
        return serialize_envelope(type=msg_type, ts=time.time(), payload=payload, ghost_id=ghost_id)

    async def _emit(envelope: Envelope) -> None:
        await _drive(envelope)
        # Relay the REAL result/nav/session envelopes to the fan-out so the STAGE-7 result
        # store persists them + the client's dashboard counts them (the driver only derived
        # the ghost's visual from them; they never reached broadcast on their own).
        if envelope.type in _RELAY_RAW:
            await broadcast(envelope)
        # After the runner picks the task up, inject the walk-to-workstation bracket so the
        # ghost visibly leaves its grave (the runner emits no ghost.walking / ghost.arrived).
        if envelope.type == "task.assigned":
            await _drive(_bracket("ghost.walking", {}))
            await _drive(_bracket("ghost.arrived", {"where": "workstation"}))

    # 4b. runtime MANAGEMENT: a paused fan-out ghost must not open a session or call
    #     the provider — gate BEFORE any provider construction; a set abort event stops it
    #     cleanly (no session, no work). The pool record's paused/abort flags drive this.
    async def _await_unpaused() -> None:
        while is_paused is not None and is_paused():
            if abort is not None and abort.is_set():
                return
            await asyncio.sleep(0.02)

    await _await_unpaused()
    if abort is not None and abort.is_set():
        return None  # cancelled before opening a session — clean, no provider call.

    provider: Any = None
    provider_error: MappedError | None = None
    aborted = False
    try:
        # 5. select the REAL full-primitive provider for this target (or the injected mock).
        if provider_factory is None:
            reg = registry if registry is not None else build_target_registry()
            _byo = _byo_extract_key()
            _byo_model = _byo_model_provider()
            provider_factory = lambda: GhostCrawlProvider(  # noqa: E731
                reg, target=target, byo_extract_key=_byo, byo_model_provider=_byo_model
            )
        provider = provider_factory()

        # 5b. register the provider so the STAGE-4 FrameFanout can stream THIS ghost's real
        #     recordings.visual frames while it works (only the SELECTED ghost is ever watched).
        #     Unregistered in the finally so a released session can never leak.
        if session_registry is not None:
            session_registry.register(ghost_id, provider)

        # 6. build the task + run the deterministic pipeline through the REAL provider.
        active_runner = runner if runner is not None else DeterministicRunner()
        task = Task(
            id=task_id or f"live-{ghost_id}",
            kind="extract",
            mission_id=mission_id,
            target={"url": validated_url},
            params={
                "ghost_id": ghost_id,
                "urls": [validated_url],
                "extract_schema": extract_schema,
            },
        )
        # run the brain; when an abort seam is wired, race it so a management cancel HARD-stops
        # the in-flight run (the runner sub-task is cancelled → the finally releases the session).
        if abort is None:
            await active_runner.run_task(task, provider, _emit)
        else:
            run_fut = asyncio.ensure_future(active_runner.run_task(task, provider, _emit))
            abort_fut = asyncio.ensure_future(abort.wait())
            done, _pending = await asyncio.wait(
                {run_fut, abort_fut}, return_when=asyncio.FIRST_COMPLETED
            )
            if run_fut not in done:  # aborted first → stop the runner
                run_fut.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_fut
                aborted = True
            if not abort_fut.done():
                abort_fut.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await abort_fut
            if not aborted:
                run_fut.result()  # surface a runner error (ProviderCallError/GhostCrawlError)
    except ProviderCallError as err:
        provider_error = err.mapped
    except GhostCrawlError as err:  # a raw SDK raise the provider did not normalize
        provider_error = map_sdk_error(err)
    finally:
        # deregister from the frame-fanout registry BEFORE release so no stream targets a
        # released session.
        if session_registry is not None:
            session_registry.unregister(ghost_id)
        # one session per task — always released.
        if provider is not None:
            release = getattr(provider, "release", None)
            if release is not None:
                try:
                    await release()
                except (ProviderCallError, GhostCrawlError):
                    pass

    if aborted:
        # cancelled mid-run: the session was released in the finally; do NOT walk home or
        # emit further work — the ghost's run is over (management owns the visual/roster).
        return None

    if provider_error is not None:
        await _surface_error(provider_error, ghost_id, broadcast, _emit)
        return provider_error

    # 7. happy path — the runner emitted task.completed (→ COMPLETED); close the loop by
    #    walking the ghost home (COMPLETED→RETURNING_HOME→IDLE).
    await _drive(_bracket("ghost.returning_home", {}))
    await _drive(_bracket("ghost.arrived", {"where": "home"}))
    return None


async def _surface_error(
    mapped: MappedError,
    ghost_id: str,
    broadcast: Broadcast,
    emit: Callable[[Envelope], Awaitable[None]],
) -> None:
    """Surface a normalized SDK error: broadcast the normalized envelope AND drive the ghost
    to the Error Graveyard through the same pipeline (error anim → returning home)."""
    error_env = serialize_envelope(
        type=mapped.event_type,  # "browser.error" | "task.retry"
        ts=time.time(),
        ghost_id=ghost_id,
        payload={
            # ``code`` is retained for INTERNAL metric keying (captcha/rate classification);
            # ``display`` is the customer-facing curated phrase the client renders so a
            # raw/vendor-named SDK code never reaches the inspector/dashboard label.
            "code": mapped.code,
            "display": sanitize_code(mapped.code),
            "visual": mapped.visual,
            "retryable": mapped.retryable,
            "retry_after": mapped.retry_after,
        },
    )
    # 1. observable normalized error signal on the wire.
    await broadcast(error_env)
    # 2. drive the ghost's presentation (bubble + error anim where the FSM allows) then home.
    await emit(error_env)
    await emit(
        serialize_envelope(type="ghost.returning_home", ts=time.time(), payload={}, ghost_id=ghost_id)
    )
    await emit(
        serialize_envelope(
            type="ghost.arrived", ts=time.time(), payload={"where": "home"}, ghost_id=ghost_id
        )
    )


class RealTaskRuntime:
    """Mounts ``mission.submit`` behind the authed WS → :func:`run_real_task` (STAGE 3).

    Holds the :class:`WsGateway`, the world map, and a lazily-built :class:`TargetRegistry`
    (so the app boots for stages 1-2 without GhostCrawl creds; the registry is constructed on
    the first mission, when a token is actually required). Each ``mission.submit`` runs one
    real session as a background task so the WS receive loop stays responsive.
    """

    def __init__(
        self,
        gateway: WsGateway,
        *,
        registry: TargetRegistry | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self._gateway = gateway
        self._map: WorldMap = load_default_map()
        self._registry = registry
        # An optional provider factory (injected as a MOCK in tests so the WS wiring can be
        # proven without a live key); production leaves it None and builds a GhostCrawlProvider.
        self._provider_factory = provider_factory
        # The live-session registry the STAGE-4 FrameFanout reads to stream the selected
        # ghost's real frames; run_real_task registers/unregisters the provider per mission.
        self._session_registry = SessionRegistry()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def session_registry(self) -> SessionRegistry:
        """The ghost→provider registry the :class:`FrameFanout` streams from."""
        return self._session_registry

    def install(self) -> None:
        """Register the authed ``mission.submit`` control verb on the gateway."""
        self._gateway.register_control("mission.submit", self._on_submit)

    def _get_registry(self) -> TargetRegistry | None:
        if self._provider_factory is not None:
            return None  # a mock factory supplies the provider; no registry needed.
        if self._registry is None:
            self._registry = build_target_registry()
        return self._registry

    async def _on_submit(self, envelope: Envelope) -> None:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        url = str(payload.get("url", ""))
        target = str(payload.get("target_name") or DEFAULT_TARGET)
        task = asyncio.ensure_future(self._run_guarded(url, target))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_guarded(self, url: str, target: str) -> None:
        """Run one real mission; a rejected URL / unexpected fault becomes an
        ``error.rejected`` envelope instead of an unretrieved background-task exception."""
        try:
            await run_real_task(
                url,
                target,
                self._gateway.broadcast,
                registry=self._get_registry(),
                provider_factory=self._provider_factory,
                session_registry=self._session_registry,
            )
        except SsrfBlockedError as err:
            await self._gateway.broadcast(
                serialize_envelope(
                    type="error.rejected",
                    ts=time.time(),
                    # The customer-facing reject reason is sanitized (an SSRF message is
                    # safe, but the boundary guarantees no lexicon can ride this field).
                    payload={"reason": sanitize_text(str(err), fallback="That target isn't allowed.")},
                )
            )


def create_live_app(
    *,
    secret: str | None = None,
    provider_factory: ProviderFactory | None = None,
    pool_max_concurrent: int | None = None,
    me_provider: MeProvider | None = None,
) -> Any:
    """Build the ghostopia server app with STAGE 3 (real GhostCrawl) mounted behind
    ``mission.submit`` and STAGE 5 (the concurrent :class:`GhostPool`) wired in.

    Wraps :func:`ghostopia_server.app.create_app` (the authed WS host) and installs a
    :class:`RealTaskRuntime`. Run it with e.g.::

        GHOSTOPIA_JWT_SECRET=… GHOSTOPIA_GC_TOKEN=… \\
            uv run uvicorn ghostopia_server.gc_event_source:create_live_app --factory
    """
    from ghostopia_ghostcrawl_provider import GhostCrawlProvider
    from ghostopia_sections import load_default_sections

    from . import db as _db
    from .app import create_app
    from .ghost_pool import GhostPool
    from .management import ManagementError, handle_management_command
    from .orchestrator import Orchestrator
    from .results import ResultRecorder
    from .status_poll import start_status_poll, stop_status_poll

    app = create_app(secret=secret)
    gateway: WsGateway = app.state.ws_gateway

    # STAGE 7: open the SQLite result store + wrap the gateway's broadcast so EVERY outbound
    # envelope is persisted (missions/tasks/results) + drives a live progress fan-out before it
    # reaches the client. Wrapping BEFORE the pool/fanout/orchestrator are built means each of
    # them captures the recording broadcast (records come from the real extract stream).
    db_path = os.environ.get(
        "GHOSTOPIA_DB_PATH", str(Path(__file__).resolve().parents[2] / "ghostopia.sqlite")
    )
    conn = _db.open_db(db_path)
    recorder = ResultRecorder(conn, gateway.broadcast)
    gateway.broadcast = recorder.broadcast  # type: ignore[method-assign]
    app.state.db = conn
    app.state.result_recorder = recorder

    runtime = RealTaskRuntime(gateway, provider_factory=provider_factory)
    runtime.install()

    # load the shipped seed, then lay any runtime-authored departments (from a
    # SEPARATE user data file) on top — so a user's own targets survive a restart while the
    # shipped example departments stay pristine. The store is reused by the SectionEditor below so
    # every subsequent save/remove writes back to the SAME file.
    from .section_editor import (
        AuthoredSectionStore,
        authored_sections_path,
        merge_authored_sections,
    )

    authored_store = AuthoredSectionStore(authored_sections_path())
    sections = merge_authored_sections(load_default_sections(), authored_store)

    # STAGE 5: the concurrent GhostPool + its frame-free status poll. The pool shares the
    # runtime's SessionRegistry so the fan-out can stream ANY pooled ghost's real frames; the
    # status poll keeps every OTHER ghost's roster row fresh (never a second frame stream).
    # It is ALSO the ONE authoritative ghost registry: the orchestrator registers each
    # fan-out ghost here so ghost.manage reaches mission-spawned ghosts, not only pool ghosts.
    _reg_cache: dict[str, TargetRegistry] = {}

    # The live-view frame poll (cdp.frame) captures frames on CHROMIUM sessions, so the operator app
    # opens chromium sessions by default (env-overridable) — this is what lets the inspector show
    # a real browser for a session-backed ghost. A stateless ghost never opens a session, so its
    # engine is moot; a session-backed (featured) ghost needs chromium for the live view.
    _pool_engine = os.environ.get("GHOSTOPIA_ENGINE", "chrome").strip() or "chrome"

    def _pool_provider_factory() -> Any:
        if provider_factory is not None:
            return provider_factory()  # a mock factory (tests) supplies the provider.
        reg = _reg_cache.get("reg")
        if reg is None:
            reg = build_target_registry()
            _reg_cache["reg"] = reg
        return GhostCrawlProvider(
            reg,
            target=DEFAULT_TARGET,
            engine=_pool_engine,
            byo_extract_key=_byo_extract_key(),
            byo_model_provider=_byo_model_provider(),
        )

    def _is_url_allowed(url: str) -> Any:
        # Adapt the submit-time SSRF gate to the handle validator shape (True == allow) so a
        # pooled behavior that navigates a discovered URL is validated AT the handle.
        try:
            validate_mission_url(url, ())
            return True
        except SsrfBlockedError as err:
            return str(err)

    # Concurrency cap — the SINGLE SOURCE OF TRUTH is the ghostcrawl entitlement:
    # the SDK ``me().max_concurrency`` (the account tier cap on cloud, or the operator-
    # configured self-host cap) surfaced on ``/v1/me``. There is NO env hack and NO
    # local tier→number map. An EXPLICIT ``pool_max_concurrent`` kwarg overrides — that is the
    # operator app's LABELED sanctioned-exception workforce cap (``workforce_pool_cap()``) and the test
    # seam; when it is set the derivation below is skipped and the raise is surfaced as an
    # explicit exception. Otherwise the pool starts on the governor-safe fallback and
    # the real cap is derived from ``me()`` at startup (an async context) and applied BEFORE any
    # ghost spawns — one accessor, one code path for cloud + self-host.
    _cap_is_explicit = pool_max_concurrent is not None
    provisional_cap: int = (
        pool_max_concurrent if pool_max_concurrent is not None else _DEFAULT_POOL_CAP
    )

    async def _default_me() -> Mapping[str, Any]:
        """Fetch the caller's entitlement via the SAME target-registry SDK seam the pool uses
        (never a second client). Only wired for a real (token-backed) deploy — a mock
        ``provider_factory`` deployment has no registry/token and keeps the fallback cap."""
        reg = _reg_cache.get("reg")
        if reg is None:
            reg = build_target_registry()
            _reg_cache["reg"] = reg
        return await reg.client_for(DEFAULT_TARGET).me()

    _me_for_cap = me_provider
    if _me_for_cap is None and provider_factory is None:
        _me_for_cap = _default_me

    # (R6): the VISIBLE-WORKFORCE cap is DECOUPLED from the live-session cap so the
    # background baton relay can walk N ≫ 2 stateless stage ghosts concurrently while the
    # live-session semaphore independently caps open sessions at the plan limit. Sized
    # generously (departments × the three stages + queue hops) up front so the relay always has
    # room; the live cap is still the entitlement (set at startup / by the explicit operator cap).
    from .workforce import workforce_visible_cap

    pool = GhostPool(
        gateway.broadcast,
        provider_factory=_pool_provider_factory,
        session_registry=runtime.session_registry,
        sections=sections,  # the SAME section runtimes the orchestrator fans work into.
        is_url_allowed=_is_url_allowed,
        max_concurrent=provisional_cap,
        visible_workforce_cap=max(provisional_cap, workforce_visible_cap()),
    )
    # Is the live cap a raised operator/test exception (18-ghost workforce) rather than the
    # derived entitlement cap? The workforce surface reads this to LABEL itself honestly.
    app.state.pool_cap_sanctioned_exception = _cap_is_explicit

    # STAGE 6: the orchestrator OWNS mission.submit — a multi-target (urls) mission is split
    # + fanned out across sections/ghosts through the bounded WorkQueue (backoff/fail), on the
    # selected brain composed with the section role; a legacy {target_name,url} still runs the
    # single stage-3 session. Registered AFTER RealTaskRuntime so it overrides the verb. It
    # shares the pool as its authoritative ghost registry so fan-out ghosts are manageable.
    orchestrator = Orchestrator(
        gateway,
        sections=sections,
        provider_factory=provider_factory,
        session_registry=runtime.session_registry,
        pool=pool,
    )
    orchestrator.install()

    # REPLAY-ON-CONNECT: a client that connects AFTER ghosts already exist (a fresh tab
    # or a page refresh) missed their one-shot ``ghost.spawned`` broadcasts — it would show a
    # full roster (from the recurring status poll) over an EMPTY canvas. Replay a positioned
    # ghost.spawned per current pool ghost to THAT client only (idempotent upsert by id, so an
    # already-showing client that reconnects never double-counts).
    async def _replay_world(send: Any) -> None:
        for env in pool.spawn_snapshot():
            await send(env)

    gateway.set_on_connect(_replay_world)

    # STAGE 4: mount the selected-ghost frame fan-out (ghost.select → recordings.visual
    # watch() for ONLY the selected ghost), sharing the runtime's live-session registry. The
    # watch_interval drives the R7 dynamic re-eval: a featured ghost whose session opens AFTER
    # select upgrades activity→frames without a re-select (the poll only reads .session, so it
    # never spends the 2-slot live budget).
    fanout = FrameFanout(runtime.session_registry, gateway.broadcast, watch_interval=0.5)
    fanout.install(gateway)
    # P1 watched-hold: give the pool the fanout's selected-ghost probe so each ghost's context
    # gets a ``watched`` predicate. A session-backed featured ghost that is the selected ghost
    # holds its live browser open + keeps navigating (WATCH_BROWSE) so the operator sees a
    # continuous live view instead of only catching the brief per-cycle extraction window.
    pool.set_selected_probe(lambda: fanout.selected_ghost_id)

    # P3 pre-warmed session pool: keep a small set of chrome sessions OPEN ahead of time (up to
    # the live cap) so a featured ghost's browser is ALREADY warm — its open is an instant adopt,
    # not a cold ``sessions.create``. Only for a real (token-backed) deploy (a mock
    # provider_factory has no SDK client); disable with GHOSTOPIA_WARM_SESSIONS=0. The pool shares
    # the ONE live-session semaphore, so warm + in-use can never exceed the plan's cap.
    def _warm_target() -> int:
        raw = (os.environ.get("GHOSTOPIA_WARM_SESSIONS") or "").strip()
        if not raw:
            return 1
        try:
            return max(0, int(raw))
        except ValueError:
            return 1

    warm_target = _warm_target()
    if provider_factory is None and warm_target > 0:
        from .warm_pool import WarmSessionPool

        def _warm_client() -> Any:
            reg = _reg_cache.get("reg")
            if reg is None:
                reg = build_target_registry()
                _reg_cache["reg"] = reg
            return reg.client_for(DEFAULT_TARGET)

        async def _warm_create() -> tuple[str, str]:
            res = await _warm_client().sessions.create(engine=_pool_engine)
            sid = str(res["session_id"]) if hasattr(res, "get") else str(res)
            eng = str(res.get("engine", _pool_engine)) if hasattr(res, "get") else _pool_engine
            return sid, eng

        async def _warm_terminate(session_id: str) -> None:
            await _warm_client().sessions.terminate(session_id)

        warm_pool = WarmSessionPool(
            create=_warm_create,
            terminate=_warm_terminate,
            sema=lambda: pool.live_session_semaphore,
            clock=lambda: asyncio.get_running_loop().time(),
            target=warm_target,
        )
        pool.set_warm_pool(warm_pool)
        app.state.warm_session_pool = warm_pool
    else:
        app.state.warm_session_pool = None

    # STAGE 6 management surface: the operator can assign behavior/section, pause/resume, or
    # retarget a live pool ghost at runtime — Pydantic-validated + applied to authoritative
    # state (pool records + section rosters). NAMES only; no key crosses the WS.
    async def _on_manage(envelope: Envelope) -> None:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        try:
            result = handle_management_command(payload, pool, sections)
        except ManagementError as err:
            await gateway.broadcast(
                serialize_envelope(
                    type="error.rejected",
                    ts=time.time(),
                    payload={"reason": sanitize_text(str(err), fallback="That command didn't work.")},
                )
            )
            return
        # Operator commands (send/recall/reassign) carry a `walk` directive — broadcast
        # a `ghost.command` walk so the ghost visibly re-paths to the authoritative target
        # (the world reflects the server's post-command state). The management
        # result mutated the pool record's section/state authoritatively before this fan-out.
        walk = result.pop("walk", None)
        await gateway.broadcast(
            serialize_envelope(type="management.applied", ts=time.time(),
                               ghost_id=result.get("ghost_id"), payload=result)
        )
        if isinstance(walk, dict):
            await gateway.broadcast(
                serialize_envelope(
                    type="ghost.command",
                    ts=time.time(),
                    ghost_id=result.get("ghost_id"),
                    payload={"kind": "walk", "args": walk},
                )
            )

    gateway.register_control("ghost.manage", _on_manage)

    # STAGE-7 management catalog: relay the registered behaviors + sections so the Sections
    # panel + Ghost-inspector dropdowns are SERVER-SOURCED (adding a behavior/section needs no
    # UI edit). NAMES + labels only — no key, no internal mechanics.
    from ghostopia_behaviors import behaviors as _behaviors
    from ghostopia_behaviors.builtin import discover_builtins

    discover_builtins()  # idempotent — ensure builtins are registered before we list them.

    async def _on_catalog(_env: Envelope) -> None:
        await gateway.broadcast(
            serialize_envelope(
                type="catalog.behaviors",
                ts=time.time(),
                payload={
                    "behaviors": [
                        {"name": reg.name, "label": reg.meta.label, "kind": reg.meta.kind}
                        for reg in _behaviors.list()
                    ]
                },
            )
        )
        # Relay the department's what-to-scrape identity through the ONE shared builder
        # (targetUrl/query/category + hasSchema flag; schema BODY stays server-side).
        await gateway.broadcast(build_catalog_sections_envelope(sections))

    gateway.register_control("catalog.request", _on_catalog)

    # LIVE-MODE WORKFORCE (departments-only): a prominent one-click template that
    # animates the four seeded DEPARTMENTS (Horror/Mystery Books + Spooky Masks/Costumes) — each
    # spawning GHOSTS_PER_DEPARTMENT ghosts running its OWN role against its OWN real target and
    # surfacing the priced/detail list it brings back. Routed through the SAME authoritative
    # pool/section path; NAMES + URLs only over the wire; every scrape is a REAL GhostCrawl
    # session. The example.com "stage" (build_workforce/run_workforce) is gone forward-only.
    from .workforce import (
        FLAGSHIP_DEPARTMENT_IDS,
        WORKFORCE_MISSION_ID,
        run_department_workforce,
    )

    # (R3, one-per-port): the workforce is a SINGLETON. ``_workforce_state["running"]`` is
    # the persistent INTENT — "the workforce should be running" — which is reused to resume
    # on reconnect. The lock serializes start/stop so two SIMULTANEOUS ``workforce.start`` verbs
    # (a double-click, or an auto-Live reconnect racing a manual start) can never both spawn a
    # workforce: the first sets the flag under the lock, the second sees it set and is a
    # no-op/attach — mirrors ``SimRuntime._on_start``'s ``if self._running: return`` guard.
    _workforce_lock = asyncio.Lock()
    _workforce_state: dict[str, bool] = {"running": False}
    app.state.workforce_running = False
    # The background baton relay (stage ghosts + WorkQueue) started by the last run —
    # stashed so ``_stop_workforce`` can cancel its baton/sustainer tasks (the stage ghosts
    # themselves are despawned by their ``stage-*`` id prefix below).
    app.state.workforce_relay = None

    def _has_live_workforce() -> bool:
        # The workforce's own ghosts are ``dept-*`` (and ``stage-*``). The
        # presence of ANY tells resume whether the world SURVIVED (attach) or was torn down (restart).
        return any(
            gid.startswith("dept-") or gid.startswith("stage-")
            for gid in getattr(pool, "_records", {})
        )

    async def _start_workforce() -> None:
        async with _workforce_lock:
            if _workforce_state["running"] and _has_live_workforce():
                # ATTACH — a second start while the world is LIVE is a no-op, NEVER a second run.
                # (A reconnecting client re-materializes the live world via replay-on-connect, not
                # by re-spawning it here.) When the intent is set but the world was torn
                # down (the idle-teardown emptied the pool after the grace while every client was
                # gone), the guard falls through to RESTART below — attach-or-restart resume.
                return
            _workforce_state["running"] = True
            app.state.workforce_running = True  # persistent INTENT (resume reads this).
            # sim ↔ workforce MUTUAL EXCLUSION: the ambient sim world and the real workforce world
            # cannot both run at once. Starting the workforce preempts (stops) the sim so the map
            # shows ONE world. The sim is wired only in the operator workforce app (workforce_app)
            # and published on ``app.state.sim_runtime``; absent in the bare live app.
            sim = getattr(app.state, "sim_runtime", None)
            if sim is not None and getattr(sim, "running", False):
                with contextlib.suppress(Exception):
                    await sim.stop()
            try:
                await _run_workforce_body()
            except BaseException:
                # a failure to materialize the workforce must not leave a false "running" intent
                # (and must not tear the WS socket down) — reset the flag and re-raise so the
                # handler's own guard can decide (the control handler swallows to keep the socket).
                _workforce_state["running"] = False
                app.state.workforce_running = False
                raise

    async def _run_workforce_body() -> None:
        # 196: open the synthetic workforce mission BEFORE spawning so the results recorder has a
        # mission row to roll each ghost's real finds up under — this is what routes workforce +
        # department results into the SAME result.mission_progress → Data Graveyard pipeline a
        # mission uses (the "by department" view groups by each ghost's section). Idempotent.
        await gateway.broadcast(
            serialize_envelope(
                type="mission.created",
                ts=time.time(),
                payload={
                    "mission_id": WORKFORCE_MISSION_ID,
                    "title": "The spooky workforce",
                    "total": 0,
                },
            )
        )
        # Entering the workforce drives the flagship "spooky workforce" —
        # the seeded Horror/Mystery Books + Spooky Masks/Costumes departments, each running its
        # OWN role against its OWN real target and surfacing the priced/detail list it finds.
        # Same authoritative pool/section path; the department ghosts loop so they stay visibly
        # alive between runs (their finds keep landing in the Data Graveyard). Bounded +
        # stoppable — ghost.despawn / pool shutdown aborts each loop + releases its session.
        def _stash_relay(relay: Any) -> None:
            app.state.workforce_relay = relay

        # feature any ADVANCED real-retail departments the operator toggled on. The
        # set defaults empty (safe keyless mode only) and is mutated by the ``workforce.advanced``
        # verb below, which re-runs the workforce so the enabled dept begins running immediately.
        advanced_enabled = frozenset(getattr(app.state, "advanced_departments", set()))
        ids = list(
            await run_department_workforce(
                pool,
                sections,
                loop=True,
                broadcast=gateway.broadcast,
                on_relay=_stash_relay,
                advanced_enabled=advanced_enabled,
            )
        )
        # The workforce is HONEST about its concurrency. When the pool cap was
        # raised explicitly for the operator app (``pool_cap_sanctioned_exception``), the run is a LABELED
        # sanctioned exception rather than a silent env raise; when the cap is the derived
        # entitlement, the workforce RESPECTS it (the pool semaphore queues any overflow). Either
        # way the operator sees the cap the run honors.
        dept_ids_present = {
            s.id for s in sections if s.id in FLAGSHIP_DEPARTMENT_IDS
        }
        await gateway.broadcast(
            serialize_envelope(
                type="workforce.started",
                ts=time.time(),
                payload={
                    "ghost_ids": ids,
                    "count": len(ids),
                    "departments": len(dept_ids_present),
                    "pool_max": pool.max_concurrent,
                    "sanctioned_exception": bool(
                        getattr(app.state, "pool_cap_sanctioned_exception", False)
                    ),
                },
            )
        )

    async def _on_workforce(_env: Envelope) -> None:
        # (R3): the control verb NEVER lets an exception escape the WS receive loop
        # (which catches only WebSocketDisconnect) — a start fault must not tear the socket down
        # and churn reconnects. The singleton guard already makes a concurrent double-start a
        # no-op; this is the final backstop.
        with contextlib.suppress(Exception):
            await _start_workforce()

    async def _stop_workforce() -> None:
        # STOP the workforce (195): despawn every workforce/department ghost — each despawn aborts
        # its run + releases its session, and the ``ghost.despawned`` fan-out makes the client
        # play the dissolve flourish, so the whole workforce dematerializes back into the graves.
        async with _workforce_lock:
            # Clear the persistent INTENT under the SAME lock start uses, so a stop can
            # never interleave a start (and resume sees the world is intentionally down).
            _workforce_state["running"] = False
            app.state.workforce_running = False
            # Cancel the background baton relay's queue/sustainer tasks BEFORE despawning
            # its stage ghosts, so the sustainer cannot re-seed a wave mid-teardown.
            relay = getattr(app.state, "workforce_relay", None)
            if relay is not None:
                with contextlib.suppress(Exception):
                    await relay.stop()
                app.state.workforce_relay = None
            stopped: list[str] = []
            # DRAIN until stable — a relay queue.run() cancelled mid-flight can land a
            # late ``stage-*`` spawn after a one-shot snapshot, so re-snapshot in a bounded loop
            # until no workforce/department/stage ghost remains (the whole workforce dematerializes).
            for _ in range(8):
                remaining = [
                    gid
                    for gid in getattr(pool, "_records", {}).keys()
                    if gid.startswith("workforce-")
                    or gid.startswith("dept-")
                    or gid.startswith("stage-")
                ]
                if not remaining:
                    break
                for gid in remaining:
                    # despawn the FEATURED (dept-*) AND the background baton (stage-*) ghosts so
                    # the whole workforce dematerializes; live:sessions → 0.
                    if await pool.despawn(gid):
                        stopped.append(gid)
                        await gateway.broadcast(
                            serialize_envelope(type="ghost.despawned", ts=time.time(),
                                               ghost_id=gid, payload={"ghost_id": gid})
                        )
                await asyncio.sleep(0)
            await gateway.broadcast(
                serialize_envelope(type="workforce.stopped", ts=time.time(),
                                   payload={"stopped": stopped, "count": len(stopped)})
            )

    async def _on_workforce_stop(_env: Envelope) -> None:
        await _stop_workforce()

    async def _on_workforce_advanced(env: Envelope) -> None:
        # toggle an opt-in ADVANCED real-retail department on/off. Enabling adds its
        # id to the enabled set + re-runs the workforce so it starts running against real retail
        # immediately; disabling removes it (it stops on the next despawn/teardown). The verb
        # NEVER lets an exception escape the WS receive loop (a bad toggle must not tear the
        # socket down). Only a KNOWN advanced department id is honored — an arbitrary id is
        # ignored so the safe keyless default can never be bypassed with a spoofed toggle.
        with contextlib.suppress(Exception):
            payload = env.payload if isinstance(env.payload, dict) else {}
            section_id = str(payload.get("id", ""))
            enabled = bool(payload.get("enabled", False))
            advanced_ids = {s.id for s in sections if getattr(s.defn, "advanced", False)}
            if section_id not in advanced_ids:
                return
            current: set[str] = set(getattr(app.state, "advanced_departments", set()))
            if enabled:
                current.add(section_id)
            else:
                current.discard(section_id)
            app.state.advanced_departments = current
            await gateway.broadcast(
                serialize_envelope(
                    type="workforce.advanced",
                    ts=time.time(),
                    payload={"id": section_id, "enabled": enabled,
                             "advanced": sorted(current)},
                )
            )
            # re-run so an enabled dept begins immediately (idempotent per id; a disabled dept
            # is despawned on the next explicit stop / idle-teardown). Re-materialize
            # under the SAME lock start/stop use (so it can never interleave a concurrent
            # start/resume and defeat the singleton guard), and STOP the prior relay before a
            # new _run_workforce_body stashes a fresh one — otherwise the old relay's
            # ``_baton_sustainer`` keeps looping and dispatching unique ``stage-*`` ghosts,
            # orphaning one sustainer per advanced toggle for the life of the process.
            if getattr(app.state, "workforce_running", False):
                async with _workforce_lock:
                    prior = getattr(app.state, "workforce_relay", None)
                    if prior is not None:
                        with contextlib.suppress(Exception):
                            await prior.stop()
                        app.state.workforce_relay = None
                    await _run_workforce_body()

    gateway.register_control("workforce.start", _on_workforce)
    gateway.register_control("workforce.stop", _on_workforce_stop)
    gateway.register_control("workforce.advanced", _on_workforce_advanced)
    app.state.advanced_departments = set()
    # Expose the zero-arg start/stop so a app entrypoint drives the SAME authoritative workforce
    # path (no bespoke spawn code).
    app.state.start_workforce = _start_workforce
    app.state.stop_workforce = _stop_workforce

    # (R2) INTENT-BASED RESUME ON RECONNECT: ``app.state.workforce_running`` is the durable
    # "the workforce SHOULD be running" intent (set by _start_workforce, cleared only by an explicit
    # _stop_workforce — NOT by the idle-teardown, which despawns the pool but leaves the intent).
    # On the FIRST (re)connect, if the intent is set, attach-or-restart: a surviving world is a
    # no-op (_start_workforce's attach guard) and the per-client _replay_world repaints it; a world
    # the idle-teardown erased (a reload taking longer than the grace) is RESTARTED — so a reconnect
    # ALWAYS rejoins a live world instead of the "no ghosts on shift / summon the workforce" CTA.
    # This decouples resume from out-racing the 6s grace. The seam (WsGateway.set_on_first_connect)
    # was built in 191 and unused until now. Never STARTS a workforce the operator never intended.
    async def _resume_workforce() -> None:
        if not getattr(app.state, "workforce_running", False):
            return
        with contextlib.suppress(Exception):
            await _start_workforce()

    gateway.set_on_first_connect(_resume_workforce)

    # EASY per-section ADD / REMOVE: the Sections panel's +/- controls spawn a ghost
    # INTO a named section (idle-roaming ambient by default) or remove one authoritatively.
    # Routed through the SAME pool path; NAMES only. A per-section soft cap keeps the workforce sane.
    _spawn_counter = {"n": 0}
    _sections_by_id = {s.id: s for s in sections}
    _SECTION_SOFT_CAP = 8

    async def _on_ghost_spawn(env: Envelope) -> None:
        payload = env.payload if isinstance(env.payload, dict) else {}
        section_id = str(payload.get("section", ""))
        section = _sections_by_id.get(section_id)
        if section is None:
            # The (user-supplied) section id is echoed back — sanitize so a crafted id
            # carrying a banned term cannot leak onto the customer surface via error.rejected.
            await gateway.broadcast(
                serialize_envelope(type="error.rejected", ts=time.time(),
                                   payload={"reason": sanitize_text(
                                       f"unknown section {section_id!r}",
                                       fallback="That section doesn't exist.")})
            )
            return
        if len(pool.ghosts_by_section().get(section_id, [])) >= _SECTION_SOFT_CAP:
            await gateway.broadcast(
                serialize_envelope(type="error.rejected", ts=time.time(),
                                   payload={"reason": sanitize_text(
                                       f"section {section_id!r} is full",
                                       fallback="That section is full.")})
            )
            return
        # behavior: the section's own role when it's a registered behavior, else ambient roam.
        role = getattr(section, "role", "") or ""
        behavior_name = role if role in _behaviors.names() else "idle_wander"
        _spawn_counter["n"] += 1
        gid = f"op-{section_id}-{_spawn_counter['n']}"
        await pool.spawn(
            ghost_id=gid,
            name=f"{section_id.capitalize()} {_spawn_counter['n']}",
            section=section,
            behavior_name=behavior_name,
        )

    async def _on_ghost_despawn(env: Envelope) -> None:
        payload = env.payload if isinstance(env.payload, dict) else {}
        gid = str(payload.get("ghost_id", ""))
        removed = await pool.despawn(gid)
        if removed:
            await gateway.broadcast(
                serialize_envelope(type="ghost.despawned", ts=time.time(),
                                   ghost_id=gid, payload={"ghost_id": gid})
            )

    gateway.register_control("ghost.spawn", _on_ghost_spawn)
    gateway.register_control("ghost.despawn", _on_ghost_despawn)

    # GRAVEYARD BUILDER: the JWT-gated map.save/load/reset editor verbs. map.save
    # validates the operator's edited map server-side (schema + bounds + catalog allowlist +
    # every section plot present + A* reachability), recomputes collision/A*, swaps the pool's
    # authoritative map atomically, and rebroadcasts world.snapshot so all clients + running
    # ghosts pick it up. An invalid map is rejected with a reason; the live map is untouched.
    # The designed default graveyard stays the shipped default (map.reset restores it).
    from .map_editor import MapEditor

    map_editor = MapEditor(gateway.broadcast, pool=pool)
    map_editor.install(gateway)
    app.state.map_editor = map_editor

    # DEPARTMENT EDITOR: the JWT-gated section.save/section.remove authoring verbs on the
    # SAME authed gateway (no second unauthed path). section.save validates the operator's
    # department server-side (strict SectionDef → SSRF gate on target_url → surface-language
    # guard on label/category), then upserts it onto the orchestrator's LIVE sections (runtime
    # CRUD preserving rosters) + rebroadcasts catalog.sections through the shared builder. An
    # invalid/hostile department is rejected with a reason; the live section set is untouched.
    from .section_editor import SectionEditor

    # Pass the SAME authored store the boot merge used so every save/remove writes back
    # to the user data file (authored departments persist across a restart).
    section_editor = SectionEditor(gateway.broadcast, orchestrator, store=authored_store)
    section_editor.install(gateway)
    app.state.section_editor = section_editor
    app.state.authored_store = authored_store

    # STAGE 6 Task/mission MANAGEMENT surface: the first-class command verbs
    # (task.*/mission.* over the authed WS + an HTTP mirror) composing over the SAME bounded
    # dispatch the orchestrator uses — assign/run enqueue onto a governor-safe WorkQueue +
    # fan out by section role; retarget/cancel drive on_end(reason)+release. This COMPOSES
    # with (does not replace) the ghost/section management above; the frontend sends NAMES
    # only.
    from .task_routes import register_task_routes

    register_task_routes(
        app,
        gateway=gateway,
        sections=sections,
        behaviors=_behaviors,
        dispatch=orchestrator.make_dispatch(),
        session_registry=runtime.session_registry,
        secret=secret,
    )

    app.state.real_task_runtime = runtime
    app.state.orchestrator = orchestrator
    app.state.frame_fanout = fanout
    app.state.ghost_pool = pool
    app.state.default_sections = sections

    @app.on_event("startup")
    async def _derive_entitlement_cap() -> None:
        # Derive the REAL concurrency cap from the ghostcrawl entitlement now that we
        # have an event loop, and apply it to the authoritative pool BEFORE any ghost spawns
        # (the orchestrator's WorkQueue follows the pool cap, so this is the one source of
        # truth). An explicit/sanctioned cap is left untouched.
        if _cap_is_explicit:
            return
        # The workforce opens ONE live GhostCrawl session per department ghost, so the cap must
        # never exceed the account's concurrent-live-session limit (else every over-concurrent
        # ``sessions.create`` is rate-limited and the Data Graveyard stays empty). Derive the
        # real limit from the entitlement (``None`` when unknown) and clamp the workforce fleet
        # to it via ``workforce_pool_cap`` — env-forced by ``GHOSTOPIA_WORKFORCE_MAX_CONCURRENT``,
        # else ``min(desired, limit)``, else the governor-safe small default.
        from .workforce import workforce_pool_cap

        account_limit = await read_me_max_concurrency(_me_for_cap)
        pool.set_max_concurrent(workforce_pool_cap(account_limit))
        # Also apply the tier's REQUEST concurrency (``max_concurrency``, e.g. 12) as the crawl
        # cap, so the workforce fans out data calls at the tier limit (fast throughput) while live
        # sessions stay capped at ``max_live_sessions``. Unknown → keep the visible-cap default.
        crawl_limit = await read_me_crawl_concurrency(_me_for_cap)
        if crawl_limit is not None:
            pool.set_crawl_concurrency(max(crawl_limit, pool.max_concurrent))

    @app.on_event("startup")
    async def _start_warm_pool() -> None:
        # Registered AFTER _derive_entitlement_cap so the warm pool warms against the FINAL
        # live-session semaphore (the entitlement cap), never the provisional boot cap.
        warm = getattr(app.state, "warm_session_pool", None)
        if warm is not None:
            with contextlib.suppress(Exception):
                await warm.start()

    @app.on_event("startup")
    async def _start_status_poll() -> None:
        app.state.status_poll_task = start_status_poll(
            pool, gateway.broadcast, fanout=fanout, interval=1.0
        )

    @app.on_event("shutdown")
    async def _stop_status_poll() -> None:
        task = getattr(app.state, "status_poll_task", None)
        if task is not None:
            await stop_status_poll(task)
        # (R1): parity with ``pool.shutdown()`` — stop the ambient sim so its four
        # background loops never leak on process shutdown. The sim is mounted by the operator
        # workforce app (workforce_app) on ``app.state.sim_runtime`` AFTER this app is built, so
        # we read it lazily here (absent in the bare live app → a no-op). Best-effort.
        sim = getattr(app.state, "sim_runtime", None)
        if sim is not None:
            with contextlib.suppress(Exception):
                await sim.stop()
        warm = getattr(app.state, "warm_session_pool", None)
        if warm is not None:
            with contextlib.suppress(Exception):
                await warm.stop()
        await pool.shutdown()
        conn.close()

    # re-append the built-UI SPA catch-all so it sits LAST — after the task/mission
    # HTTP routes this factory registered above — and never shadows them (idempotent re-mount).
    from .app import mount_web_ui

    mount_web_ui(app)
    return app


__all__ = [
    "RealTaskRuntime",
    "create_live_app",
    "run_real_task",
]
