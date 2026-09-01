# ghostopia

**A visual scraping workforce, powered by [GhostCrawl](https://ghostcrawl.io).**

ghostopia turns your GhostCrawl account into a small pixel-art town. Every ghost
drifting around it is a real GhostCrawl session doing real work. You point a
department at a target (a product page, a category, or a search), and its ghosts
go research, extract, and bring back store-ready records: title, price, rating,
availability, a link, and a product image. You watch them work, then export the
findings.

You bring GhostCrawl (cloud or self-hosted); ghostopia is the thin program on top.
**GhostCrawl does all the browsing and scraping work** — ghostopia points a
workforce at your targets and shows you what it brings back.

See it running: **https://ghostcrawl.io/ghostopia**

## Quickstart

You need [`uv`](https://docs.astral.sh/uv/) (Python) and Node.js (for the web UI
build), plus a GhostCrawl key and endpoint.

```bash
# 1. Clone this repo (it is self-contained — no other checkout required).
git clone https://github.com/ghostcrawl/ghostopia.git
cd ghostopia

# 2. Configure ONE file: your GhostCrawl key + endpoint (and a signing secret).
cp .env.example .env
#    edit .env — set GHOSTOPIA_GC_TOKEN, GHOSTOPIA_GC_BASE_URL, GHOSTOPIA_JWT_SECRET

# 3. Run. This installs deps, builds the web UI, and serves UI + API on ONE port.
make run
```

Then open **http://localhost:8000**. The UI mints a token against the backend,
connects, and runs the default best-price example against the GhostCrawl endpoint
you configured. Watch the ghosts search, visit, extract, compare, and deliver, and
see the saved best offers.

### The one `.env`

`.env.example` documents every value. The four you must set:

| Variable | What it is |
| --- | --- |
| `GHOSTOPIA_GC_TOKEN` | Your GhostCrawl key / license key (stays on the server; never sent to the browser). |
| `GHOSTOPIA_GC_BASE_URL` | The GhostCrawl endpoint — the **local port** if you run GhostCrawl yourself (Docker / self-host, e.g. `http://localhost:8080`), or the GhostCrawl **cloud** endpoint (e.g. `https://api.ghostcrawl.io`). |
| `GHOSTOPIA_JWT_SECRET` | A long random string the server uses to mint the UI's token (e.g. `openssl rand -hex 32`). |
| `GHOSTOPIA_DB_PATH` | Where the local results database (SQLite) lives (a relative path is fine). |

Everything else in `.env.example` is optional with sensible defaults.

### Self-contained

A cloned `ghostopia` runs on its own. The GhostCrawl Python SDK ships as a
vendored wheel under `vendor/` and the dependency is pointed at it, so a plain
`uv sync` inside the folder resolves everything. `make run` does this for you. To
refresh the SDK, drop a newer wheel in `vendor/` and update the `path` in the root
`pyproject.toml` (see `vendor/README.md`).

## What ghostopia is

ghostopia is a consumer of GhostCrawl: it talks to GhostCrawl **only** through the
public GhostCrawl SDK and API. GhostCrawl is the engine that does the work;
ghostopia is the app on top that makes it visible.

- The GhostCrawl SDK and your key live **only** on the ghostopia server. The web UI
  is a thin renderer over an authenticated WebSocket and never sees a credential.
- Extraction runs on GhostCrawl's own native structured-data path by default: it
  reads the machine-readable product data a store already embeds and returns the
  full priced grid, with no model to configure and nothing third-party in the loop.
  Connecting your own model is optional, only for pages that carry no structured
  data.

## Scraping departments

Each area of the town is a **department** — a themed, configurable scraping team.
Point a department at a real target and watch its ghosts work it, then see the
priced list they bring back, on top of the shipped research → extraction → verify
pipeline.

A department can be pointed at a target in one of two ways:

- **A category URL** — e.g. a `books.toscrape.com` category. Ghosts navigate the
  category, follow same-host detail links, and extract a per-department schema
  (title, price, rating, availability). Off-host links are never followed.
- **A keyless search query** — one search runs, each result is visited, and
  title/price are scraped into the department's priced list.

Departments are authored at runtime through the authenticated gateway with a
strict schema (`target_url`, `query`, `category`, `extract_schema`). Every target
is checked before a session starts (loopback, private, and metadata addresses are
rejected), and department labels are kept on-brand. Themed examples ship out of the
box, and the default best-price example runs the moment you connect a key.

Concurrency across all departments is bounded by your plan: ghostopia reads your
`max_concurrency` from GhostCrawl as the single source of truth for how many jobs
run at once, and queues gracefully when the limit is reached.

## Layout

One folder, two toolchains — a Python backend and a thin TypeScript frontend.

**Python backend** — a `uv` workspace (`pyproject.toml`):

```
pyproject.toml            uv workspace root (members: packages/* + apps/server)
packages/<name>/          harness packages (module ghostopia_<name> under src/)
  shared/                 shared models / contract types
  event-bus/              normalized event bus
  ghost-world/            world model
  ghost-runtime/          ghost state machine + behavior runtime
  agent-runtime/          agent providers (deterministic + optional model)
  browser-provider/       browser-provider abstraction
  ghostcrawl-provider/    concrete GhostCrawl SDK provider
  orchestration/          mission → tasks → ghosts orchestrator
  core/                   shared core wiring
apps/server/              FastAPI + WebSocket server (holds the SDK + credentials)
```

**TypeScript frontend** — an npm workspace (`package.json`):

```
apps/web/                 the web app (React + Vite + PixiJS)
packages/ghost-renderer/  reusable renderer package
packages/ghost-art/       art / sprite pipeline package
```

The frontend renders server-authoritative state over an authenticated WebSocket
and shares one contract with the server (Pydantic models on the server, mirrored TS
types on the client).

## Brand

The name is always all-lowercase `ghostopia`, or all-uppercase `GHOSTOPIA` where
caps are idiomatic (the `GHOSTOPIA_*` env prefix, banners). Never title-case.

## License

ghostopia is **source-available** under the [Ghostopia Source-Available
License](LICENSE). You may run it, read it, and modify it for your own use with
GhostCrawl. You may not resell or redistribute it as your own product, and you may
not use it with any scraping service other than GhostCrawl. See [`LICENSE`](LICENSE)
for the full terms.

---

Powered by [GhostCrawl](https://ghostcrawl.io) — the all-in-one web-data API for
crawling, scraping, structured extraction, and browser automation.
