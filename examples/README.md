# ghostopia example recipes

Three declarative mission recipes that show the range of the ghostopia workforce.
A recipe is **data**: it names a behaviour and its parameters, is validated on load,
and is never imported or executed as code.

| Recipe | What it demonstrates |
| --- | --- |
| [`crawl-swarm.task.json`](crawl-swarm.task.json) | A multi-identity crawl swarm — bounded concurrency across several GhostCrawl identity profiles, with results flowing to the Data Graveyard. |
| [`research-swarm.task.json`](research-swarm.task.json) | An autonomous research swarm — one search fans out into per-result extraction tasks, showing multi-step mission routing. |
| [`whiteboard.task.json`](whiteboard.task.json) | A collaborative drawing mission — concurrent ghosts hold real browser sessions and draw strokes, showing full-fidelity input beyond scraping. |

## Using a recipe

Recipes are validated before a mission starts: the named behaviour must exist, its
parameters must match the declared schema, and every target URL passes a safety
check. Load one from the UI's example menu, or copy it as a starting point for a
new mission.

## Safety and limits

- Targets are checked before any session starts; unsafe addresses are rejected.
- Concurrency is bounded by the plan limits reported by GhostCrawl, with graceful
  queueing when the limit is reached.
- Some advanced behaviours require a paid tier and a compatible browser engine;
  on unsupported sessions they degrade with an explicit signal rather than silently
  doing nothing.

See [docs/usage.md](../docs/usage.md) for the mission walkthrough.
