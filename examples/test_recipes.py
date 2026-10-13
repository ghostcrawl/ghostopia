"""Acceptance suite for the three example recipes (RESEARCH-3 §6, decode-only).

For EACH recipe (`crawl-swarm`, `research-swarm`, `whiteboard`):
  1. parse the `*.task.json` against `TaskSpec` (`extra='forbid'` — hallucinated keys rejected);
  2. resolve `spec.behavior` in the LIVE `BehaviorRegistry`;
  3. validate `spec.params` against that behavior's `param_schema`;
  4. run a declarative DRY-RUN — behavior exists, every target url passes a fake SSRF gate,
     `concurrency` ≤ the governor cap (50), `identities` is a well-formed list — asserting it
     passes with NO author-supplied code executed (a recipe is DATA, never `eval`'d).

The crawl + research recipes additionally SIMULATE an assignment against the in-memory
`FakeBrowserProvider` and assert the normalized event sequence. The whiteboard recipe's held-
stroke drive is unit-verified in `packages/behaviors/tests/test_whiteboard_draw.py`; its LIVE
drag is probe-gated (Task 3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import ghostopia_behaviors.builtin  # noqa: F401 — auto-discover every shipped behavior
import pytest
from ghostopia_behaviors import behaviors
from ghostopia_behaviors.behavior import BehaviorContext
from ghostopia_behaviors.builtin.whiteboard_draw_behavior import WhiteboardDrawParams
from ghostopia_browser_provider import FakeBrowserProvider
from ghostopia_shared import Envelope, Point, Task, TaskSpec, WorldQuery
from pydantic import ValidationError

_EXAMPLES_DIR = Path(__file__).resolve().parent
_RECIPES = {
    "crawl": _EXAMPLES_DIR / "crawl-swarm.task.json",
    "research": _EXAMPLES_DIR / "research-swarm.task.json",
    "whiteboard": _EXAMPLES_DIR / "whiteboard.task.json",
}

_CONCURRENCY_CAP = 50


# --------------------------------------------------------------------------------------
# Minimal test doubles (examples/ is not on the behaviors-tests sys.path).
# --------------------------------------------------------------------------------------


class _Ghost:
    """A no-op spying GhostHandle sufficient to run a behavior to completion."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._pos = Point(x=0.0, y=0.0)

    def walk_to(self, dest: Point) -> None:
        self.calls.append("walk_to")

    def walk_to_workstation(self) -> None:
        self.calls.append("walk_to_workstation")

    def walk_home(self) -> None:
        self.calls.append("walk_home")

    def face_browser(self) -> None:
        self.calls.append("face_browser")

    def play_work(self) -> None:
        self.calls.append("play_work")

    def play_error(self) -> None:
        self.calls.append("play_error")

    def play_success(self) -> None:
        self.calls.append("play_success")

    def say(self, text: str, kind: str = "info") -> None:
        self.calls.append("say")

    def set_overlay(self, kind: str) -> None:
        self.calls.append("set_overlay")

    def is_idle(self) -> bool:
        return True

    def is_walking(self) -> bool:
        return False

    def at_workstation(self) -> bool:
        return True

    def position(self) -> Point:
        return self._pos


class _Emit:
    def __init__(self) -> None:
        self.events: list[Envelope] = []

    async def __call__(self, envelope: Envelope) -> None:
        self.events.append(envelope)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]


# --------------------------------------------------------------------------------------
# A fake SSRF gate (the real one is /21). Blocks loopback/link-local/private hosts
# and non-http schemes; a public https target passes. Decode-only: never fetches anything.
# --------------------------------------------------------------------------------------

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "metadata.google.internal"}
_BLOCKED_PREFIXES = ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "169.254.")


