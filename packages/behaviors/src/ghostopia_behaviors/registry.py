"""The BehaviorRegistry — register-by-name plugin API with capability meta as DATA.

Replace the hardcoded ``createDriverForAgent`` switch with a
``register(name, factory, meta)`` registry. ``meta`` carries capability/classification as
DATA (kind/needs/overlay/label + the ``param_schema``/``examples`` the
management UI and AI author against) — NOTHING here branches on a hardcoded behavior-kind
``if/elif`` switch, so adding a behavior never touches the renderer or core loop.

``create(name)`` returns a FRESH instance per assignment (each ghost gets its own behavior
state). A duplicate ``register`` and an unknown ``create``/``get`` both raise.

A module-level singleton :data:`behaviors` is the process-wide registry the builtin
auto-discovery loader and the composition layer share.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ghostopia_behaviors.behavior import Behavior

__all__ = [
    "BehaviorMeta",
    "BehaviorRegistration",
    "BehaviorRegistry",
    "behaviors",
]

# A zero-arg factory producing a FRESH Behavior instance per assignment.
BehaviorFactory = Callable[[], Behavior]

# The builtin ``list`` type, captured before the ``BehaviorRegistry.list`` method shadows
# the name inside the class body (so return annotations resolve to the type, not the method).
_List = list


@dataclass(frozen=True)
class BehaviorMeta:
    """Capability/classification carried as DATA (never branched on).

    Fields:
      * ``kind``         — free-form classification hint (``deterministic``/``llm``/
        ``ambient``/…); the registry does NOT enumerate a fixed set.
      * ``needs``        — context deps the runner must inject (e.g. ``["browser"]``).
      * ``label``        — human label for the management UI.
      * ``param_schema`` — a Pydantic model the AI/management surface authors against.
      * ``examples``     — machine-readable ``{title, params}`` examples for AI authoring.
      * ``overlay``      — optional default status-overlay hint the renderer reads as data.
    """

    kind: str
    needs: list[str]
    label: str
    param_schema: type[BaseModel]
    examples: list[dict[str, Any]] = field(default_factory=list)
    overlay: str | None = None


@dataclass(frozen=True)
class BehaviorRegistration:
    """One registered behavior: its ``name``, a zero-arg ``factory``, and its ``meta``."""

    name: str
    factory: BehaviorFactory
    meta: BehaviorMeta


class BehaviorRegistry:
    """A name → :class:`BehaviorRegistration` map. Meta is data; there is no kind switch."""

    def __init__(self) -> None:
        self._registry: dict[str, BehaviorRegistration] = {}

    def register(self, name: str, factory: BehaviorFactory, meta: BehaviorMeta) -> None:
        """Register ``name`` with its ``factory`` + capability ``meta``. Duplicate raises."""
        if name in self._registry:
            raise ValueError(f"behavior {name!r} already registered")
        self._registry[name] = BehaviorRegistration(name=name, factory=factory, meta=meta)

    def create(self, name: str) -> Behavior:
        """Return a FRESH behavior instance for ``name``. Unknown name raises ``KeyError``."""
        return self.get(name).factory()

    def get(self, name: str) -> BehaviorRegistration:
        """Return the registration for ``name``. Unknown name raises ``KeyError``."""
        try:
            return self._registry[name]
        except KeyError:
            raise KeyError(f"no behavior registered as {name!r}") from None

    def list(self) -> _List[BehaviorRegistration]:
        """All registrations (with meta incl. ``param_schema``/``examples``) as data — the
        exact surface the management/AI-authoring layer consumes."""
        return _List(self._registry.values())

    def names(self) -> _List[str]:
        """Registered behavior names."""
        return _List(self._registry.keys())


#: The process-wide registry the builtin loader + composition layer share.
behaviors = BehaviorRegistry()
