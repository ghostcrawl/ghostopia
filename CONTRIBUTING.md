# Contributing to ghostopia

Thanks for helping make ghostopia better. Contributions of code, docs, recipes and bug reports are all welcome.

## Getting set up

1. Fork the repository and clone your fork.
2. Copy `.env.example` to `.env` and set the three required values (a GhostCrawl key, base URL and JWT secret).
3. Install prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 22+.
4. Run the app:

   ```bash
   make run
   ```

   Open <http://localhost:8000>. If you prefer to work on the frontend alone, run `npm run dev`.

## Making a change

1. Create a branch from `main`: `git checkout -b my-change`.
2. Keep changes focused. Add or update tests for behavior changes.
3. Run the checks before opening a PR:

   ```bash
   npm run typecheck
   npm run test:unit
   uv run pytest
   ```

4. Commit with a clear, plain message and push to your fork.
5. Open a pull request describing what changed and how you verified it.

## Conventions

- Python: follow the existing style in `packages/` and `apps/server/`; `ruff` and `mypy` configuration is in the repo root.
- TypeScript: keep types explicit at module boundaries; run `npm run typecheck`.
- Use neutral, factual wording in code, docs and UI copy. No internal project names, ticket numbers or credentials in shipped text.
- Never commit `.env`, database files, build output or secrets.
- New mission recipes belong in `examples/` with a short README entry.

## Code of conduct

By participating you agree to the [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Security

Do not open a public issue for a security problem. Follow [SECURITY.md](.github/SECURITY.md) to report it privately.
