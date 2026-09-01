"""The normalized WebSocket envelope — the single validated wire boundary.

Every message between the Python server and the thin TS renderer is one ``Envelope``
(EVENT_PROTOCOL §1). It carries a ``protocol_version`` the server version-gates:
the field always parses (so the server can inspect and gate),
and ``is_supported_version`` surfaces a mismatch. ``extra='forbid'`` rejects unknown
top-level fields; ``type`` must be non-empty; unknown ``type`` values are rejected by
the consumer against the catalog (never ``eval``'d).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Bumped on any breaking schema change to the envelope / GhostEvent / Behavior contract.
PROTOCOL_VERSION: int = 1


class Envelope(BaseModel):
    """The normalized envelope: ``{protocol_version, type, ghost_id, ts, payload}``."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    type: str = Field(min_length=1)
    ghost_id: str | None = None
    ts: float
    payload: Any = None


def is_supported_version(envelope: Envelope) -> bool:
    """True when the envelope's ``protocol_version`` matches this server's contract.

    The server uses this to gate: an unsupported version is refused, not parsed as data.
    """
    return envelope.protocol_version == PROTOCOL_VERSION


def serialize_envelope(
    *,
    type: str,
    ts: float,
    payload: Any = None,
    ghost_id: str | None = None,
) -> Envelope:
    """Build an envelope stamped with the current ``PROTOCOL_VERSION``.

    ``type`` should be a value from the event catalog (``ghost.*``/``browser.*``/
    ``task.*``/``result.*``); it is validated non-empty.
    """
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        type=type,
        ghost_id=ghost_id,
        ts=ts,
        payload=payload,
    )


def parse_envelope(data: str | bytes | dict[str, Any]) -> Envelope:
    """Parse an inbound wire message (JSON string/bytes or a mapping) into an Envelope.

    Rejects unknown top-level fields (``extra='forbid'``) and an empty ``type``. The
    ``protocol_version`` is preserved for the caller to gate via ``is_supported_version``.
    """
    if isinstance(data, (str, bytes)):
        parsed: Any = json.loads(data)
        return Envelope.model_validate(parsed)
    return Envelope.model_validate(data)
