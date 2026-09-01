"""The dual-target registry — N independent async SDK clients, no mode flag.

``ghostcrawl-provider`` owns a registry of named targets (e.g. ``"cloud"`` /
``"selfhost"``). There is **no SDK "mode" flag**: a client targets whatever
``base_url`` + ``token`` it was constructed with, so mixed routing is just N
independent long-lived ``AsyncGhostCrawl`` instances. The frontend only ever sends a
target **NAME** — never a key.

Secrets stay **server-side**: each target's ``token_ref`` is the
NAME of a server-side environment variable (env / the local ``pass`` vault export);
the token VALUE is resolved here and never logged, never placed in ``repr`` or errors.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

# The default SDK client factory. Importing ``ghostcrawl`` here is intentional and
# audited: this package is the SOLE SDK-importing package (grep-asserted in
# tests/test_sdk_first.py).
from ghostcrawl import AsyncGhostCrawl

#: A factory that builds one async SDK client from a resolved token + base_url.
ClientFactory = Callable[..., Any]


class UnknownTargetError(KeyError):
    """Raised when ``client_for`` is asked for a target name that was never configured.

    Deliberately carries ONLY the requested name (never a token) so an error surfaced
    to a caller/log cannot leak a secret.
    """


def _resolve_token(token_ref: str) -> str:
    """Resolve a target's token VALUE from its server-side ``token_ref`` env var name.

    Raises ``ValueError`` (naming only the ref, never the value) when the env var is
    unset — a misconfigured target must fail loudly at construction, not silently
    egress with an empty key.
    """
    value = os.environ.get(token_ref)
    if not value:
        raise ValueError(
            f"token ref {token_ref!r} is not set in the server environment "
            "(configure it via env / the local pass vault)"
        )
    return value


class TargetRegistry:
    """Named targets -> independent ``AsyncGhostCrawl`` clients.

    Built from a ``{ target_name: { base_url, token_ref } }`` config. Each target gets
    its OWN client constructed with its own ``base_url`` + server-resolved ``token``.
    Clients are long-lived (held for the process); ``aclose`` releases them all on
    shutdown.
    """

    def __init__(
        self,
        config: Mapping[str, Mapping[str, str]],
        *,
        client_factory: ClientFactory = AsyncGhostCrawl,
    ) -> None:
        if not config:
            raise ValueError("TargetRegistry requires at least one configured target")
        self._clients: dict[str, Any] = {}
        for name, spec in config.items():
            base_url = spec["base_url"]
            token = _resolve_token(spec["token_ref"])
            # token flows into the SDK ctor only; it is never stored on self and never
            # rendered by __repr__.
            self._clients[name] = client_factory(token=token, base_url=base_url)

    def client_for(self, name: str = "cloud") -> Any:
        """Return the long-lived SDK client for ``name`` (same instance across calls)."""
        try:
            return self._clients[name]
        except KeyError:
            raise UnknownTargetError(name) from None

    @property
    def targets(self) -> tuple[str, ...]:
        """The configured target names (no secrets)."""
        return tuple(self._clients)

    async def aclose(self) -> None:
        """Close every held client (best-effort; ``aclose`` on each if present)."""
        for client in self._clients.values():
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()

    def __repr__(self) -> str:
        # Names only — NEVER a token or base_url that could carry inline credentials.
        return f"TargetRegistry(targets={self.targets!r})"


__all__ = ["TargetRegistry", "UnknownTargetError"]
