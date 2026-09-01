"""Single-operator JWT auth — HS256 mint/verify via PyJWT.

The ghostopia server is single-operator, local-first: the frontend authenticates
its WebSocket upgrade with a short-lived HS256 token the server minted. The signing
secret lives SERVER-SIDE ONLY — read from the ``GHOSTOPIA_JWT_SECRET`` env var (which
the operator sources from ``pass``); there is deliberately NO hardcoded fallback, so a
missing secret is a hard error, never a silent weak default.

The token is never handed a GhostCrawl key; it only proves "the operator opened this
session". Verification runs on the WS accept in ``ws_gateway`` before the connection is
accepted.
"""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

_ALGORITHM = "HS256"
_SECRET_ENV = "GHOSTOPIA_JWT_SECRET"
_DEFAULT_TTL_SECONDS = 12 * 60 * 60  # 12h operator session


def get_jwt_secret() -> str:
    """Return the HS256 signing secret from the environment (server-side only).

    Raises ``RuntimeError`` when ``GHOSTOPIA_JWT_SECRET`` is unset — there is NO
    committed default. The operator sources it from ``pass``/env.
    """
    secret = os.environ.get(_SECRET_ENV)
    if not secret:
        raise RuntimeError(
            f"{_SECRET_ENV} is not set — the ghostopia server refuses to mint/verify "
            "tokens without an operator-provided secret (no hardcoded default)."
        )
    return secret


def mint_token(*, subject: str, secret: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed HS256 token for ``subject`` valid for ``ttl_seconds``.

    A non-positive ``ttl_seconds`` produces an already-expired token (used in tests to
    exercise the expiry path).
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def verify_token(token: str, *, secret: str) -> dict[str, Any]:
    """Verify + decode an HS256 token, returning its claims.

    Raises ``jwt.ExpiredSignatureError`` for an expired token and
    ``jwt.InvalidTokenError`` (its base class) for a tampered signature, wrong secret,
    or malformed token. Callers on the WS handshake treat ANY raise as "reject".
    """
    return jwt.decode(token, secret, algorithms=[_ALGORITHM])
