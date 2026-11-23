<p align="center">
  <img src="assets/screenshots/world.png" alt="ghostopia — a pixel-art town where each ghost is a real GhostCrawl session" width="760">
</p>

<h1 align="center">ghostopia</h1>

<p align="center"><strong>Move through the web like a ghost.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-blue.svg" alt="Python 3.12+">
  <a href="https://pypi.org/project/ghostcrawl/"><img src="https://img.shields.io/pypi/v/ghostcrawl.svg?label=GhostCrawl%20SDK" alt="GhostCrawl SDK on PyPI"></a>
  <a href="https://docs.ghostcrawl.io"><img src="https://img.shields.io/badge/Docs-ghostcrawl.io-blue.svg" alt="Documentation"></a>
  <a href="https://docs.ghostcrawl.io"><img src="https://img.shields.io/badge/MCP-ready-purple.svg" alt="MCP ready"></a>
  <img src="https://img.shields.io/badge/Browsers-Chrome%20%7C%20Firefox%20%7C%20WebKit-orange.svg" alt="Real browsers">
</p>

ghostopia is a **free, full, drop-in runnable harness** — the canonical example of what you can build on [GhostCrawl](https://ghostcrawl.io). It turns your GhostCrawl account into a small pixel-art town: every ghost is a real browser session that searches open catalogs, visits product pages, extracts offers, keeps **exact matches only**, and files the cheapest results for CSV or JSON export. You bring GhostCrawl (cloud or self-hosted); ghostopia is the thin program on top, and GhostCrawl does all the browsing and extraction work.

<p align="center">
  ⭐ <a href="https://github.com/ghostcrawl/ghostopia/stargazers"><strong>If ghostopia is useful to you, a star helps other developers find it.</strong></a>
</p>

<p align="center">
  <a href="https://buymeacoffee.com/ghostcrawl"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" width="180" /></a>
</p>

## Screenshots

| The workforce world | Best-price mission | On a phone |
| --- | --- | --- |
| <img src="assets/screenshots/world.png" alt="The workforce world" width="100%"> | <img src="assets/screenshots/mission.png" alt="A best-price mission" width="100%"> | <img src="assets/screenshots/offers.png" alt="Offers on a phone" width="100%"> |

## Features

- **Real sessions, real pages.** Each ghost opens a real Chrome, Firefox or WebKit session through GhostCrawl — no simulated results, no per-site scraping rules.
- **Exact matches only.** Cases, accessories and near-misses are dropped; only the product you asked for is filed.
- **Data Graveyard.** Export title, price, stock, link and image as CSV or JSON, with best-price and per-department grouping.
- **A live pixel-art workforce.** Watch ghosts wander between departments, open a live browser view, and follow a mission from search to result.
- **Cloud or self-host.** The same code talks to `https://api.ghostcrawl.io` or to your own GhostCrawl — only the base URL changes.
- **Mission recipes.** Ready-made task files in [`examples/`](examples/) show fan-out search, research swarms and whiteboard workflows.

## Quickstart

Three steps to a running app:

```bash
git clone https://github.com/ghostcrawl/ghostopia.git
cd ghostopia
cp .env.example .env
```

Edit `.env` and set the three required values:

```dotenv
GHOSTOPIA_GC_TOKEN=your-ghostcrawl-key
GHOSTOPIA_GC_BASE_URL=https://api.ghostcrawl.io
GHOSTOPIA_JWT_SECRET=$(openssl rand -hex 32)
```

Then run it:

```bash
make run
```

Open <http://localhost:8000>. `make run` installs the Python and frontend dependencies with [uv](https://docs.astral.sh/uv/) and npm, builds the web UI, and serves the UI and API on a single port. Your GhostCrawl key stays on the server; the browser authenticates with a short-lived token from `/token`.

## Configuration

Copy [`.env.example`](.env.example) to `.env`. Only three variables are required.

| Variable | Required | Secret | What it is |
| --- | --- | --- | --- |
| `GHOSTOPIA_GC_TOKEN` | yes | **yes** | Your GhostCrawl key. Read server-side only; never sent to the browser. |
| `GHOSTOPIA_GC_BASE_URL` | yes | no | GhostCrawl endpoint: cloud `https://api.ghostcrawl.io`, or your self-host URL. |
| `GHOSTOPIA_JWT_SECRET` | yes | **yes** | Long random string used to sign UI tokens. Generate with `openssl rand -hex 32`. |
| `GHOSTOPIA_DB_PATH` | no | no | Where the local SQLite results database lives (default `ghostopia.sqlite`). |
| `GHOSTOPIA_ENGINE` | no | no | Browser engine for sessions (default `chrome`). |
| `GHOSTOPIA_BYO_EXTRACT_KEY` | no | **yes** | Optional extraction model key for pages with no machine-readable product data. Leave blank to run fully on GhostCrawl. |
| `GHOSTOPIA_BYO_EXTRACT_TYPE` | no | no | Model provider type: `anthropic`, `openai`, `openai_compat` or `google`. |
| `GHOSTOPIA_BYO_EXTRACT_MODEL` | no | no | Model id; a default is chosen per provider type. |
| `GHOSTOPIA_BYO_EXTRACT_BASE_URL` | no | no | Required only for `openai` / `openai_compat`. |
| `GHOSTOPIA_TARGETS` | no | no | Optional JSON map of multiple named GhostCrawl targets. Not needed for one endpoint. |
| `GHOSTOPIA_GHOSTS_PER_SECTION` | no | no | Ghosts staffed per department (default 3). |
| `GHOSTOPIA_LIVE_BROWSER_GHOSTS` | no | no | Watchable live-browser ghosts (default 2). |

See [`.env.example`](.env.example) for the full list, including paths for authored departments, the world map and the built UI, plus concurrency and idle-teardown tuning.

### Cloud or self-host

The application code is identical — only `GHOSTOPIA_GC_BASE_URL` changes:

```dotenv
# GhostCrawl cloud
GHOSTOPIA_GC_BASE_URL=https://api.ghostcrawl.io

# Your own GhostCrawl
GHOSTOPIA_GC_BASE_URL=http://localhost:47700
```

For self-host, confirm the instance is healthy before starting ghostopia:

```bash
curl -fsS http://localhost:47700/healthz
```

See [`docs/selfhost.md`](docs/selfhost.md) for the full self-host walkthrough.

## Usage

1. Open <http://localhost:8000> and enter a product name, or paste product URLs.
2. Choose the departments you want to staff and click **find**. Ghosts fan out through GhostCrawl, search catalogs and visit real pages.
3. Results arrive in the Data Graveyard — the cheapest exact matches, grouped by department or mission.
4. Export the table as CSV or JSON with the export buttons.

You can add departments and targets at runtime in the UI; they are saved next to your results database so they survive a restart. The [mission recipes](examples/) are JSON task files you can load directly.

## Project structure

```text
ghostopia/
├── apps/
│   ├── server/src/ghostopia_server/   FastAPI app, auth, orchestration, results
│   └── web/src/                       TypeScript UI (world, HUD, editor)
├── packages/                          Python packages (behaviors, providers, world, ...)
│   ├── ghost-renderer/                Canvas renderer
│   └── ghost-art/                     Pixel-art atlas and palettes
├── assets/                            Sprites, grids, palettes, screenshots
├── maps/                              Shipped world and department maps
├── docs/                              Usage, self-host, troubleshooting
├── examples/                          Mission recipes and task files
├── Makefile                           `make run` entrypoint
├── pyproject.toml / uv.lock           Python workspace (uv)
└── package.json / package-lock.json   Frontend workspace (npm)
```

## Troubleshooting

If `make run` fails, check that Python 3.12+, a recent [uv](https://docs.astral.sh/uv/) and Node 22+ are installed. Common issues — missing `.env`, a blocked self-host URL, or an empty results table — are covered in [`docs/troubleshooting.md`](docs/troubleshooting.md). Full configuration is in [`docs/usage.md`](docs/usage.md).

## Contributing

Contributions are welcome. Fork the repo, create a branch, run `make run`, add tests for your change, and open a pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the [code of conduct](.github/CODE_OF_CONDUCT.md).

## Security

Please report security issues privately using the contact in [`SECURITY.md`](.github/SECURITY.md). Never post keys or credentials in issues or pull requests.

## License

MIT — see [`LICENSE`](LICENSE). You are free to use, modify and self-host ghostopia. If you fork it, please keep the "Powered by GhostCrawl" attribution as requested in [`NOTICE`](NOTICE).

## Support

- Docs: [docs.ghostcrawl.io](https://docs.ghostcrawl.io)
- Issues and feature requests: [GitHub issues](https://github.com/ghostcrawl/ghostopia/issues)
- GhostCrawl: [ghostcrawl.io](https://ghostcrawl.io)

Built and verified against GhostCrawl. **Powered by GhostCrawl.**