def _ssrf_ok(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTS or host.endswith(".internal"):
        return False
    return not host.startswith(_BLOCKED_PREFIXES)


# --------------------------------------------------------------------------------------
# The declarative dry-run (decode-only): parse → resolve → validate params → gate.
# --------------------------------------------------------------------------------------


def _load_spec(path: Path) -> TaskSpec:
    return TaskSpec.model_validate_json(path.read_text())


def _dry_run(spec: TaskSpec) -> dict[str, Any]:
    """Validate a recipe WITHOUT executing any behavior code. Raises on any failure."""
    # (2) behavior resolves in the live registry
    reg = behaviors.get(spec.behavior)  # KeyError if unknown
    # (3) params validate against the behavior's own param_schema
    reg.meta.param_schema.model_validate(spec.params)
    # (4a) every target url passes the SSRF gate
    urls = list(spec.inputs.urls or [])
    blocked = [u for u in urls if not _ssrf_ok(u)]
    if blocked:
        raise ValueError(f"SSRF-blocked target(s): {blocked}")
    # (4b) concurrency within the governor cap (TaskSpec also enforces le=50)
    if not (1 <= spec.concurrency <= _CONCURRENCY_CAP):
        raise ValueError(f"concurrency {spec.concurrency} out of bounds")
    # (4c) identities shape
    if spec.identities is not None and not all(isinstance(i, str) for i in spec.identities):
        raise ValueError("identities must be a list[str]")
    return {"behavior": spec.behavior, "targets_ok": len(urls), "concurrency": spec.concurrency}


def _task_from_spec(spec: TaskSpec) -> Task:
    """Map a declarative recipe into a runtime Task for a Fake-provider simulation.

    Mirrors the manager seam: the behavior's params carry the merged inputs (the behaviors
    read urls/query from ``task.params``), and the first url seeds ``task.target``.
    """
    urls = list(spec.inputs.urls or [])
    params: dict[str, Any] = {**spec.params, "ghost_id": "g-sim"}
    if urls:
        params.setdefault("urls", urls)
    if spec.inputs.extract_schema is not None:
        params.setdefault("extract_schema", spec.inputs.extract_schema)
    if spec.inputs.query is not None:
        params.setdefault("query", spec.inputs.query)
    kind = spec.params.get("kind")
    return Task(
        id="sim-task",
        kind=kind if isinstance(kind, str) else spec.behavior,
        target={"url": urls[0]} if urls else {},
        params=params,
        inputs=spec.inputs.model_dump(),
    )


async def _simulate(spec: TaskSpec) -> _Emit:
    """Run the recipe's behavior to completion against the Fake provider (no network)."""
    ghost, browser, emit = _Ghost(), FakeBrowserProvider(), _Emit()
    # world is unused by these behaviors (they touch only ctx.browser/ctx.ghost).
    ctx = BehaviorContext(
        ghost=ghost,
        browser=browser,
        world=cast(WorldQuery, None),
        emit=emit,
        task=_task_from_spec(spec),
    )
    behavior = behaviors.create(spec.behavior)
    await behavior.on_start(ctx)
    for _ in range(300):
        if getattr(behavior, "is_done", False):
            break
        await behavior.on_tick(ctx, dt_ms=60.0)
    assert getattr(behavior, "is_done", False), f"{spec.behavior} did not finish in budget"
    return emit


# --------------------------------------------------------------------------------------
# Parse + dry-run acceptance (all three recipes)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_RECIPES))
def test_recipe_parses_resolves_and_dry_runs(key: str) -> None:
    spec = _load_spec(_RECIPES[key])
    assert spec.title  # a real title
    assert spec.behavior in behaviors.names()  # names a vetted behavior
    report = _dry_run(spec)  # DATA validated + gated, NO code executed
    assert report["behavior"] == spec.behavior
    assert 1 <= report["concurrency"] <= _CONCURRENCY_CAP


def test_recipe_extra_forbid_rejects_smuggled_key() -> None:
    # A recipe with a smuggled `api_key` field is rejected by extra='forbid' (never executed).
    raw = _RECIPES["crawl"].read_text().rstrip().rstrip("}")
    smuggled = raw + ', "api_key": "sk-should-be-rejected" }'
    with pytest.raises(ValidationError):
        TaskSpec.model_validate_json(smuggled)


def test_dry_run_blocks_ssrf_target() -> None:
    # The dry-run's SSRF gate rejects a metadata/loopback target before any dispatch.
    spec = _load_spec(_RECIPES["crawl"]).model_copy(deep=True)
    spec.inputs.urls = ["http://169.254.169.254/latest/meta-data/"]
    with pytest.raises(ValueError, match="SSRF"):
        _dry_run(spec)


def test_whiteboard_recipe_targets_canvas_and_carries_strokes() -> None:
    spec = _load_spec(_RECIPES["whiteboard"])
    assert spec.behavior == "whiteboard_draw"
    assert spec.target.section == "canvas"
    params = WhiteboardDrawParams.model_validate(spec.params)
    assert len(params.strokes) >= 1
    assert params.strokes[0][0].x == 120.0  # parsed to Point


# --------------------------------------------------------------------------------------
# Simulated assignment (crawl + research) — normalized event sequence over the Fake provider
# --------------------------------------------------------------------------------------


async def test_crawl_swarm_simulation_emits_normalized_sequence() -> None:
    spec = _load_spec(_RECIPES["crawl"])
    emit = await _simulate(spec)
    for expected in ("browser.session_opened", "browser.navigate", "result.scraped", "task.completed"):
        assert expected in emit.types, f"missing {expected} in {emit.types}"
    # a session opened before the first navigate before the first scrape
    assert emit.types.index("browser.session_opened") < emit.types.index("browser.navigate")
    assert emit.types.index("browser.navigate") < emit.types.index("result.scraped")


async def test_research_swarm_simulation_spawns_extraction_tasks() -> None:
    spec = _load_spec(_RECIPES["research"])
    emit = await _simulate(spec)
    spawned = [e for e in emit.events if e.type == "task.spawned"]
    assert spawned, "research swarm should spawn extraction tasks"
    assert all(e.payload.get("kind") == "extract" for e in spawned)
    assert all(e.payload.get("url") for e in spawned)
