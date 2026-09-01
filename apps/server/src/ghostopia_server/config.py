"""Server-side config — the target registry + JWT secret (secrets NEVER leave the server).

The thin TS renderer sends only a target NAME (e.g. ``"cloud"``) + a mission URL; the
GhostCrawl credentials that a target needs are resolved HERE, server-side, from the
environment (env / the local ``pass`` vault export). This module is the ONE place the
harness turns a target name into a live SDK client, so a key is never reachable from the
frontend.

The target registry config is a ``{ target_name: { base_url, token_ref } }`` map (dual
target — cloud / self-host — no mode flag). ``token_ref`` is the NAME of a server-side env
var; :class:`~ghostopia_ghostcrawl_provider.target_registry.TargetRegistry` resolves the
token VALUE from it and never stores/logs it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from ghostopia_ghostcrawl_provider import TargetRegistry

from .auth import get_jwt_secret as _get_jwt_secret

#: The default registry target name a mission uses when the client sends none.
DEFAULT_TARGET = "cloud"

#: The default env var name a target's token is resolved from (a ``token_ref``).
_DEFAULT_TOKEN_REF = "GHOSTOPIA_GC_TOKEN"

#: The default cloud base URL when ``GHOSTOPIA_GC_BASE_URL`` is unset.
_DEFAULT_BASE_URL = "https://api.ghostcrawl.io"


def load_target_config(
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Load the ``{ target: { base_url, token_ref } }`` registry config from the environment.

    A full JSON map may be supplied via ``GHOSTOPIA_TARGETS`` (server-side only). When it is
    unset a single ``"cloud"`` target is synthesized from ``GHOSTOPIA_GC_BASE_URL`` +
    the canonical ``GHOSTOPIA_GC_TOKEN`` ref, so a one-target deploy needs no JSON.

    Never contains a token VALUE — only the NAME of the env var each target resolves from.
    """
    environ = os.environ if env is None else env
    raw = environ.get("GHOSTOPIA_TARGETS")
    if raw:
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("GHOSTOPIA_TARGETS must be a non-empty JSON object of targets")
        return {str(name): {str(k): str(v) for k, v in spec.items()} for name, spec in parsed.items()}
    return {
        DEFAULT_TARGET: {
            "base_url": environ.get("GHOSTOPIA_GC_BASE_URL", _DEFAULT_BASE_URL),
            "token_ref": _DEFAULT_TOKEN_REF,
        }
    }


def build_target_registry(
    config: Mapping[str, Mapping[str, str]] | None = None,
) -> TargetRegistry:
    """Build the :class:`TargetRegistry` from ``config`` (or the environment).

    Each target's token VALUE is resolved from its ``token_ref`` env var at construction
    and flows only into the SDK client ctor — never stored on the registry, never logged.
    Raises ``ValueError`` (naming only the ref) if a configured target's token env is unset.
    """
    return TargetRegistry(config if config is not None else load_target_config())


def get_jwt_secret() -> str:
    """The HS256 signing secret, read SERVER-SIDE from ``GHOSTOPIA_JWT_SECRET``.

    Thin re-export of :func:`ghostopia_server.auth.get_jwt_secret` so all server-side
    secret access funnels through ``config`` (no committed default; raises when unset).
    """
    return _get_jwt_secret()


__all__ = [
    "DEFAULT_TARGET",
    "build_target_registry",
    "get_jwt_secret",
    "load_target_config",
]
