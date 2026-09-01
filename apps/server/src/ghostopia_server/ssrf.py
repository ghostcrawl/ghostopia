"""SSRF gate — thin re-export of the shared policy.

The SSRF policy body now lives in ``ghostopia_shared.ssrf`` so the ONE gate mandate
on "every target + every search-result visit" is importable by BOTH the server (mission/task
dispatch) AND the ``search_and_detail`` behavior (which visits result urls inline). This
module keeps the server's historical import surface byte-for-byte unchanged — every existing
``from ghostopia_server.ssrf import …`` (orchestrator, task_routes, gc_event_source, tests)
resolves to the exact same objects, only the definition site moved.
"""

from __future__ import annotations

from ghostopia_shared.ssrf import (
    Resolver,
    SsrfBlockedError,
    validate_mission_url,
)

__all__ = ["Resolver", "SsrfBlockedError", "validate_mission_url"]
