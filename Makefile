# ghostopia — one-command run recipe.
#
# Quickstart:
#   cp .env.example .env      # then fill in the four required values
#   make run                  # installs deps, builds the UI, serves it + the API on ONE port
#
# ghostopia is a thin program on top of GhostCrawl: GhostCrawl does the browsing/scraping
# work; this app points a workforce at your targets and shows what it brings back. `make run`
# builds the web UI into apps/web/dist and boots the server, which serves that built UI and its
# authed API on the SAME port — open http://localhost:$(PORT) and you are running.

# Host/port the single server binds. Override: `make run PORT=9000`.
HOST ?= 127.0.0.1
PORT ?= 8000

# The server app factory — the operator run entrypoint: the real GhostCrawl live app PLUS the
# clean lifecycle (when the last viewer disconnects, the whole workforce is torn down and every
# live GhostCrawl session released after a short refresh-tolerant grace, so a closed tab never
# leaves ghosts looping and saturating the account's concurrency cap). `create_live_app` is the
# bare library app WITHOUT that disconnect-teardown — do not run it directly.
APP := ghostopia_server.workforce_app:create_workforce_app

.DEFAULT_GOAL := run
.PHONY: run install install-py install-web build-web serve clean help

## run: install everything, build the UI, and serve UI + API on one port
run: install build-web serve

## install: install both toolchains (Python via uv, frontend via npm)
install: install-py install-web

install-py:
	uv sync --all-packages

install-web:
	npm install

## build-web: build the thin web UI into apps/web/dist (served by the backend)
build-web:
	cd apps/web && npx vite build

## serve: boot the single server (serves the built UI + the authed API)
serve:
	@if [ ! -f .env ]; then \
		echo "No .env found — copy .env.example to .env and fill it in:"; \
		echo "    cp .env.example .env"; \
		exit 1; \
	fi
	@echo "ghostopia serving UI + API on http://$(HOST):$(PORT)"
	set -a && . ./.env && set +a && \
		uv run uvicorn $(APP) --factory --host $(HOST) --port $(PORT)

## clean: remove the built UI
clean:
	rm -rf apps/web/dist

## help: list the common targets
help:
	@echo "ghostopia targets:"
	@echo "  make run        install + build UI + serve on one port (default)"
	@echo "  make install    install Python (uv) + frontend (npm) deps"
	@echo "  make build-web  build the web UI into apps/web/dist"
	@echo "  make serve      boot the server (expects .env + a built UI)"
	@echo "  make clean      remove the built UI"
