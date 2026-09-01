"""Server sim runtime — mounts the STAGE-2 simulated world behind the authed WS.

Constructs a :class:`~ghostopia_core.SimEventSource` (real behaviors over the
``FakeBrowserProvider`` — NO GhostCrawl, NO SDK) + a
:class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver`, calls
:func:`~ghostopia_core.wire_world`, and binds the driver's visual-command sink to the
:class:`~ghostopia_server.ws_gateway.WsGateway` fan-out. The whole thing sits behind
an authed operator control verb (``sim.start`` / ``sim.stop``): only a JWT-accepted client can
request the stream, and every visual command leaves the server as a validated ``Envelope``.

Seeds a handful of ghosts so the operator sees BOTH ends of dual control: 1-3 TASKED ghosts
running the real ``navigate_and_extract`` behavior through the full receive→walk→work→finish→
return loop, PLUS 1-2 IDLE ghosts running ``idle_wander`` so the graveyard is never dead. The
loop re-runs while the sim is on, so the world keeps living for the operator's eyeball.

The frontend imports NONE of this — it only speaks the WS, applying the broadcast
visual-command / spawn envelopes to its store.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ghostopia_core import SimEventSource, SimGhost, wire_world
from ghostopia_ghost_runtime.ghost_driver import GhostDriver
from ghostopia_shared import Bounds, Envelope, GhostCommand, GhostState, Point, SectionRef, Task
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_world import WorldMap, load_default_map

from .critters_runtime import CritterRuntime
from .prop_reactions import active_workstations, is_working_state, prop_state_envelope
from .smalltalk import SmallTalkDirector, TalkCandidate
from .ws_gateway import WsGateway

# The visual-command envelope type the thin renderer applies to its store (walk / face / anim
# / say / overlay). Distinct from the ``ghost.spawned`` bootstrap envelope.
_COMMAND_TYPE = "ghost.command"
_SPAWN_TYPE = "ghost.spawned"
# The despawn envelope the thin renderer applies to dissolve a sprite (parity with the pool's
# ``ghost.despawned``). Emitted for every sim ghost on stop so no orphan sprite lingers (R1).
_DESPAWN_TYPE = "ghost.despawned"


def _tile_ground(x: float, y: float, tile_size: int) -> dict[str, float]:
    """Tile → world-pixel ground point (tile bottom-centre) — matches the renderer's seed
    convention so the server sends pixel coords the client uses directly (no client map)."""
    return {"x": x * tile_size + tile_size / 2.0, "y": y * tile_size + tile_size}


class SimRuntime:
    """Owns the simulated-world lifecycle behind ``sim.start`` / ``sim.stop``."""

    def __init__(
        self,
        gateway: WsGateway,
        *,
        tick_ms: float = 220.0,
        tick_delay_s: float = 0.11,
        walk_ticks: int = 8,
        idle_ticks: int = 30,
        cycle_pause_s: float = 2.0,
        on_before_start: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._gateway = gateway
        # (mutual exclusion): the sim world and the workforce world cannot both run. When
        # the operator app wires the two together, this preempt hook stops the workforce before
        # the sim starts (the reverse direction of ``_start_workforce`` stopping the sim).
        self._on_before_start = on_before_start
        self._map: WorldMap = load_default_map()
        self._tile_size = self._map.tile_size
        self._tick_ms = tick_ms
        self._tick_delay_s = tick_delay_s
        self._walk_ticks = walk_ticks
        self._idle_ticks = idle_ticks
        self._cycle_pause_s = cycle_pause_s
        self._running = False
        self._queue: asyncio.Queue[Envelope] | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._pump_task: asyncio.Task[None] | None = None
        # small-talk: idle co-located ghosts trade a couple of spooky lines so the
        # graveyard feels alive when work is light. Director + the CURRENT cycle's driver+ids.
        self._director = SmallTalkDirector(max_dist=3.0 * float(self._tile_size))
        self._driver: GhostDriver | None = None
        self._ghost_ids: list[str] = []
        self._smalltalk_task: asyncio.Task[None] | None = None
        # graveyard critters + reactive props: autonomous cat/wisp/bat that
        # wander/idle/follow, and crypt-terminals that power on when a ghost works nearby.
        self._critters: CritterRuntime | None = None
        self._critter_task: asyncio.Task[None] | None = None
        # workstation props in WORLD PIXELS (bottom-centre, matching the renderer's placement)
        # so prop-active proximity is computed in the same space as the driver ghost positions.
        self._workstations = [
            (
                w.id,
                w.x * self._tile_size + self._tile_size / 2.0,
                w.y * self._tile_size + float(self._tile_size),
            )
            for w in self._map.workstations.values()
        ]

    # -- mounting --------------------------------------------------------------

    def install(self) -> None:
        """Register the authed control verbs on the gateway (called at app build)."""
        self._gateway.register_control("sim.start", self._on_start)
        self._gateway.register_control("sim.stop", self._on_stop)
        self._gateway.register_control("critter.pet", self._on_pet)

    async def _on_start(self, _envelope: Envelope) -> None:
        if self._running:
            return  # idempotent — a second sim.start (or a second client) is a no-op.
        # mutual exclusion: preempt the workforce before the sim world starts, so only ONE
        # world is ever live (the reverse of the workforce stopping the sim). Best-effort — a
        # preempt fault must never block starting the sim.
        if self._on_before_start is not None:
            with contextlib.suppress(Exception):
                await self._on_before_start()
        self._running = True
        self._queue = asyncio.Queue()
        self._critters = CritterRuntime(self._gateway.broadcast, self._map)
        await self._critters.spawn()
        self._pump_task = asyncio.ensure_future(self._pump())
        self._loop_task = asyncio.ensure_future(self._run_loop())
        self._smalltalk_task = asyncio.ensure_future(self._smalltalk_loop())
        self._critter_task = asyncio.ensure_future(self._critter_loop())

    async def _on_pet(self, envelope: Envelope) -> None:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        critter_id = str(payload.get("critter_id", ""))
        if self._critters is not None and critter_id:
            await self._critters.pet(critter_id)

    async def _on_stop(self, _envelope: Envelope) -> None:
        await self.stop()

    async def stop(self) -> None:
        """Stop the simulated world (idempotent) — public seam the idle-teardown + the server
        shutdown hook both call when the last operator disconnects / the process shuts down.

        R1: "stopped" must mean ZERO further broadcasts. Flipping ``_running=False`` is
        not enough — the four background loops (``_pump``/``_run_loop``/``_smalltalk``/
        ``_critter``) keep broadcasting until the in-flight cycle drains. So we CANCEL + AWAIT
        all four (clearing the handles), then emit ``ghost.despawned`` for the current cycle's
        sim ghosts so clients don't render orphaned sprites after the world stops."""
        was_running = self._running
        self._running = False
        # cancel + await the four background loops so none keeps broadcasting after stop returns.
        tasks = [self._pump_task, self._loop_task, self._smalltalk_task, self._critter_task]
        for task in tasks:
            if task is not None:
                task.cancel()
        for task in tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._pump_task = None
        self._loop_task = None
        self._smalltalk_task = None
        self._critter_task = None
        # despawn the sim's ghosts on clients (only if a cycle actually ran) so no orphan sprite
        # is left behind — the tasks are already stopped, so this is the LAST thing on the wire.
        if was_running:
            for gid in list(self._ghost_ids):
                await self._broadcast(_DESPAWN_TYPE, gid, {"ghost_id": gid})
        self._ghost_ids = []
        self._driver = None

    @property
    def running(self) -> bool:
        """True while the simulated world loops are active."""
        return self._running

    # -- fan-out ---------------------------------------------------------------

    def _sink(self, command: GhostCommand) -> None:
        """The driver's sync visual-command sink → an ordered async broadcast queue."""
        if self._queue is not None:
            self._queue.put_nowait(self._command_to_envelope(command))

    async def _pump(self) -> None:
        """Drain the command queue to the WS in order (validated per Envelope on send)."""
        while self._running:
            queue = self._queue
            if queue is None:
                return
            try:
                envelope = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            await self._gateway.broadcast(envelope)

    def _command_to_envelope(self, command: GhostCommand) -> Envelope:
        payload: dict[str, Any] = {"kind": command.kind, "args": dict(command.args)}
        if command.kind == "walk":
            args = payload["args"]
            dest = args.get("destination")
            if isinstance(dest, dict):
                args["destination"] = _tile_ground(dest["x"], dest["y"], self._tile_size)
            path = args.get("path")
            if isinstance(path, list):
                args["path"] = [
                    _tile_ground(pt[0], pt[1], self._tile_size)
                    for pt in path
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2
                ]
        return serialize_envelope(
            type=_COMMAND_TYPE, ts=time.time(), payload=payload, ghost_id=command.ghost_id
        )

    async def _broadcast(self, type: str, ghost_id: str, payload: dict[str, Any]) -> None:
        await self._gateway.broadcast(
            serialize_envelope(type=type, ts=time.time(), payload=payload, ghost_id=ghost_id)
        )

    # -- the living-world loop -------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            specs, spawns = self._seed()
            source = SimEventSource(
                specs,
                tick_ms=self._tick_ms,
                tick_delay_s=self._tick_delay_s,
                walk_ticks=self._walk_ticks,
                idle_ticks=self._idle_ticks,
            )
            driver = GhostDriver(self._map, self._sink)
            world = wire_world(self._sink, source, driver, self._map)
            # expose this cycle's driver + ids to the small-talk loop.
            self._driver = driver
            self._ghost_ids = [spec.ghost_id for spec in specs]

            # place each ghost at its home grave so the first A* walk plans from the right
            # tile, and announce the spawn so a fresh client can render the ghost.
            for spec, spawn in zip(specs, spawns, strict=True):
                pos = spawn["position"]
                driver.handle_for(spec.ghost_id).set_position(Point(x=pos["x"], y=pos["y"]))
                await self._broadcast(_SPAWN_TYPE, spec.ghost_id, spawn)

            await world.start()
            if not self._running:
                break
            await asyncio.sleep(self._cycle_pause_s)

    async def _smalltalk_loop(self) -> None:
        """Periodically pair IDLE co-located ghosts for a short spooky exchange.

        Reads the current cycle's driver for each ghost's coarse state + position, builds the
        candidate set (a WORKING/attention ghost is excluded so small-talk never speaks over real
        status), and forwards the director's scheduled ``say`` turns to the broadcast sink.
        """
        while self._running:
            await asyncio.sleep(1.0)
            driver = self._driver
            if driver is None:
                continue
            candidates: list[TalkCandidate] = []
            for gid in self._ghost_ids:
                pos = driver.handle_for(gid).position()
                candidates.append(
                    TalkCandidate(
                        ghost_id=gid,
                        x=pos.x,
                        y=pos.y,
                        idle=driver.state_of(gid) == GhostState.IDLE,
                    )
                )
            for cmd in self._director.step(candidates, time.time()):
                self._sink(cmd)

    async def _critter_loop(self) -> None:
        """Step the graveyard critters + reactive props each tick from REAL ghost state.

        Critters follow nearby ghosts using the driver's real pixel positions;
        crypt-terminals (P13) power on when a WORKING ghost is physically at/near them. Both
        are broadcast so the thin renderer draws them — no floor tile is ever mutated."""
        dt_ms = 150.0
        while self._running:
            await asyncio.sleep(dt_ms / 1000.0)
            driver = self._driver
            positions: dict[str, Point] = {}
            working_xy: list[tuple[float, float]] = []
            if driver is not None:
                for gid in self._ghost_ids:
                    pos = driver.handle_for(gid).position()
                    positions[gid] = pos
                    if is_working_state(driver.state_of(gid).value):
                        working_xy.append((pos.x, pos.y))
            if self._critters is not None:
                await self._critters.step(dt_ms, positions)
            # reactive props: which crypt-terminals a working ghost is powering
            # right now (a real ghost→workstation proximity, not a timer). Broadcast full state.
            active = active_workstations(working_xy, self._workstations)
            await self._gateway.broadcast(prop_state_envelope(self._workstations, active))

    # -- seeding ---------------------------------------------------------------

    def _seed(self) -> tuple[list[SimGhost], list[dict[str, Any]]]:
        """Seed 2 TASKED (navigate_and_extract) + 2 IDLE (idle_wander) ghosts + their spawn
        envelopes. Data-only; behaviors are resolved BY NAME in ``wire_world``."""
        section = self._wander_section()
        grave = self._grave_positions()

        tasked = [
            ("ghost-scout", "Scout", "grave-1", "research", 0x7ad7ff),
            ("ghost-digger", "Digger", "grave-2", "extraction", 0xffb347),
        ]
        # ambient idle ghosts: a few always wander the same section so the graveyard
        # is never dead when no mission runs — and, being co-located + idle, they are eligible to
        # pair up for small-talk (see _smalltalk_loop).
        idle = [
            ("ghost-keeper", "Keeper", "grave-3", section.id, 0x8be04a),
            ("ghost-rose", "Rose", "grave-4", section.id, 0xff5aa8),
            ("ghost-mossy", "Mossy", "grave-3", section.id, 0x7fd7c4),
        ]

        specs: list[SimGhost] = []
        spawns: list[dict[str, Any]] = []

        for gid, name, home, sect, color in tasked:
            task = Task(
                id=f"task-{gid}",
                kind="extract",
                target={"url": "https://acme.example"},
                params={
                    "ghost_id": gid,
                    "urls": ["https://acme.example"],
                    "extract_schema": {"title": "str", "price": "str"},
                    "dwell_ms": 700.0,
                    "max_pages": 2,
                },
            )
            specs.append(SimGhost(ghost_id=gid, behavior="navigate_and_extract", tasked=True, task=task))
            spawns.append(self._spawn(gid, name, home, sect, color, grave))

        for gid, name, home, sect, color in idle:
            task = Task(id=f"idle-{gid}", kind="ambient", params={"ghost_id": gid, "dwell_ms": 1400.0})
            specs.append(
                SimGhost(ghost_id=gid, behavior="idle_wander", tasked=False, task=task, section=section)
            )
            spawns.append(self._spawn(gid, name, home, sect, color, grave))

        return specs, spawns

    def _spawn(
        self,
        gid: str,
        name: str,
        home: str,
        section: str,
        color: int,
        grave: dict[str, dict[str, float]],
    ) -> dict[str, Any]:
        pos = grave.get(home) or next(iter(grave.values()))
        return {
            "id": gid,
            "name": name,
            "home_grave": home,
            "section": section,
            "color": color,
            "state": "IDLE",
            "position": pos,
        }

    def _grave_positions(self) -> dict[str, dict[str, float]]:
        return {
            g.id: _tile_ground(g.x, g.y, self._tile_size) for g in self._map.graves.values()
        }

    def _wander_section(self) -> SectionRef:
        """A section with real bounds AND ≥1 walkable tile for IdleWander to reach."""
        for name, b in self._map.regions.items():
            has_walkable = any(
                self._map.is_walkable(x, y)
                for y in range(b.y, b.y + b.h)
                for x in range(b.x, b.x + b.w)
            )
            if has_walkable:
                return SectionRef(id=name, role="idle", bounds=Bounds(x=b.x, y=b.y, w=b.w, h=b.h))
        # fallback: the whole map (guaranteed to have walkable tiles).
        return SectionRef(
            id="graveyard", role="idle", bounds=Bounds(x=0, y=0, w=self._map.width, h=self._map.height)
        )


def create_sim_app(*, secret: str | None = None) -> Any:
    """Build the ghostopia server app with the STAGE-2 simulation mounted behind ``sim.start``.

    Wraps :func:`ghostopia_server.app.create_app` (the authed WS host) and installs a
    :class:`SimRuntime` on its gateway. Run it with e.g.::

        GHOSTOPIA_JWT_SECRET=… uv run uvicorn ghostopia_server.sim_runtime:create_sim_app --factory
    """
    from .app import create_app

    app = create_app(secret=secret)
    gateway: WsGateway = app.state.ws_gateway
    runtime = SimRuntime(gateway)
    runtime.install()
    app.state.sim_runtime = runtime

    @app.on_event("shutdown")
    async def _stop_sim() -> None:
        # Parity with ``pool.shutdown()``: stop the sim on process shutdown so its four
        # background loops never leak (cancel+await + despawn its ghosts). Best-effort.
        with contextlib.suppress(Exception):
            await runtime.stop()

    return app
