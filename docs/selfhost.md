# ghostopia with self-hosted GhostCrawl

ghostopia 2.3.8 works unchanged against a self-hosted GhostCrawl 2.3.8. The only
difference is the `GHOSTOPIA_GC_BASE_URL` value in `.env`.

## 1. Install GhostCrawl 2.3.8

Follow the single unified install guide — one terminal path on Linux, macOS and
Windows: [docs.ghostcrawl.io/self-host](https://docs.ghostcrawl.io/self-host).

Verify the local endpoint before wiring ghostopia to it:

```bash
curl http://localhost:47700/healthz
# {"status":"ok"}
```

## 2. Point ghostopia at it

In `.env`:

```
GHOSTOPIA_GC_BASE_URL=http://localhost:47700
GHOSTOPIA_GC_TOKEN=<your GhostCrawl key or license key>
GHOSTOPIA_JWT_SECRET=<a long random string>
```

`GHOSTOPIA_GC_TOKEN` is read by the ghostopia server only; it is never sent to the
browser. Keep `.env` out of version control.

## 3. Run ghostopia

```bash
make run          # http://localhost:8000
```

Pick a mission in the UI. Each ghost now drives the local GhostCrawl 2.3.8 instance;
all browsing and extraction happen there, and ghostopia renders the returned results.

## Notes

- The local GhostCrawl endpoint must be reachable from the ghostopia **server**
  process (same host by default). The browser never talks to it directly.
- Cloud and self-host use the same GhostCrawl SDK surface, so no code change is
  needed — only the base URL.
- See [usage.md](usage.md) for the full mission walkthrough and configuration table.
