# ghostopia-behavior-executor

The **trust/isolation model** as a small, tested Python package.

User/AI-authored behaviors run **server-side**, driving real browsers with real GhostCrawl
keys. So the platform cannot hand a behavior the raw host — it hands it a bounded context and
runs it under limits. This package delivers **Layers 1 and 2** of the layered trust model.

## The layered trust model

| Layer | What | Where |
|-------|------|-------|
| **Layer 0** | Declarative tasks = **no code** (the safest authoring surface) | authoring layer |
| **Layer 1** | **Capability-scoping** (always on): a behavior only ever receives a bounded context | this package — `capability_scope.py` + `guarded_provider.py` |
| **Layer 2** | **Pluggable Executor** with resource/time limits | this package — `executor.py` + `in_process_executor.py` |

### Layer 1 — capability-scoped context (always on)

`build_capability_scoped_context(...)` returns a `BehaviorContext` whose **own attribute
surface is exactly** `{ghost, browser, world, emit, task, section, rng, log}` — and nothing
else. There is **no `os` / `sys` / `socket` / `subprocess` / `httpx` / raw SDK / GhostCrawl key**
reachable from the context. `rng` is a seeded `random.Random` (deterministic
per seed, independent of the process-global RNG); `emit` / `log` are the injected callbacks.

`guard_browser_provider(provider, is_url_allowed)` enforces the **SSRF gate at the handle**:
every URL a behavior navigates — `nav.goto`, `open`, `create_session`,
`scrape` — passes the **injected** validator **before** the wrapped provider is touched. A
blocked URL (private / loopback / link-local / `169.254.169.254` metadata / non-`http(s)`) never
reaches the real provider (and thus never reaches GhostCrawl); an allowed URL delegates
unchanged. The validator is **injected** (`is_url_allowed(url) -> bool | str`; `True` allows,
anything else blocks with the reason) so this package stays **pure** — it imports neither
`apps/server`'s `ssrf` module nor the SDK. The server wires the real `validate_mission_url`
gate; tests wire a fake.

### Layer 2 — pluggable Executor (wall-clock + tick deadlines)

`Executor` is a `Protocol`: `async run(behavior, ctx, limits) -> RunResult`. It drives
`on_start` → `on_tick` / `on_event` → `on_end`, calls `on_end` **exactly once** (with the
ctx's browser session released) on every terminal path, and returns a `RunResult`
(`outcome ∈ {completed, failed, cancelled, timed_out}` + `ticks` + `tick_overruns`).

`InProcessExecutor` is **v0**: an in-process, capability-scoped asyncio executor. It enforces
two limits so no behavior can wedge the loop:

- **wall-clock budget** (`RunLimits.wall_clock_ms`) — the whole run is bounded; running the
  full allotted time is normal `completed`, while a hook that **hangs** past the budget ends the
  run `timed_out` (the behavior sees `on_end("failed")`; the caller sees `"timed_out"`).
- **tick deadline** (`RunLimits.tick_deadline_ms`) — each `on_tick` runs as its own task under
  a watchdog; a tick that overruns is **cancelled, flagged (`tick_overruns`), and NOT awaited**
  — the loop moves on. A slow/greedy tick can never block the executor.

## The no-rewrite hardening path

The whole point of the `Executor` seam is that **hardening needs no author-facing change**.
v0 is in-process — honest for **operator-authored**
behaviors (the current, lower-risk population). When untrusted authorship arrives, the first
hardening step is a **`SubprocessExecutor` / separate-interpreter executor** that marshals the
capability-scoped ctx across a process boundary. That is an **Executor-implementation swap
only**:

- the `Behavior` contract (`on_start` / `on_tick` / `on_event` / `on_end`) is **untouched**;
- `build_capability_scoped_context` is **untouched**;
- the call site swaps `InProcessExecutor()` for `SubprocessExecutor()` and nothing else.

The test suite proves this by running the **same** scripted behavior through a second trivial
stub `Executor` implementing the same seam. **No subprocess/isolate executor is built in v0** —
it is the documented deferred path, and this package adds **no** native-isolate dependency.

The swap only works if **behaviors are pure with respect to the injected ctx**:
a behavior with a module-level side effect or a ctx-external reach (global state,
direct network, host access) is **isolate-hostile** and violates the contract — it cannot be
marshalled across a process boundary. Reaching the outside world **only** through the ctx is a
hard contract requirement, not a style preference.

## ⚠ Python `eval` / `exec` are NOT a security boundary

This executor **never** `eval`s / `exec`s authored or LLM output, and grants a behavior **no**
way to run arbitrary host code. Authored / LLM output is **DATA** —
validated and decode-only. Isolation comes from capability-scoping (Layer 1)
plus the executor seam (Layer 2 → subprocess/separate-interpreter), **not** from trying to
sandbox `eval`. A sandboxed interpreter-in-the-same-process is not a boundary and is not used.

## Testing

```bash
cd ghostopia && uv run pytest packages/behavior-executor
```
