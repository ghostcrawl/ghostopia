# vendor/

This folder makes a downloaded `ghostopia/` **self-contained** — it installs and runs
with a plain `uv sync` from inside the folder, with no sibling directories required.

## `ghostcrawl-2.3.6-py3-none-any.whl`

The GhostCrawl Python SDK (version `2.3.6`), vendored as a wheel. `ghostopia` is a thin
consumer of GhostCrawl: the server holds this SDK (and every credential) and talks to a
GhostCrawl endpoint you configure in `.env` (`GHOSTOPIA_GC_BASE_URL` + `GHOSTOPIA_GC_TOKEN`).
GhostCrawl does all the browsing/scraping work; ghostopia is the program on top.

The root `pyproject.toml` points the `ghostcrawl` dependency at this wheel via
`[tool.uv.sources]`, so `uv sync` resolves the SDK from here rather than from an
out-of-folder path or from PyPI. This pins the exact SDK build ghostopia was tested against.

### Updating the wheel

Drop a newer `ghostcrawl-<version>-py3-none-any.whl` in here, update the `path` in the root
`pyproject.toml` `[tool.uv.sources]` to match the new filename, then run `uv lock` to refresh
`uv.lock`.
