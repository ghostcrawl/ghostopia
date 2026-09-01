"""Props react to ghost state — generalize the terminal-flicker.

A crypt-terminal (and its nearby candles/lanterns) should power ON when a ghost is actually
WORKING at it, and settle when the ghost leaves. This is driven by the REAL ghost→workstation
relationship (a working ghost physically at/near a workstation), NOT a decorative timer:

* :func:`is_working_state` — the coarse states that mean "busy at the crypt-terminal";
* :func:`active_workstations` — the PURE predicate: which workstations have a working ghost
  within ``radius`` px (assigned+working ⇒ active; idle/away ⇒ settle);
* :func:`prop_state_payload` / :func:`prop_state_envelope` — the ``prop.state`` wire shape the
  thin renderer applies (swap the terminal to its active clip / raise its glow — in place, no
  floor tile ever mutates).

Everything here is pure + unit-testable; the server (sim_runtime) computes it each tick from
the driver's real ghost positions + states and broadcasts the envelope.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable

from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope

__all__ = [
    "PROP_ACTIVE_RADIUS_PX",
    "Workstation",
    "active_workstations",
    "is_working_state",
    "prop_state_envelope",
    "prop_state_payload",
]

_PROP_STATE_TYPE = "prop.state"

#: A ghost within this many pixels of a workstation while working powers it on.
PROP_ACTIVE_RADIUS_PX: float = 28.0

#: The coarse ghost states that mean "busy at the crypt-terminal" (a working relationship).
_WORKING_STATES = frozenset(
    {
        "AT_WORKSTATION",
        "OPENING_BROWSER",
        "NAVIGATING",
        "SEARCHING",
        "READING",
        "SCROLLING",
        "EXTRACTING",
        "PROCESSING",
        "WAITING",
        "RETRYING",
    }
)

#: A workstation as (id, world-pixel x, world-pixel y).
Workstation = tuple[str, float, float]


def is_working_state(state: str) -> bool:
    """True when a ghost in ``state`` is actively working (would power a nearby terminal)."""
    return state in _WORKING_STATES


def active_workstations(
    working_xy: Iterable[tuple[float, float]],
    workstations: Iterable[Workstation],
    radius: float = PROP_ACTIVE_RADIUS_PX,
) -> set[str]:
    """The set of workstation ids that have a WORKING ghost within ``radius`` px.

    PURE: ``working_xy`` is the pixel positions of ghosts currently in a working state (the
    caller filters by :func:`is_working_state`); a workstation is ACTIVE iff some working ghost
    sits within ``radius`` of it (assigned+working ⇒ active), else it settles (idle/away)."""
    pts = list(working_xy)
    active: set[str] = set()
    for wid, wx, wy in workstations:
        for gx, gy in pts:
            if math.hypot(gx - wx, gy - wy) <= radius:
                active.add(wid)
                break
    return active


def prop_state_payload(
    workstations: Iterable[Workstation], active_ids: set[str]
) -> dict[str, object]:
    """The ``prop.state`` payload: every workstation prop + whether it is currently active."""
    return {
        "props": [
            {"id": wid, "x": wx, "y": wy, "active": wid in active_ids}
            for (wid, wx, wy) in workstations
        ]
    }


def prop_state_envelope(
    workstations: Iterable[Workstation], active_ids: set[str]
) -> Envelope:
    """Build the ``prop.state`` envelope the renderer applies to its props (in place)."""
    return serialize_envelope(
        type=_PROP_STATE_TYPE,
        ts=time.time(),
        payload=prop_state_payload(workstations, active_ids),
    )
