# Using ghostopia

This guide covers configuration, a typical mission, and exporting results. Start with the [quickstart in the README](../README.md#quickstart) if you have not run the app yet.

## Configuration

ghostopia reads a single `.env` file at boot. Copy the example and fill it in:

```bash
cp .env.example .env
```

### Required values

| Variable | Description |
| --- | --- |
| `GHOSTOPIA_GC_TOKEN` | Your GhostCrawl key. It is resolved and used **server-side only** and is never sent to the browser. |
| `GHOSTOPIA_GC_BASE_URL` | GhostCrawl endpoint. Cloud: `https://api.ghostcrawl.io`. Self-host: your own URL, for example `http://localhost:47700`. |
| `GHOSTOPIA_JWT_SECRET` | A long random string used to sign the short-lived token the UI authenticates with. Generate one with `openssl rand -hex 32`. |

### Commonly adjusted values

| Variable | Default | Description |
| --- | --- | --- |
| `GHOSTOPIA_DB_PATH` | `ghostopia.sqlite` | Local SQLite database holding results, missions and authored departments. |
| `GHOSTOPIA_ENGINE` | `chrome` | Browser engine used for GhostCrawl sessions. |
| `GHOSTOPIA_BYO_EXTRACT_KEY` | empty | Optional extraction model key for pages without machine-readable product data. Leave empty to rely on GhostCrawl's own structured extraction. |
| `GHOSTOPIA_BYO_EXTRACT_TYPE` | `anthropic` | Provider type: `anthropic`, `openai`, `openai_compat` or `google`. |
| `GHOSTOPIA_BYO_EXTRACT_MODEL` | provider default | Override the model id. |
| `GHOSTOPIA_BYO_EXTRACT_BASE_URL` | empty | Required only for `openai` / `openai_compat`. |
| `GHOSTOPIA_TARGETS` | empty | JSON map of multiple named endpoints. Not needed for a single GhostCrawl target. |
| `GHOSTOPIA_GHOSTS_PER_SECTION` | `3` | Number of ghosts staffed per department. |
| `GHOSTOPIA_LIVE_BROWSER_GHOSTS` | `2` | Number of ghosts with a watchable live browser view. |

The full list, including map/UI paths and idle-teardown tuning, is in [`.env.example`](../.env.example).

## Running a mission

1. Start the app with `make run` and open <http://localhost:8000>.
2. In the mission form, type a product name, or paste a list of product URLs.
3. Optionally pick a query/category mode and the departments to staff.
4. Click **find**. The workforce searches open catalogs and visits real pages through GhostCrawl. Each ghost appears in the world and its state is reflected live in the roster and inspector.
5. When the mission completes, open the **Data Graveyard** to review the filed offers.

### Exact matching

ghostopia deliberately keeps **exact product matches only**. Cases, accessories and near-misses are filtered out, so the results table reflects the item you asked for rather than loosely related products. Offers are grouped by department or mission, and the lowest priced current offer is marked as the best.

### Live view

A limited number of ghosts expose a live browser view. Select a ghost in the inspector to watch its page while it works. The live view starts only when you open it and does not change how missions run.

## Data Graveyard and export

The Data Graveyard lists each filed record with title, price, stock, link and image. Use the export buttons to save the current view as:

- **CSV** — for spreadsheets and further analysis.
- **JSON** — for scripts and pipelines.

Exports reflect the exact-match, best-price filtering applied in the UI, so what you save matches what you see.

## Recipes and task files

[`examples/`](../examples/) contains ready-made JSON task files and a short README:

- `crawl-swarm.task.json` — fan out a crawl across many targets.
- `research-swarm.task.json` — split a research question across departments.
- `whiteboard.task.json` — a whiteboard-style workflow.

The [`examples/recipes/`](../examples/recipes/) directory also has small standalone Python and TypeScript snippets showing direct GhostCrawl SDK usage — useful as a starting point for your own harness.

## Cloud and self-host are the same app

There is no mode flag. Change only `GHOSTOPIA_GC_BASE_URL` to switch between GhostCrawl cloud and your own instance; all application code, UI, and results handling are identical. See [`selfhost.md`](selfhost.md).

## Where data is stored

Everything ghostopia authors — results, missions and runtime departments — lives in the local SQLite database at `GHOSTOPIA_DB_PATH`. Back up that file to keep your results. Deleting it starts a fresh workspace. No data is sent anywhere except the GhostCrawl endpoint you configure.

## More help

See [`troubleshooting.md`](troubleshooting.md) for common problems, or open an issue if something is not covered.
