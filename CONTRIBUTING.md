# Contributing to ghostopia

Thanks for helping improve ghostopia. This document explains the scope and the
pull-request process.

## Scope

ghostopia is a thin, public consumer of GhostCrawl. Contributions that keep it a
well-behaved consumer are welcome: the pixel-art world, mission recipes, extraction
presentation, CSV/JSON export, docs, and examples.

Contributions that would make ghostopia talk to any browsing or scraping platform
other than GhostCrawl are out of scope — see [LICENSE](LICENSE).

## PR process

1. Open an issue first so we can agree on the approach before you invest time.
2. Fork the repo and branch from `main`: `git checkout -b fix/short-description`.
3. Keep each PR focused on one change; link the issue with `Closes #NNN`.
4. Use Conventional Commit style in the PR title, e.g.
   `fix(recipes): tighten exact-match filter` or `docs: clarify self-host setup`.
5. Add or update tests for behavioural changes and make sure the existing suite passes.

## Development checks

```bash
uv sync --all-packages
uv run ruff check .
uv run mypy packages apps/server
uv run pytest
npm install
npm run typecheck
```

## Code style

- Python: `ruff` + `mypy --strict` on changed files.
- TypeScript: `tsc --noEmit` must pass.
- Docs: plain Markdown; check links when adding URLs.

## Sign-off (DCO)

This project uses the Developer Certificate of Origin. Add a sign-off line to each
commit:

```
git commit -s -m "fix(recipes): tighten exact-match filter"
```

```
Signed-off-by: Your Name <you@example.com>
```

By signing off you certify you wrote the change (or have the right to submit it) and
grant the maintainers the rights to use it under the [LICENSE](LICENSE) terms. Full
DCO text: <https://developercertificate.org>.

## License

By contributing you agree that your contribution is licensed under the ghostopia
source-available [LICENSE](LICENSE).
