# Troubleshooting

Common problems and their fixes. For full configuration see [`usage.md`](usage.md); for self-host setup see [`selfhost.md`](selfhost.md).

## `make run` fails at install

- **Python version** — ghostopia needs Python 3.12 or newer. Check with `python3 --version`.
- **uv missing** — install [uv](https://docs.astral.sh/uv/) and make sure `uv` is on your `PATH`.
- **Node version** — use Node 22 or newer (`node --version`). The frontend build needs npm, which ships with Node.
- **npm install errors** — remove `node_modules` and retry; the canonical lockfile is `package-lock.json`.

## "No .env found"

`make run` requires a `.env` file. Create it with:

```bash
cp .env.example .env
```

Then set `GHOSTOPIA_GC_TOKEN`, `GHOSTOPIA_GC_BASE_URL` and `GHOSTOPIA_JWT_SECRET`.

## Server starts but the page is blank

`make run` builds the UI into `apps/web/dist` before serving. If you changed frontend code outside `make run`, rebuild it:

```bash
cd apps/web && npx vite build
```

Then restart the server and hard-refresh the browser.

## Missions produce no results

- **Key or endpoint** — confirm `GHOSTOPIA_GC_TOKEN` is valid for the endpoint in `GHOSTOPIA_GC_BASE_URL`. For self-host, check `curl -fsS <base-url>/healthz`.
- **Exact matching** — near-misses, cases and accessories are intentionally filtered out. Try a more specific product name.
- **No machine-readable data** — some sites do not publish structured product data. GhostCrawl's own extraction still tries the page; optionally set `GHOSTOPIA_BYO_EXTRACT_KEY` to add an extraction model.
- **Endpoint reachability** — the GhostCrawl endpoint must be able to reach the target sites.

## "Connection refused" or TLS errors on self-host

Check the base URL scheme and port, and that the service is running. See the healthz check in [`selfhost.md`](selfhost.md).

## Live browser view does not open

Only a limited number of ghosts (`GHOSTOPIA_LIVE_BROWSER_GHOSTS`, default 2) expose a live view. Select a ghost that carries the live affordance in the roster or inspector.

## Auth or 401 responses from the UI

The browser token is short-lived. Reload the page so the app mints a fresh one. If the problem persists, confirm `GHOSTOPIA_JWT_SECRET` is set and consistent across restarts.

## Where did my results go?

Results, missions and authored departments live in the SQLite file at `GHOSTOPIA_DB_PATH` (default `ghostopia.sqlite` in the project root). If the file is missing or was deleted, ghostopia starts with a fresh workspace. Keep a backup if you want to retain results across reinstalls.

## Still stuck

Open an issue with the steps to reproduce, the ghostopia version and relevant logs. **Remove keys and tokens before pasting logs.** For security issues, use the private reporting link in [SECURITY.md](../.github/SECURITY.md) instead of a public issue.
