"""``wire_world`` — the STAGE-2 composition: EventSource -> GhostDriver + Behaviors -> WS.

This is where the pieces built in isolation become a living world:

* the :class:`~ghostopia_core.event_source.EventSource` (a
  :class:`~ghostopia_core.sim_event_source.SimEventSource` in stages 1-2) produces the
  normalized event stream;
* every event is fed through the :class:`~ghostopia_ghost_runtime.ghost_driver.GhostDriver`,
  which keeps each ghost's authoritative coarse state and emits PRESENTATION visual commands
  (walk with an A* path / work / success / contextual bubbles) into the injected
  ``broadcast`` sink (the server WS fan-out);
* each ghost mounts ONE ``Behavior`` — resolved BY NAME through the
  :data:`~ghostopia_behaviors.behaviors` registry (data-driven; the core loop NEVER branches
  on behavior kind) — with a capability-scoped :class:`BehaviorContext` whose ``ghost`` is the
  driver's own concrete ``GhostHandle`` (so the behavior's motion + the driver's presentation
  share one handle), whose ``browser`` is the per-ghost ``FakeBrowserProvider`` (no SDK), whose
  ``world`` is the read-only :class:`WorldQuery`, and whose ``emit`` re-enters the SAME event
  stream the driver consumes.

Because everything hangs behind the ``EventSource`` seam, stage 3 swaps the simulated source
for real GhostCrawl events with ZERO change to the driver, the behaviors, or the renderer.
"""

from __future__ import annotations

import random
from collections.abc import Callable

import ghostopia_behaviors.builtin  # noqa: F401  (runs the builtin auto-discovery loader)
from ghostopia_behaviors import behaviors as behavior_registry
from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_ghost_runtime.ghost_driver import GhostDriver
from ghostopia_shared import GhostCommand
from ghostopia_world import WorldMap, create_world_query

from ghostopia_core.sim_event_source import SimEventSource

# The visual-command sink the driver + handles push to (the server WS fan-out in
# ``sim_runtime``; a list collector in tests). Sync — a scheduling adapter bridges it to an
# async WS ``broadcast`` upstream.
BroadcastSink = Callable[[GhostCommand], None]


class WiredWorld:
    """The composed world: a started/stopped :class:`EventSource` feeding a
    :class:`GhostDriver` with behaviors mounted per ghost."""

    def __init__(self, source: SimEventSource, driver: GhostDriver) -> None:
        self.source = source
        self.driver = driver

    async def start(self) -> None:
        """Start producing events (drives the mounted behaviors + the state chain)."""
        await self.source.start()

    async def stop(self) -> None:
        await self.source.stop()


def wire_world(
    broadcast: BroadcastSink,
    source: SimEventSource,
    driver: GhostDriver,
    world_map: WorldMap,
    *,
    seed: int = 1337,
) -> WiredWorld:
    """Compose the world: subscribe the driver to the source + mount a Behavior per ghost.

    ``broadcast`` is the visual-command sink the ``driver`` (and every ``GhostHandle`` it
    owns) was constructed to push to — every presentation command fans out through it. This
    function:

    1. subscribes ``driver.dispatch`` to ``source`` so every produced event updates the
       ghost's coarse state and emits presentation into ``broadcast``;
    2. for each ghost the source declares, builds a :class:`BehaviorContext`
       (``ghost`` = the driver's shared concrete ``GhostHandle``, ``browser`` = the ghost's
       ``FakeBrowserProvider``, ``world`` = the read-only ``WorldQuery``, ``emit`` = the
       source's own fan-out so behavior events re-enter the stream, plus ``task``/``section``
       /seeded ``rng``);
    3. resolves the ghost's behavior BY NAME via the registry (``navigate_and_extract`` /
       ``idle_wander`` / ``agent`` / any dropped-in module) and MOUNTS it on the source, which
       ticks it.

    Returns a :class:`WiredWorld` whose ``start()`` runs the simulation.
    """
    # 1. every produced event -> the driver -> coarse state + presentation into broadcast.
    #    (``broadcast`` is the sink the driver + its handles were built with; referencing it
    #    here documents + fixes the fan-out target the presentation flows into.)
    _ = broadcast
    source.subscribe(driver.dispatch)

    world_query = create_world_query(world_map, random.Random(seed))

    # 2 + 3. mount one behavior per declared ghost, data-driven by registry name.
    for spec in source.ghosts:
        handle = driver.handle_for(spec.ghost_id)  # SHARED with the driver's presentation.
        ctx = BehaviorContext(
            ghost=handle,
            browser=source.provider_for(spec.ghost_id),
            world=world_query,
            emit=source.emit,
            task=spec.task,
            section=spec.section,
            rng=random.Random(seed + hash(spec.ghost_id) % 100_000),
        )
        behavior = behavior_registry.create(spec.behavior)  # BY NAME (no kind switch).
        source.mount(spec.ghost_id, behavior, ctx)

    return WiredWorld(source, driver)
