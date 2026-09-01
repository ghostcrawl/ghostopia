"""Server-authoritative section/department authoring verbs — the department editor.

An operator authors a themed scraping **department** (a label/theme + a what-to-scrape identity
— a ``target_url`` OR a ``query`` — + an ``extract_schema``) and edits/removes it AT RUNTIME
over the JWT-gated ``section.save`` / ``section.remove`` WS verbs. This module is the TRUST
BOUNDARY, mirroring :class:`~ghostopia_server.map_editor.MapEditor` (``map.save``):

* the verbs are registered ONLY on the authed :class:`~ghostopia_server.ws_gateway.WsGateway`
  (the operator JWT is verified pre-accept), so there is no second unauthed authoring path;
* the submitted payload is validated into a STRICT :class:`~ghostopia_sections.section.SectionDef`
  (``extra='forbid'`` — a hallucinated/unknown field is rejected cleanly);
* any ``target_url`` passes the SSRF gate (``validate_mission_url``) BEFORE the department goes
  live — a loopback / private / cloud-metadata target is rejected with a reason and the live
  section set is left UNTOUCHED;
* the customer-facing ``label`` / ``category`` pass the surface-language guard
  (:func:`~ghostopia_shared.surface_safe.is_surface_safe`) — an internal / vendor / codename
  term is rejected before the department is broadcast.

On a fully-valid save the editor calls :meth:`Orchestrator.upsert_section` (runtime CRUD that
preserves live rosters), rebroadcasts ``catalog.sections`` through the ONE shared builder, and
emits ``section.saved {ok:True}``. Every failure emits a clean ``{ok:False, reason}`` WITHOUT
mutating live state. ``section.remove`` drops the department, rebroadcasts, and reports whether
it existed.

Persistence: the shipped ``maps/graveyard.sections.json`` seed is NEVER
rewritten (static seeding stays a separate concern), but every authored department IS written
back to a SEPARATE user data file via :class:`AuthoredSectionStore`. A boot merge
(:func:`merge_authored_sections`) lays that file on top of the seed so a user's own targets
survive a server restart — the fix for the "authored departments are
memory-only" root cause. The seed stays pristine; the user's edits live in the user file.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ghostopia_sections import Section
from ghostopia_sections.section import SectionDef
from ghostopia_shared import Envelope
from ghostopia_shared.envelope import serialize_envelope
from ghostopia_shared.surface_safe import is_surface_safe

from .gc_event_source import build_catalog_sections_envelope
from .ssrf import SsrfBlockedError, validate_mission_url

__all__ = [
    "AuthoredSectionStore",
    "SectionEditor",
    "authored_sections_path",
    "merge_authored_sections",
]

Broadcast = Callable[[Envelope], Awaitable[None]]


def authored_sections_path() -> Path:
    """Resolve the user data file the authored departments persist to.

    ``GHOSTOPIA_AUTHORED_SECTIONS_PATH`` overrides it; otherwise it sits next to the server's
    SQLite store (``GHOSTOPIA_DB_PATH`` dir, else the apps/server root) as
    ``ghostopia.authored-sections.json``. It is DELIBERATELY separate from the shipped
    ``maps/graveyard.sections.json`` seed so the seed is never mutated and a user's own
    targets survive a restart."""
    raw = os.environ.get("GHOSTOPIA_AUTHORED_SECTIONS_PATH")
    if raw and raw.strip():
        return Path(raw.strip())
    db_path = os.environ.get("GHOSTOPIA_DB_PATH")
    base = Path(db_path).resolve().parent if db_path else Path(__file__).resolve().parents[2]
    return base / "ghostopia.authored-sections.json"


class AuthoredSectionStore:
    """The write-back file for runtime-authored departments.

    A tiny JSON list of :class:`SectionDef` dumps keyed by id. :meth:`save` upserts a
    department and dumps the whole list to disk; :meth:`remove` drops one and rewrites;
    :meth:`load` reads them back (an absent/corrupt file is an empty set — the seed still
    boots). It NEVER touches the shipped seed file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _read_raw(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return []
        if isinstance(data, dict):
            data = data.get("sections", [])
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    def _write_raw(self, rows: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(rows, f, indent=2, sort_keys=True)

    def load(self) -> list[SectionDef]:
        """Read the authored departments back (tolerant of an absent/corrupt file)."""
        out: list[SectionDef] = []
        for row in self._read_raw():
            try:
                out.append(SectionDef.model_validate(row))
            except Exception:  # noqa: BLE001 - a stale/invalid row is skipped, never fatal
                continue
        return out

    def save(self, defn: SectionDef) -> None:
        """Upsert one authored department by id + dump the whole set to disk."""
        rows = [r for r in self._read_raw() if r.get("id") != defn.id]
        rows.append(defn.model_dump(mode="json", exclude_none=True))
        self._write_raw(rows)

    def remove(self, section_id: str) -> bool:
        """Drop an authored department by id; return whether it existed."""
        rows = self._read_raw()
        kept = [r for r in rows if r.get("id") != section_id]
        if len(kept) == len(rows):
            return False
        self._write_raw(kept)
        return True


def merge_authored_sections(
    seed: list[Section], store: AuthoredSectionStore
) -> list[Section]:
    """Lay the persisted authored departments ON TOP of the shipped seed (boot merge).

    An authored id that matches a seed id OVERRIDES it (the user's edit wins) in place; a new
    authored id is appended. The seed is never lost — this is what makes a user's own targets
    survive a restart while the shipped example departments always remain."""
    merged = list(seed)
    index = {s.id: i for i, s in enumerate(merged)}
    for defn in store.load():
        section = Section(defn)
        if defn.id in index:
            merged[index[defn.id]] = section
        else:
            index[defn.id] = len(merged)
            merged.append(section)
    return merged

#: The generic reject reason surfaced when a label/category carries a banned term — the banned
#: term itself is NEVER echoed back onto the customer surface.
_LABEL_REJECT_REASON = "That department name isn't allowed."


class SectionEditor:
    """Owns the ``section.save`` / ``section.remove`` authoring verbs on the authed gateway.

    Mirrors :class:`~ghostopia_server.map_editor.MapEditor`: it validates an operator-submitted
    department (schema → SSRF → surface-language) and, only on a fully-valid save, mutates the
    injected :class:`~ghostopia_server.orchestrator.Orchestrator`'s live sections + rebroadcasts
    ``catalog.sections``. An invalid/hostile department is rejected with a reason and the live
    section set is left untouched.
    """

    def __init__(
        self,
        broadcast: Broadcast,
        orchestrator: Any,
        store: AuthoredSectionStore | None = None,
    ) -> None:
        self._broadcast = broadcast
        self._orchestrator = orchestrator
        #: The write-back store. When set, a valid save persists the authored
        #: department + a remove drops it, so a user's own targets survive a restart. ``None``
        #: keeps the historical memory-only behavior (tests that don't exercise persistence).
        self._store = store

    # -- installation ---------------------------------------------------------------

    def install(self, gateway: Any) -> None:
        """Register the two authoring verbs on the authed gateway (JWT-gated + allow-listed)."""
        gateway.register_control("section.save", self.on_save)
        gateway.register_control("section.remove", self.on_remove)

    # -- helpers --------------------------------------------------------------------

    async def _reject(self, reason: str) -> None:
        await self._broadcast(
            serialize_envelope(
                type="section.saved", ts=time.time(), payload={"ok": False, "reason": reason}
            )
        )

    async def _rebroadcast_catalog(self) -> None:
        """Rebroadcast the CURRENT department set through the ONE shared catalog builder."""
        await self._broadcast(build_catalog_sections_envelope(self._orchestrator._sections))

    @staticmethod
    def _labels_are_surface_safe(defn: SectionDef) -> bool:
        """The customer-facing label + category carry NO banned vendor/codename term."""
        return is_surface_safe(defn.label) and is_surface_safe(defn.category)

    # -- verbs ----------------------------------------------------------------------

    async def on_save(self, envelope: Envelope) -> None:
        """``section.save {section}`` — validate (schema → SSRF → language) → go live + relay."""
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        raw = payload.get("section")

        # 1. strict schema — a hallucinated/unknown field is a clean reject.
        try:
            defn = SectionDef.model_validate(raw)
        except Exception as err:  # noqa: BLE001 - any parse failure is a clean reject
            await self._reject(f"invalid department: {err}")
            return

        # 2. SSRF gate on the authored target BEFORE it goes live. A section can
        #    carry a concrete ``target_url``; a loopback/private/metadata target is rejected and
        #    the live set is untouched.
        if defn.target_url:
            try:
                validate_mission_url(defn.target_url, ())
            except SsrfBlockedError as err:
                await self._reject(f"blocked target: {err}")
                return

        # 3. surface-language guard on the customer-facing label/category. The
        #    banned term is NEVER echoed back onto the surface.
        if not self._labels_are_surface_safe(defn):
            await self._reject(_LABEL_REJECT_REASON)
            return

        # validated → go live atomically (runtime CRUD preserves live rosters), then rebroadcast.
        self._orchestrator.upsert_section(defn)
        # write the authored department back to the user data file so it
        # survives a restart. Only a FULLY-validated department reaches here, so a hostile /
        # malformed one is never persisted.
        if self._store is not None:
            self._store.save(defn)
        await self._broadcast(
            serialize_envelope(type="section.saved", ts=time.time(), payload={"ok": True})
        )
        await self._rebroadcast_catalog()

    async def on_remove(self, envelope: Envelope) -> None:
        """``section.remove {id}`` — drop the department, rebroadcast, report whether it existed."""
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        section_id = str(payload.get("id", ""))
        existed = self._orchestrator.remove_section(section_id)
        # Drop the persisted authored copy too (if any), so a removed department does
        # not resurrect on the next boot merge.
        if existed and self._store is not None:
            self._store.remove(section_id)
        await self._broadcast(
            serialize_envelope(
                type="section.saved",
                ts=time.time(),
                payload={"ok": bool(existed), "removed": section_id if existed else None},
            )
        )
        await self._rebroadcast_catalog()
