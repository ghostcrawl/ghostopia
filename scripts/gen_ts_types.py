#!/usr/bin/env python3
"""Generate the server->client contract JSON Schema for the TS frontend.

This is the SINGLE Pydantic->TS contract source. It imports the shared Pydantic
models (the WS ``Envelope``, the harness ``TaskSpec``/``MissionSpec``, and every domain
entity + event model) and dumps a combined ``model_json_schema()`` (``$defs`` map) to
``apps/web/src/contract/schema.json``. ``package.json``'s ``gen:types`` then pipes that
schema through ``json-schema-to-typescript`` to emit typed client bindings.

The frontend NEVER imports the GhostCrawl SDK; the ONLY thing it shares with the Python
server is this generated contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/ -> ghostopia/
ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "apps" / "web" / "src" / "contract" / "schema.json"

# Ensure the shared package's src root is importable when run as a bare script
# (uv run also puts it on the path via the workspace install, but this makes the
# script robust when invoked directly).
sys.path.insert(0, str(ROOT / "packages" / "shared" / "src"))


def main() -> None:
    from ghostopia_shared import (
        Agent,
        AgentEvent,
        BrowserEvent,
        BrowserSession,
        Envelope,
        ErrorEvent,
        Ghost,
        GhostCommand,
        GhostEvent,
        Mission,
        MissionSpec,
        Result,
        SectionDef,
        SectionRef,
        Task,
        TaskSpec,
        Workstation,
        WorldObject,
    )
    from pydantic.json_schema import GenerateJsonSchema, models_json_schema

    models = [
        Envelope,
        GhostEvent,
        GhostCommand,
        AgentEvent,
        BrowserEvent,
        ErrorEvent,
        Ghost,
        Task,
        Mission,
        Agent,
        BrowserSession,
        Workstation,
        WorldObject,
        Result,
        SectionDef,
        SectionRef,
        TaskSpec,
        MissionSpec,
    ]

    # models_json_schema returns (keys->schema map, combined definitions with $defs).
    _, combined = models_json_schema(
        [(m, "validation") for m in models],
        ref_template="#/$defs/{model}",
        title="ghostopia-contract",
        schema_generator=GenerateJsonSchema,
    )

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(combined, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    defs = combined.get("$defs", {})
    print(f"wrote {SCHEMA_PATH.relative_to(ROOT)} ({len(defs)} definitions)")


if __name__ == "__main__":
    main()
