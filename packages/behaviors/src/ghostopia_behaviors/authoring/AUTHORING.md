# Authoring for ghostopia — the plug-and-play harness SDK

Two authoring paths, both **plug-and-play**: adding either changes what the workforce can do
while touching **neither the renderer nor the core loop** (a guarantee proven by
`tests/test_dx_property.py`).

---

## Path A — author a **Behavior** (one Python file)

A **Behavior** is the *how*: an original, modular decision unit ONE ghost runs over time.

1. **Copy** `authoring/template_behavior.py`.
2. **Rename** the class and its `name` string (e.g. `"whiteboard_draw"`). The `name` MUST be
   unique in the registry; if the behavior is a section's default, it must also equal that
   section's `role` in the map data (`maps/graveyard.sections.json`).
3. **Implement** the lifecycle against the typed `BehaviorContext`:
   - `on_start(ctx)` — set up (parse params, walk to a workstation, seed state).
   - `on_tick(ctx, dt_ms)` — advance ONE step. **NON-BLOCKING**: at most one awaited browser
     op per tick; dwell/back-off are pure timers. Never block the tick on a long GhostCrawl call.
   - `on_event(ctx, event)` — react to a normalized op-completion (`browser.error` retry, …).
   - `on_end(ctx, reason)` — tear down (release the session, walk home).
4. **Declare a `param_schema`** (a Pydantic model) + **`examples`** in the `behaviors.register`
   call's `meta`. This is the machine-readable contract the management UI **and AI** author
   against; a `TaskSpec.params` is validated against it before the behavior runs.
5. **Drop** the file under `src/ghostopia_behaviors/builtin/`. The auto-discovery loader
   (`builtin/__init__.py`) imports every non-`_` module on import, so it self-registers with
   **zero other edit**.

### The capability seam (what a behavior may touch)

A behavior receives ONLY the `BehaviorContext`:

| Field       | What it is                                                            |
|-------------|-----------------------------------------------------------------------|
| `ctx.ghost`   | the narrow `GhostHandle` (walk/play/say/overlay) — drives the visual ghost |
| `ctx.browser` | the FULL-primitive `BrowserProvider` — the ONLY path to GhostCrawl (session/nav/mouse/keyboard/page/extract/scrape/search/screenshot) |
| `ctx.world`   | the read-only `WorldQuery` (free workstations, section bounds, random reachable) |
| `ctx.emit` / `ctx.emit_event` | the normalized `Envelope` sink |
| `ctx.task` / `ctx.section` / `ctx.rng` / `ctx.log` | the assigned task, section, seeded RNG, breadcrumb sink |

A behavior gets **no** `os`/`sys`/`subprocess`/`socket`/`httpx`/SDK/keys — capability-scoped.
There is **no `import ghostcrawl`** anywhere in a behavior; secrets stay server-side.

---

## Path B — author a **Task** (declarative, no code)

A **Task** is the *what*: DATA that NAMES a vetted behavior and parameterizes it. A non-coder
or an AI authors a `*.task.json` — it is **validated, never imported or eval'd** (decode-only):

```json
{
  "title": "Research 200 companies across identities",
  "behavior": "navigate_and_extract",
  "target": { "gc_target": "cloud", "section": "extraction" },
  "identities": ["us-desktop-chrome", "de-mobile-safari"],
  "concurrency": 8,
  "params": { "kind": "extract" },
  "inputs": { "urls": ["https://example.com"], "extract_schema": { "name": "str" } }
}
```

Validation (all before anything runs — see `examples/test_recipes.py`):

1. **Parse** against `TaskSpec` — `extra='forbid'` rejects any hallucinated/unknown key (a
   smuggled `api_key` field fails validation and is never executed).
2. **Resolve** `behavior` in the live `BehaviorRegistry` — an unknown name is rejected.
3. **Validate** `params` against that behavior's `param_schema`.
4. **Dry-run**: the behavior exists, every target url passes the SSRF gate, `concurrency` ≤ the
   governor cap (50), and `identities` is a well-formed list.

See `examples/` for three complete recipes (crawl swarm, research swarm, whiteboard).

---

## The guarantee (why this is plug-and-play)

`meta` is DATA — the registry never branches on a hardcoded behavior-kind `if/elif`. So:

- adding a **Behavior** file → `behaviors.list()` gains it; the renderer/core loop is untouched.
- adding a **Task** `*.task.json` → `TaskSpec` validation accepts it; no code, no renderer edit.

`tests/test_dx_property.py` hashes an enumerated renderer/core-loop file set before and after
authoring and fails if either path reached into it.
