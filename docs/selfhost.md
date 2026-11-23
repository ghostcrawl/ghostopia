# Running ghostopia against self-hosted GhostCrawl

ghostopia works the same way with GhostCrawl cloud or your own GhostCrawl instance. Only one value changes: the base URL.

## 1. Confirm your GhostCrawl is healthy

Before starting ghostopia, check that your self-hosted GhostCrawl answers a health check. For a local instance on port 47700:

```bash
curl -fsS http://localhost:47700/healthz
```

If that fails, fix the GhostCrawl instance first — ghostopia cannot run missions until its endpoint is reachable.

## 2. Point ghostopia at it

Copy the example environment and set the three required values:

```bash
cp .env.example .env
```

```dotenv
GHOSTOPIA_GC_TOKEN=your-selfhost-key
GHOSTOPIA_GC_BASE_URL=http://localhost:47700
GHOSTOPIA_JWT_SECRET=              # openssl rand -hex 32
```

Use the actual host and port of your instance if it is not on the same machine. If the UI and API are served over HTTPS, use an `https://` base URL as well.

## 3. Run the app

```bash
make run
```

Open <http://localhost:8000>. Everything else — departments, missions, exact-match filtering, the Data Graveyard and CSV/JSON export — behaves exactly as it does with the cloud endpoint.

## Switching back to cloud

Change one line and restart:

```dotenv
GHOSTOPIA_GC_BASE_URL=https://api.ghostcrawl.io
```

Your local results database is independent of the endpoint, so results from both setups remain in `GHOSTOPIA_DB_PATH`.

## Key handling

`GHOSTOPIA_GC_TOKEN` and `GHOSTOPIA_JWT_SECRET` stay on the server. The browser only receives a short-lived token minted by the app. Keep `.env` out of version control — the repository `.gitignore` already excludes it.

## Troubleshooting self-host

- **Connection refused** — the GhostCrawl port is wrong or the service is not running; re-check the healthz command above.
- **HTTP 401/403** — the key is wrong or missing for that instance.
- **TLS errors** — use the correct scheme and a certificate your host trusts.
- **Empty results** — confirm the key has permission to open sessions and that the instance can reach the target sites.

More general problems are covered in [`troubleshooting.md`](troubleshooting.md). For GhostCrawl installation itself, see [docs.ghostcrawl.io/self-host](https://docs.ghostcrawl.io/self-host).
