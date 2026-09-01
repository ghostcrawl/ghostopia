"""``ghostopia-behavior-executor`` — the trust/isolation model as a small package.

Three concerns, one per module:

* :mod:`capability_scope` — Layer 1 (always-on): :func:`build_capability_scoped_context`
  builds the bounded :class:`BehaviorContext` (ghost/browser/world/emit/task/section/rng/log
  ONLY — never host/SDK/keys).
* :mod:`guarded_provider` — :func:`guard_browser_provider` enforces the SSRF gate AT the
  handle so every navigated URL is validated before dispatch.
* :mod:`executor` / :mod:`in_process_executor` — Layer 2: the pluggable :class:`Executor`
  seam (wall-clock + tick deadlines) and its v0 :class:`InProcessExecutor`. Swapping to a
  subprocess/isolate executor is an executor-package change with ZERO behavior-contract
  change. Python ``eval``/``exec`` are NOT a security boundary and are not
  used for isolation.
"""

from __future__ import annotations

from ghostopia_behavior_executor.capability_scope import build_capability_scoped_context
from ghostopia_behavior_executor.executor import Executor, RunLimits, RunOutcome, RunResult
from ghostopia_behavior_executor.guarded_provider import (
    SsrfBlockedUrlError,
    UrlValidator,
    guard_browser_provider,
)
from ghostopia_behavior_executor.in_process_executor import InProcessExecutor

__all__ = [
    "build_capability_scoped_context",
    "guard_browser_provider",
    "SsrfBlockedUrlError",
    "UrlValidator",
    "Executor",
    "RunLimits",
    "RunOutcome",
    "RunResult",
    "InProcessExecutor",
]
