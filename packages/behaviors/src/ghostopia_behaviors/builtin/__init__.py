"""builtin/ — the AUTO-DISCOVERY loader.

Importing this package imports EVERY behavior module in this directory, so each builtin
self-registers on import with ZERO renderer/core-loop edit. Author a behavior in ONE file,
drop it under ``builtin/`` → :func:`discover_builtins` picks it up on the next import (the
"adding a behavior never touches the renderer or core loop" property).

Convention: any module in this package whose name does NOT start with ``_`` is a behavior
module and is imported (a ``*_behavior`` naming suffix is honored too — it always matches).
Each module registers itself against the shared :data:`ghostopia_behaviors.registry.behaviors`
singleton at import time.
"""

from __future__ import annotations

import importlib
import pkgutil

__all__ = ["discover_builtins"]


def discover_builtins() -> list[str]:
    """Import every behavior module in this package so each self-registers.

    Idempotent: re-importing an already-imported module is a no-op, and builtins guard
    their own ``register`` calls against duplicates. Returns the module names imported.
    """
    imported: list[str] = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{mod.name}")
        imported.append(mod.name)
    return imported


# Run discovery on package import so ``import ghostopia_behaviors.builtin`` self-registers
# all builtins with no other edit.
discover_builtins()
