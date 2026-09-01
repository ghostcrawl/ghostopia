"""The coarse ghost lifecycle state machine.

A deterministic, PURE reducer over the 16 :class:`~ghostopia_shared.GhostState`
lifecycle states (``IDLE`` .. ``RETURNING_HOME``). ``reduce(state, event)`` maps a
current state + a normalized event to the next state via a table-driven legal-
transition set (:data:`transitions`). Rejected (illegal) transitions leave the state
UNCHANGED — never an undefined/garbage state.

This is the COARSE lifecycle ONLY. It holds NO task-decision logic: a Behavior DECIDES
actions and EMITS events; the FSM merely REACTS to them (single direction of authority).
The work phase (NAVIGATING..PROCESSING) is filled by whatever the
active Behavior does; the FSM just follows the event stream through it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ghostopia_shared import GhostState


class _EventLike(Protocol):
    """Structural shape of an event ``reduce`` accepts (``GhostEvent`` / ``Envelope``)."""

    type: str
    payload: Any


Guard = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class Transition:
    """One legal edge: ``event`` (dotted type) + optional payload ``guard`` -> ``to``."""

    event: str
    to: GhostState
    guard: Guard | None = None


# --------------------------------------------------------------------------------------
# Payload guards (the ONLY place payload is inspected — never for task decisions, only to
# disambiguate which coarse edge a browser.action / browser.error takes).
# --------------------------------------------------------------------------------------


def _kind_is(want: str) -> Guard:
    return lambda p: p.get("kind") == want


def _retryable(p: dict[str, Any]) -> bool:
    return bool(p.get("retryable"))


def _not_retryable(p: dict[str, Any]) -> bool:
    return not bool(p.get("retryable"))


# --------------------------------------------------------------------------------------
# Common edges shared by every WORK state (any-work error/complete handling).
# --------------------------------------------------------------------------------------

_WORK_STATES: tuple[GhostState, ...] = (
    GhostState.OPENING_BROWSER,
    GhostState.NAVIGATING,
    GhostState.SEARCHING,
    GhostState.READING,
    GhostState.SCROLLING,
    GhostState.EXTRACTING,
    GhostState.PROCESSING,
)


def _work_common() -> list[Transition]:
    # A retryable error parks the ghost in WAITING; a non-retryable error or an explicit
    # task.failed drops it to ERROR; a behavior-emitted task.completed ends the work.
    return [
        Transition("browser.error", GhostState.WAITING, _retryable),
        Transition("browser.error", GhostState.ERROR, _not_retryable),
        Transition("task.failed", GhostState.ERROR),
        Transition("task.completed", GhostState.COMPLETED),
    ]


# --------------------------------------------------------------------------------------
# The legal-transition table (coarse lifecycle). Rules are evaluated in order; the FIRST
# whose event matches AND whose guard passes wins.
# --------------------------------------------------------------------------------------

transitions: dict[GhostState, tuple[Transition, ...]] = {
    GhostState.IDLE: (
        Transition("task.assigned", GhostState.RECEIVING_TASK),
        Transition("ghost.assigned", GhostState.RECEIVING_TASK),
        Transition("ghost.wander", GhostState.IDLE),  # ambient idle-wander self-loop
    ),
    GhostState.RECEIVING_TASK: (
        Transition("ghost.walking", GhostState.WALKING),
    ),
    GhostState.WALKING: (
        # WALKING is toward a workstation; arrival lands AT_WORKSTATION.
        Transition("ghost.arrived", GhostState.AT_WORKSTATION),
    ),
    GhostState.AT_WORKSTATION: (
        Transition("browser.session_opened", GhostState.OPENING_BROWSER),
    ),
    GhostState.OPENING_BROWSER: (
        Transition("browser.navigate", GhostState.NAVIGATING),
        *_work_common(),
    ),
    GhostState.NAVIGATING: (
        Transition("browser.action", GhostState.SEARCHING, _kind_is("search")),
        Transition("browser.action", GhostState.READING, _kind_is("read")),
        Transition("browser.action", GhostState.SCROLLING, _kind_is("scroll")),
        *_work_common(),
    ),
    GhostState.SEARCHING: (
        Transition("browser.action", GhostState.READING, _kind_is("read")),
        *_work_common(),
    ),
    GhostState.READING: (
        Transition("browser.action", GhostState.SCROLLING, _kind_is("scroll")),
        Transition("result.record_extracted", GhostState.EXTRACTING),
        *_work_common(),
    ),
    GhostState.SCROLLING: (
        Transition("browser.action", GhostState.READING, _kind_is("read")),
        Transition("result.record_extracted", GhostState.EXTRACTING),
        *_work_common(),
    ),
    GhostState.EXTRACTING: (
        Transition("result.record_extracted", GhostState.EXTRACTING),  # more records
        Transition("task.started", GhostState.PROCESSING),
        *_work_common(),
    ),
    GhostState.PROCESSING: (
        Transition("task.completed", GhostState.COMPLETED),
        Transition("browser.error", GhostState.WAITING, _retryable),
        Transition("browser.error", GhostState.ERROR, _not_retryable),
        Transition("task.failed", GhostState.ERROR),
    ),
    GhostState.WAITING: (
        Transition("task.retry", GhostState.RETRYING),
        Transition("task.failed", GhostState.ERROR),
    ),
    GhostState.RETRYING: (
        Transition("browser.navigate", GhostState.NAVIGATING),  # re-dispatch
        Transition("task.failed", GhostState.ERROR),
    ),
    GhostState.ERROR: (
        Transition("ghost.returning_home", GhostState.RETURNING_HOME),
    ),
    GhostState.COMPLETED: (
        Transition("ghost.returning_home", GhostState.RETURNING_HOME),
    ),
    GhostState.RETURNING_HOME: (
        Transition("ghost.arrived", GhostState.IDLE),  # arrived back at the grave
        Transition("ghost.idle", GhostState.IDLE),
    ),
}


def _match(state: GhostState, event: _EventLike) -> Transition | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for t in transitions.get(state, ()):  # noqa: SIM110 (guard eval, not any())
        if t.event == event.type and (t.guard is None or t.guard(payload)):
            return t
    return None


def is_legal(state: GhostState, event: _EventLike) -> bool:
    """Whether ``event`` triggers a legal transition out of ``state``."""
    return _match(state, event) is not None


def reduce(state: GhostState, event: _EventLike) -> GhostState:
    """Return the next coarse lifecycle state for ``(state, event)``.

    Pure and total: an illegal transition returns ``state`` unchanged; the result is
    ALWAYS a real :class:`GhostState`, never an undefined value.
    """
    t = _match(state, event)
    return t.to if t is not None else state
