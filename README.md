# ghostopia

![ghostopia — a pixel-art town where each ghost is a real GhostCrawl session bringing back product data](assets/screenshots/world.png)

**A visual scraping workforce, powered by [GhostCrawl](https://ghostcrawl.io).**

ghostopia turns your GhostCrawl account into a small pixel-art town. Every ghost
is a real GhostCrawl browser session. Type a product name (or paste product URLs),
and the workforce searches, visits pages, extracts offers, keeps **exact matches
only**, and files the cheapest results for CSV/JSON export.

You bring GhostCrawl (cloud or self-hosted); ghostopia is the thin program on top.
**GhostCrawl does all the browsing and scraping work** — ghostopia points a
workforce at your targets and shows you what it brings back. Built and verified
against **GhostCrawl 2.3.9**.

- Marketing preview (facade demo): [ghostcrawl.io/ghostopia](https://ghostcrawl.io/ghostopia)
- Story: [Ghostopia: Your Scraping Workforce](https://ghostcrawl.io/blog/ghostopia-your-scraping-workforce/)

## Screenshots

| The workforce world | Best-price mission | On a phone |
| --- | --- | --- |
| ![world](assets/screenshots/world.png) | ![mission](assets/screenshots/mission.png) | ![mobile](assets/screenshots/offers.png) |

## Try it

The interactive workforce UI is the marketing preview at
[ghostcrawl.io/ghostopia](https://ghostcrawl.io/ghostopia) — use your GhostCrawl
key there. This public repository ships docs, screenshots, `.env.example`, and
mission recipes under [examples/](examples/); the full self-host application
tree is not published in this facade yet.

When you are ready to wire GhostCrawl yourself:

1. Create a cloud or self-host key ([docs.ghostcrawl.io/self-host](https://docs.ghostcrawl.io/self-host)).
2. Copy [`.env.example`](.env.example) and set `GHOSTOPIA_GC_TOKEN` + `GHOSTOPIA_GC_BASE_URL`.
3. Follow [docs/usage.md](docs/usage.md) and the recipes in [examples/](examples/).

### What you type vs what you get

| You do | ghostopia does |
| --- | --- |
| Type a product name | Fan-out search across open catalogs via GhostCrawl |
| Or paste product URLs | Visit each page in a real browser and extract fields |
| Click find | Keep **exact** matches only; drop cases / accessories / near-misses |
| Open Data Graveyard | Export title / price / stock / link / image as CSV or JSON |

### The one `.env`

| Variable | What it is |
| --- | --- |
| `GHOSTOPIA_GC_TOKEN` | Your GhostCrawl key (stays on the server; never sent to the browser). |
| `GHOSTOPIA_GC_BASE_URL` | GhostCrawl endpoint — cloud `https://api.ghostcrawl.io`, or your own self-host (e.g. `http://localhost:47700`). |
| `GHOSTOPIA_JWT_SECRET` | Long random string for UI tokens (`openssl rand -hex 32`). |

Everything else in `.env.example` is optional with sensible defaults. See
[docs/usage.md](docs/usage.md) for the full configuration and mission walkthrough.

## GhostCrawl dependency

ghostopia is a thin program on top of GhostCrawl. Install the public SDK with
`pip install ghostcrawl==2.3.9`. See [docs/selfhost.md](docs/selfhost.md) to point
at a self-hosted GhostCrawl 2.3.9 instead of the cloud.

## Powered by GhostCrawl

Every ghost is a real GhostCrawl session: Chrome, Firefox, or WebKit, coherent
browser identity, structured extraction. If you like what you see, start at
[ghostcrawl.io](https://ghostcrawl.io), the
[API docs](https://docs.ghostcrawl.io), or the single self-host install guide at
[docs.ghostcrawl.io/self-host](https://docs.ghostcrawl.io/self-host).

## Examples

Runnable mission recipes live in [examples/](examples/).

## Contributing & Security

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Please report
security issues privately per [SECURITY.md](.github/SECURITY.md).

## License

Source-available — see [LICENSE](LICENSE). You may use, modify, and self-host
ghostopia for your own data needs; keep the "Powered by GhostCrawl" attribution.
