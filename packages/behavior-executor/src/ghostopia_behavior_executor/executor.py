"""Layer 2: the pluggable ``Executor`` seam.

An ``Executor`` runs ONE behavior against a capability-scoped :class:`BehaviorContext` under
resource/time limits, driving ``on_start`` → ``on_tick``/``on_event`` → ``on_end`` and
guaranteeing ``on_end`` fires EXACTLY once (with session release) on every terminal path
(completed / failed / cancelled / timed-out).

The whole point of this seam is the **no-rewrite hardening path**. v0 is
:class:`~ghostopia_behavior_executor.in_process_executor.InProcessExecutor` — an in-process
capability-scoped asyncio executor, honest for operator-authored behaviors. When untrusted
authorship arrives, a ``SubprocessExecutor`` / separate-interpreter executor (marshalling the
ctx across the boundary) drops in behind THIS Protocol with **zero behavior-contract change**:
the :class:`~ghostopia_behaviors.behavior.Behavior` contract and
:func:`~ghostopia_behavior_executor.capability_scope.build_capability_scoped_context` are
untouched. That is only possible if behaviors are PURE with respect to the injected ctx
— a behavior with a module-level side effect or a ctx-external reach
is isolate-hostile and violates the contract.

⚠ Python ``eval``/``exec`` are NOT a security boundary and are not used anywhere here for
isolation. Authored/LLM output is DATA (validated, decode-only); the executor
grants a behavior NO way to run arbitrary host code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from ghostopia_behaviors.behavior import Behavior, BehaviorContext

__all__ = ["RunLimits", "RunOutcome", "RunResult", "Executor"]

# The terminal disposition of a run. Note ``timed_out`` is richer than the behavior-facing
# ``EndReason`` (which has no timed-out member): a wall-clock overrun is reported to the
# behavior as ``on_end("failed")`` but surfaced to the CALLER as ``"timed_out"``.
RunOutcome = Literal["completed", "failed", "cancelled", "timed_out"]


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Resource/time budget for one behavior run.

    * ``wall_clock_ms`` — the total budget for the whole run (start→ticks→end). A run that
      exceeds it ends ``timed_out`` (behavior sees ``on_end("failed")``).
    * ``tick_deadline_ms`` — the per-``on_tick`` deadline. An ``on_tick`` that overruns is
      FLAGGED and detached (never awaited forever) so no behavior can wedge the loop.
    """

    wall_clock_ms: float
    tick_deadline_ms: float


@dataclass(frozen=True, slots=True)
class RunResult:
    """What an ``Executor`` reports back for one run — the terminal outcome plus counters.

    ``tick_overruns`` lets a caller/test observe that a tick blew its deadline (flagged, not
    awaited). Every ``Executor`` implementation returns this same shape, which is what makes
    the InProcess↔Subprocess swap a no-rewrite change at the call site.
    """

    outcome: RunOutcome
    ticks: int = 0
    tick_overruns: int = 0


@runtime_checkable
class Executor(Protocol):
    """The pluggable run-a-behavior seam.

    A conforming executor MUST: drive ``on_start`` once; drive ``on_tick`` (and, when an
    event source is wired, ``on_event``) up to ``limits.wall_clock_ms``; call ``on_end``
    EXACTLY once with the mapped reason; release the ctx's browser session on end; and never
    let a behavior block the loop past ``limits.tick_deadline_ms`` per tick. It returns a
    :class:`RunResult`. It grants the behavior NO host-code path (see module docstring).
    """

    async def run(
        self, behavior: Behavior, ctx: BehaviorContext, limits: RunLimits
    ) -> RunResult: ...
