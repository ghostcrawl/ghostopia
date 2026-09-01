# ghostopia example recipes

Three real, validated `*.task.json` recipes that prove the harness is
**general** — the same declarative Task pipeline drives crawling, autonomous research, and
full-fidelity collaborative drawing. A recipe is **DATA** that NAMES a vetted behavior; it is
parsed + validated + dry-run, never imported or `eval`'d (decode-only). See
`../packages/behaviors/src/ghostopia_behaviors/authoring/AUTHORING.md` for the authoring guide.

`test_recipes.py` is the acceptance suite: for each recipe it parses against `TaskSpec`
(`extra='forbid'`), resolves the behavior in the live registry, validates `params` against the
behavior's `param_schema`, and runs a declarative **dry-run** (behavior exists, every target
url passes an SSRF gate, `concurrency` ≤ the governor cap, `identities` is well-formed). The
crawl + research recipes additionally simulate an assignment against the in-memory
`FakeBrowserProvider` and assert the normalized `browser.*`/`result.*`/`task.*` event stream.

## Recipe 1 — `crawl-swarm.task.json` (multi-identity crawl swarm)

`navigate_and_extract` across a set of GhostCrawl identity profiles with bounded concurrency.
Each ghost opens a session under a different identity, navigates + extracts, and results flow
to the Data Graveyard. Proves identity/target selection + bounded concurrency + extraction.

## Recipe 2 — `research-swarm.task.json` (autonomous research swarm)

`scout_urls` runs `search()` → emits `task.spawned {kind:"extract", url}` → the orchestrator
routes each spawned task to the `extraction` section running `navigate_and_extract` (section
fan-out). Proves autonomous multi-step + section routing. An LLM variant swaps
`"behavior": "agent"` (`AgentBehavior`) — identical event stream, identical visuals.

## Recipe 3 — `whiteboard.task.json` (collaborative whiteboard drawing)

`whiteboard_draw` — concurrent ghosts each hold their OWN chromium session on a drawing site
and draw a stroke with a real HELD mouse drag (`mouse.down` → moves → `mouse.up`) over the
CDP-WS relay. This is the forcing function proving the primitive layer is
genuinely full-fidelity, not just scrape/extract.

**Prereqs (documented, gated):** paid tier + `cdp_passthrough_enabled` + a **chromium** engine.
On FF/WebKit / non-entitled sessions the behavior DEGRADES to discrete `cdp.input` clicks and
emits an explicit `draw_degraded` signal (never a silent no-op). The LIVE drag through the
deployed relay is verified, gated behind a paid chromium probe: if the relay filters raw `Input.*` methods, the recipe stays unit-verified +
degrade-only and an "expose raw mouse verbs on `/v1/cdp/input`" ask routes to the GhostCrawl
backlog.

## Naming note

Behavior names are sometimes written with hyphens (`navigate-and-extract`); the Python
registry + the section `role` fields use **underscores** (`navigate_and_extract`,
`scout_urls`, `whiteboard_draw`) — consistent with every shipped builtin and the `canvas`
section role in `../maps/graveyard.sections.json`. The recipes use the underscore names so
they resolve in the live registry.
