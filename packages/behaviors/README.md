# ghostopia-behaviors

The first-class **dynamic Behavior system**. A ghost runs ONE active `Behavior` — a
pluggable tick/event module that decides what it does over time, drives the visible ghost
through the narrow `GhostHandle`, and reaches GhostCrawl **only** through the full-primitive
`ctx.browser` (never the SDK, never a secret), emitting the same normalized
`ghost.*`/`browser.*`/`task.*`/`result.*` event stream.

## Author a behavior in ONE file

1. Drop a module under `src/ghostopia_behaviors/builtin/` (e.g. `my_thing.py`).
2. Define a class with `name` + async `on_start` / `on_tick` / `on_event` / `on_end`.
3. Self-register at import time with capability **meta as data**:

```python
from pydantic import BaseModel
from ghostopia_behaviors.registry import BehaviorMeta, behaviors


class MyThingParams(BaseModel):
    target: str


class MyThing:
    name = "my_thing"

    async def on_start(self, ctx): ...
    async def on_tick(self, ctx, dt_ms): ...      # NON-BLOCKING: ≤1 awaited op/tick
    async def on_event(self, ctx, event): ...
    async def on_end(self, ctx, reason): ...


behaviors.register(
    "my_thing",
    MyThing,
    BehaviorMeta(
        kind="deterministic",
        needs=["browser"],
        label="My Thing",
        param_schema=MyThingParams,      # AI + humans author against this
        examples=[{"title": "example", "params": {"target": "https://example.com"}}],
        overlay="work",
    ),
)
```

That's it. The `builtin/__init__.py` **auto-discovery loader** imports every module in the
directory on package import, so **dropping the file registers it** — the renderer and the
core loop are **never touched**. They read `meta.overlay`/`meta.label` as data and drive the
ghost via the handle.

## The contract

- `BehaviorContext` (`behavior.py`) carries `{ ghost, browser, world, emit, task, section,
  rng, log }`. `browser` is the **full-primitive** `BrowserProvider` — a
  behavior can do anything a browser can, and nothing else (no `fs`/`net`/`child_process`/
  keys/raw SDK). `ctx.emit_event(type, payload)` builds + publishes a normalized `Envelope`.
- `BehaviorRegistry` (`registry.py`) — `register(name, factory, meta)` / `create(name)`
  (fresh instance per assignment) / `get` / `list` (meta incl. `param_schema`/`examples`).
  Meta is **data**; nothing branches on a hardcoded behavior-kind switch.

## Built-ins

| name | kind | what it does |
|------|------|--------------|
| `navigate_and_extract` | deterministic | walk → open session → navigate → dwell → extract → next url; pushes discovered urls; bounded retry; walk home |
| `idle_wander` | ambient | on a dwell timer, walk to a random reachable tile in the section bounds; emit `ghost.wander` |
| `scout_urls` | deterministic | search/seed via `ctx.browser`; emit `task.spawned{kind:"extract"}` per discovered url |
| `verify` | deterministic | re-scrape a sample; emit `result.verified{ok}` + `play_success`/`play_error` |
| `agent` | llm | `AgentBehavior` adapter — runs ANY `AgentProvider` (deterministic OR LLM) behind THIS one contract, emitting the SAME normalized sequence |

`AgentBehavior` is why both brains express as Behaviors behind ONE contract: the world
cannot tell which brain runs. The concrete provider is chosen by the composition layer
via `select_agent_provider`, never inside a behavior.
