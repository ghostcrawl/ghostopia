# Using ghostopia

ghostopia 2.3.9 is a thin program on top of GhostCrawl **2.3.9**. It does not browse
or scrape by itself — it asks GhostCrawl to do the work and renders the results as a
pixel-art workforce.

## Connect to GhostCrawl

Copy `.env.example` to `.env` and set the three required values:

| Variable | What it is |
| --- | --- |
| `GHOSTOPIA_GC_TOKEN` | Your GhostCrawl key. Stays on the server; never sent to the browser. |
| `GHOSTOPIA_GC_BASE_URL` | `https://api.ghostcrawl.io` for cloud, or your self-host base URL. |
| `GHOSTOPIA_JWT_SECRET` | A long random string for the UI token (`openssl rand -hex 32`). |

Everything else is optional with defaults. Then run `make run` and open
<http://localhost:8000>.

To point ghostopia at a self-hosted GhostCrawl 2.3.9 instead of the cloud, see
[selfhost.md](selfhost.md) and the single install guide at
<https://docs.ghostcrawl.io/self-host>.

## Run a mission

1. Type a product name or paste product URLs.
2. ghostopia fans the search out through GhostCrawl; each ghost opens a real browser
   session and visits a page.
3. Results are filtered to **exact matches only** — cases, accessories and
   near-misses are dropped.
4. The Data Graveyard lists title / price / stock / link / image, sortable, with
   CSV/JSON export.

One-click examples ship preloaded: `Sony WH-1000XM5`, Nintendo Switch OLED, and
Dyson V15 Detect. More declarative recipes are in [examples/](../examples/).

## Where the browsing happens

Every navigation, extraction and session is a GhostCrawl SDK call. ghostopia holds
no browsing internals of its own. The result set you see is whatever GhostCrawl
returns for the requested targets, filtered locally for exact matches and ranked by
price.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) and Node.js.
- A GhostCrawl 2.3.9 cloud key, or a reachable GhostCrawl 2.3.9 self-host endpoint.
- Outbound access from the server (not the browser) to that endpoint.
