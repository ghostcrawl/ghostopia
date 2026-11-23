"""SQLite persistence for missions/tasks/results (STAGE 7 — Python stdlib ``sqlite3``).

The server-owned, single-operator result store : the deterministic runner/LLM brain
extract REAL records from a page; each ``result.record_extracted`` is persisted here, tasks
accrue their record counts + status, and missions track completed/failed progress. The Data
Graveyard + the global dashboard read completed missions, per-section throughput, and a data
preview from this file over the authed WS.

Design:

* **stdlib only** — ``sqlite3`` (no ORM); the ``.sqlite`` file is gitignored (``*.sqlite``).
* **parameterized statements ONLY** — every value binds via ``?`` placeholders; no SQL is
  built by string-formatting untrusted input .
* **idempotent schema** — :func:`open_db` runs ``CREATE TABLE IF NOT EXISTS`` so re-opening an
  existing DB is a no-op and prior results survive a restart (persistence across re-open).
* **section/behavior recorded per task** — so the dashboard can report per-section throughput
  and per-behavior activity without a second source of truth.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from ghostopia_behaviors.product_match import (
    match_candidate,
    parse_offer,
    parse_query,
    product_identity_key,
)
from ghostopia_shared.price import normalize_amount, parse_price_amount

__all__ = [
    "SCHEMA_VERSION",
    "MigrationBackupError",
    "best_offers",
    "latest_verdicts",
    "observation_history",
    "record_verdict",
    "completed_missions",
    "insert_mission",
    "insert_result",
    "insert_task",
    "mission_progress",
    "open_db",
    "parse_price",
    "result_preview",
    "section_throughput",
    "update_task",
]

# The idempotent schema. missions ← tasks ← results (task carries section + behavior so
# per-section/per-behavior metrics need no join to a second store).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    total       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT,
    kind        TEXT NOT NULL DEFAULT '',
    section     TEXT,
    behavior    TEXT,
    url         TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    records     INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT,
    mission_id  TEXT,
    url         TEXT,
    -- the ORIGIN DEPARTMENT the behavior tagged this find with (repository_section), which is
    -- NOT the ghost's rostered STAGE section (tasks.section). A background relay ghost sits at a
    -- research/extraction/verify desk but its finds belong to the department that seeded it, so
    -- grouping the Data Graveyard by the rostered task section bucketed every relay find under a
    -- stage and left the departments empty. The result carries its own department here.
    section     TEXT,
    record_json TEXT NOT NULL,
    created_at  REAL NOT NULL
);

--  LEGACY (read-only since W6): the old min-price-forever best-offer row per fuzzy key.
-- No longer written or read for ranking; kept so an existing operator DB loses nothing and the
-- v2 migration can seed ``observations`` from it.
CREATE TABLE IF NOT EXISTS offers (
    product_key TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    price_raw   TEXT NOT NULL DEFAULT '',
    price_num   REAL,
    -- the price converted to an approximate common unit (USD) so cheapest-first ordering and the
    -- min-price conflict resolution are CURRENCY-SAFE (€9 can never "beat" £10 on bare numbers).
    price_norm  REAL,
    currency    TEXT,
    link        TEXT,
    image       TEXT,
    source_url  TEXT,
    section     TEXT,
    updated_at  REAL NOT NULL
);

-- W6 (audit 2026-10-18 ): the APPEND-ONLY offer observation log. One row per
-- (product, item link, department) per CHANGE of price/stock/condition/match; an unchanged
-- re-scrape only bumps ``last_seen_at`` (bounded growth). The current offer = the LATEST
-- observation per (product, item); best = cheapest eligible current observation. Replaces the
-- old min-price-forever ``offers`` row (kept, read-only, for legacy DBs).
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key   TEXT NOT NULL,
    item_link     TEXT NOT NULL DEFAULT '',
    source_url    TEXT NOT NULL DEFAULT '',
    section       TEXT,
    title         TEXT NOT NULL DEFAULT '',
    price_raw     TEXT NOT NULL DEFAULT '',
    price_num     REAL NOT NULL,
    currency      TEXT,
    price_norm    REAL,
    in_stock      INTEGER,
    condition     TEXT,
    method        TEXT,
    gtin          TEXT,
    mpn           TEXT,
    image         TEXT,
    match_query   TEXT,
    match_score   REAL,
    is_match      INTEGER,
    first_seen_at REAL NOT NULL,
    last_seen_at  REAL NOT NULL
);

-- W6 (): the persisted verify-desk verdict per department run — the matched best
-- (or status=no_exact_match, never an unrelated fallback) that the Data Graveyard badges.
CREATE TABLE IF NOT EXISTS verdicts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    section          TEXT,
    task_id          TEXT,
    mission_id       TEXT,
    match_query      TEXT,
    status           TEXT NOT NULL,
    best_product_key TEXT,
    best_link        TEXT,
    best_title       TEXT,
    best_price_raw   TEXT,
    best_price_num   REAL,
    best_currency    TEXT,
    candidates       INTEGER NOT NULL DEFAULT 0,
    matched          INTEGER NOT NULL DEFAULT 0,
    verified_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_item     ON observations(product_key, item_link, section);
CREATE INDEX IF NOT EXISTS idx_obs_section  ON observations(section);
CREATE INDEX IF NOT EXISTS idx_verdicts_sec ON verdicts(section);
CREATE INDEX IF NOT EXISTS idx_tasks_mission  ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_results_mission ON results(mission_id);
CREATE INDEX IF NOT EXISTS idx_results_task    ON results(task_id);
CREATE INDEX IF NOT EXISTS idx_offers_section  ON offers(section);
"""

#: Bumped by forward migrations (stored in ``PRAGMA user_version``). 2 = observations.
SCHEMA_VERSION = 2

# Freshness window for "current" offers, measured against wall-clock now: a price nobody
# re-observed for a day stops competing for "best". Env ``GHOSTOPIA_OFFER_MAX_AGE_S``; 0
# disables. Unknown stock (None) stays eligible (most keyless pages state no stock).
_DEFAULT_MAX_AGE_S = 86400.0


def parse_price(raw: Any) -> tuple[float | None, str | None]:
    """``(price_num, currency)`` via the ONE shared parser (``ghostopia_shared.price``).

    ``(None, ...)`` when unusable: no number, ``<= 0``/Free placeholders, multi-buy,
    instalment or non-current ("Was"/"RRP"/struck) values ."""
    amount, currency = parse_price_amount(raw)
    return (amount, currency) if amount is not None else (None, None)


def _str_field(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _item_link(record: Any, url: str | None) -> str:
    link = record.get("link") if isinstance(record, dict) else None
    return link if isinstance(link, str) and link else (url or "")


def _product_key(record: dict[str, Any], url: str | None) -> str:
    """GTIN > brand+MPN > model codes(+variant/spec) > token signature > item link ."""
    title = record.get("title")
    return product_identity_key(
        title if isinstance(title, str) else None,
        fallback=_item_link(record, url),
        gtin=record.get("gtin") if isinstance(record.get("gtin"), str) else None,
        mpn=record.get("mpn") if isinstance(record.get("mpn"), str) else None,
        brand=record.get("brand") if isinstance(record.get("brand"), str) else None,
    )


def _item_key(record: Any, url: str | None) -> str:
    """The results-card identity: the ITEM link when the record names one (a listing's N
    products stay N cards —), else page url + product identity."""
    if isinstance(record, dict):
        link = record.get("link")
        if isinstance(link, str) and link:
            return link
        title = record.get("title")
        if isinstance(title, str) and title.strip():
            return f"{url or ''}#{product_identity_key(title)}"
    return url or ""


def _bool_int(v: bool | None) -> int | None:
    return None if v is None else int(bool(v))


def _record_observation(
    conn: sqlite3.Connection,
    record: Any,
    url: str | None,
    section: str | None,
    *,
    match_query: str | None = None,
    now: float | None = None,
    method: str | None = None,
) -> None:
    """Append (or extend) the offer observation for one priced record. No-op without a usable
    price (``<= 0``/placeholder/unparseable never becomes an offer). Does NOT commit."""
    if not isinstance(record, dict):
        return
    offer = parse_offer(record)
    if offer.amount is None or offer.amount <= 0:
        return
    stamp = _stamp(now)
    key = _product_key(record, url)
    link = _item_link(record, url)
    score: float | None = None
    is_match: int | None = None
    if match_query:
        verdict = match_candidate(parse_query(match_query), offer.title)
        score, is_match = verdict.score, int(verdict.is_match)
    in_stock = _bool_int(offer.in_stock)
    meth = method or _str_field(record.get("price_method")) or None
    latest = conn.execute(
        "SELECT id, price_num, currency, in_stock, condition, is_match FROM observations "
        "WHERE product_key = ? AND item_link = ? AND section IS ? "
        "ORDER BY last_seen_at DESC, id DESC LIMIT 1",
        (key, link, section),
    ).fetchone()
    if latest is not None and (
        latest["price_num"] == offer.amount
        and latest["currency"] == offer.currency
        and latest["in_stock"] == in_stock
        and latest["condition"] == offer.condition
        and latest["is_match"] == is_match
    ):
        # An unchanged re-observation by current code VERIFIES a migrated legacy row (M1).
        conn.execute(
            "UPDATE observations SET last_seen_at = MAX(last_seen_at, ?), title = ?, image = ?, "
            "source_url = ?, method = CASE WHEN method IN (?, ?) THEN ? ELSE method END "
            "WHERE id = ?",
            (stamp, offer.title, _str_field(record.get("image")), url or "",
             *_LEGACY_METHODS, meth or "observed", latest["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO observations
            (product_key, item_link, source_url, section, title, price_raw, price_num, currency,
             price_norm, in_stock, condition, method, gtin, mpn, image, match_query, match_score,
             is_match, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key, link, url or "", section, offer.title,
            _str_field(record.get("price")) or f"{offer.amount}", offer.amount, offer.currency,
            normalize_amount(offer.amount, offer.currency), in_stock, offer.condition, meth,
            _str_field(record.get("gtin")) or None, _str_field(record.get("mpn")) or None,
            _str_field(record.get("image")), match_query, score, is_match, stamp, stamp,
        ),
    )


def _max_age() -> float:
    raw = os.environ.get("GHOSTOPIA_OFFER_MAX_AGE_S")
    try:
        return float(raw) if raw not in (None, "") else _DEFAULT_MAX_AGE_S
    except ValueError:
        return _DEFAULT_MAX_AGE_S


def _current_observations(
    conn: sqlite3.Connection, section: str | None, now: float | None = None
) -> list[sqlite3.Row]:
    """The LATEST observation per (product, item link) in scope, last seen within the freshness
    window of wall-clock ``now`` (: a lone 40-day-old row is NOT current)."""
    if section is None:
        rows = conn.execute(
            "SELECT * FROM observations ORDER BY last_seen_at ASC, id ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM observations WHERE section = ? ORDER BY last_seen_at ASC, id ASC",
            (section,),
        ).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in rows:
        latest[(r["product_key"], r["item_link"])] = r
    cur = list(latest.values())
    max_age = _max_age()
    if cur and max_age > 0:
        ref = _stamp(now)
        cur = [r for r in cur if ref - r["last_seen_at"] <= max_age]
    return cur


#: Observation methods seeded by the v2 migration. They carry no match verdict, so they are
#: UNVERIFIED and never eligible for "best" until the item is re-observed by current code (M1).
_LEGACY_METHODS = ("legacy_offer", "legacy_result")


def _eligible(r: sqlite3.Row) -> bool:
    return (
        r["method"] not in _LEGACY_METHODS
        and r["price_num"] is not None and r["price_num"] > 0
        and r["in_stock"] != 0
        and r["condition"] in (None, "new")
        and r["is_match"] != 0
    )


def _value_key(r: sqlite3.Row | dict[str, Any]) -> tuple[int, float]:
    norm = r["price_norm"]
    # unknown currency never beats a known one on value 
    return (0, norm) if norm is not None else (1, r["price_num"])


def best_offers(
    conn: sqlite3.Connection, section: str | None = None, *, now: float | None = None
) -> list[dict[str, Any]]:
    """The cheapest CURRENT eligible offer per product, cheapest first (optionally one section).

    Current = latest observation per (product, item link) within the freshness window — a price
    rise IS reflected . Eligible = priced > 0, not out of stock, new/unknown condition,
    and not a recorded match-reject for the department's query . Each row also carries
    ``observed_at``/``first_seen_at``/``in_stock``/``condition``/``method`` /
    ``match_score`` so the UI/export can show age + provenance."""
    ref = _stamp(now)
    winners: dict[str, sqlite3.Row] = {}
    for r in _current_observations(conn, section, ref):
        if not _eligible(r):
            continue
        cur = winners.get(r["product_key"])
        if cur is None or _value_key(r) < _value_key(cur):
            winners[r["product_key"]] = r
    ordered = sorted(winners.values(), key=lambda r: (*_value_key(r), r["product_key"]))
    return [
        {
            "product_key": r["product_key"],
            "title": r["title"],
            "price_raw": r["price_raw"],
            "price_num": r["price_num"],
            "currency": r["currency"],
            "link": r["item_link"],
            "image": r["image"],
            "source_url": r["source_url"],
            "section": r["section"],
            "observed_at": r["last_seen_at"],
            "first_seen_at": r["first_seen_at"],
            "in_stock": None if r["in_stock"] is None else bool(r["in_stock"]),
            "condition": r["condition"],
            "method": r["method"],
            "match_score": r["match_score"],
        }
        for r in ordered
    ]


def observation_history(
    conn: sqlite3.Connection, product_key: str, section: str | None = None
) -> list[dict[str, Any]]:
    """Every observation of one product (oldest first) — the price/stock history."""
    if section is None:
        rows = conn.execute(
            "SELECT * FROM observations WHERE product_key = ? ORDER BY first_seen_at, id",
            (product_key,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM observations WHERE product_key = ? AND section = ? "
            "ORDER BY first_seen_at, id",
            (product_key, section),
        ).fetchall()
    return [dict(r) for r in rows]


def record_verdict(
    conn: sqlite3.Connection, payload: dict[str, Any], *, now: float | None = None
) -> None:
    """Persist one verify-desk verdict (``result.verified`` with ``status``/``best``)."""
    best = payload.get("best") if isinstance(payload.get("best"), dict) else None
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        status = "matched" if best is not None else "no_exact_match"
    if status != "matched":
        best = None  # a fallback/related item is NEVER persisted as the best 
    amount = currency = None
    key = link = None
    if best is not None:
        offer = parse_offer(best)
        amount, currency = offer.amount, offer.currency
        key = _product_key(best, _str_field(payload.get("url")) or None)
        link = _item_link(best, _str_field(payload.get("url")) or None)
    conn.execute(
        """
        INSERT INTO verdicts
            (section, task_id, mission_id, match_query, status, best_product_key, best_link,
             best_title, best_price_raw, best_price_num, best_currency, candidates, matched,
             verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.get("section") if isinstance(payload.get("section"), str) else None,
            payload.get("task_id") if isinstance(payload.get("task_id"), str) else None,
            payload.get("mission_id") if isinstance(payload.get("mission_id"), str) else None,
            payload.get("query") if isinstance(payload.get("query"), str) else None,
            status, key, link,
            _str_field(best.get("title")) if best else None,
            _str_field(best.get("price")) if best else None,
            amount, currency,
            int(payload.get("candidates") or 0) if isinstance(payload.get("candidates"), int) else 0,
            int(payload.get("matched") or 0) if isinstance(payload.get("matched"), int) else 0,
            _stamp(now),
        ),
    )
    conn.commit()


def latest_verdicts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The newest verdict per department (section), newest first."""
    rows = conn.execute(
        "SELECT v.* FROM verdicts v JOIN (SELECT section, MAX(id) AS mid FROM verdicts "
        "GROUP BY section) last ON v.id = last.mid ORDER BY v.id DESC"
    ).fetchall()
    return [
        {
            "section": r["section"],
            "query": r["match_query"],
            "status": r["status"],
            "best_product_key": r["best_product_key"],
            "best_link": r["best_link"],
            "best_title": r["best_title"],
            "best_price_raw": r["best_price_raw"],
            "best_price_num": r["best_price_num"],
            "best_currency": r["best_currency"],
            "candidates": r["candidates"],
            "matched": r["matched"],
            "verified_at": r["verified_at"],
        }
        for r in rows
    ]


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating + migrating) the ghostopia result DB at ``path``.

    Idempotent: the schema is created with ``IF NOT EXISTS`` so re-opening an existing DB
    keeps every prior row (persistence across restart). ``row_factory`` is ``sqlite3.Row`` so
    queries return name-addressable rows. ``check_same_thread=False`` allows the single async
    server loop to reuse one connection across coroutines (all access is on the loop thread).
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        _backup_before_migration(conn, path)
    except Exception:
        conn.close()
        raise
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


class MigrationBackupError(RuntimeError):
    """The pre-migration backup could not be written/verified — the migration was refused."""


def _backup_db(conn: sqlite3.Connection, dest: Path) -> None:
    """Online, consistent copy via the sqlite backup API (safe with WAL + a live writer)."""
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
        out.commit()
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise MigrationBackupError(f"backup integrity_check failed: {ok}")
    finally:
        out.close()


def _backup_before_migration(conn: sqlite3.Connection, path: str | Path) -> Path | None:
    """Snapshot an EXISTING, older-schema DB before any schema change (M2).

    Writes ``<db>.bak-v<old>-<UTC stamp>`` next to the DB (or in ``GHOSTOPIA_DB_BACKUP_DIR``)
    with the sqlite backup API and verifies it (``integrity_check`` + same table set). Any
    failure raises :class:`MigrationBackupError` and the DB is left untouched. A fresh/empty
    DB, ``:memory:``, or a DB already at :data:`SCHEMA_VERSION` needs no backup.

    Restore: stop the ghostopia server, then
    ``cp <db>.bak-v<old>-<stamp> <db> && rm -f <db>-wal <db>-shm`` and start the PREVIOUS
    ghostopia version (a newer one would simply migrate the restored copy again)."""
    if str(path) == ":memory:":
        return None
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if not tables or version >= SCHEMA_VERSION:
        return None
    src = Path(path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bdir = Path(os.environ.get("GHOSTOPIA_DB_BACKUP_DIR") or src.parent)
    dest = bdir / f"{src.name}.bak-v{version}-{stamp}"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        _backup_db(conn, dest)
        chk = sqlite3.connect(str(dest))
        try:
            copied = {
                r[0] for r in chk.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        finally:
            chk.close()
        if copied != tables:
            raise MigrationBackupError(f"backup table set mismatch: {sorted(copied)} != {sorted(tables)}")
    except MigrationBackupError:
        raise
    except Exception as exc:  # noqa: BLE001 - any I/O/sqlite failure refuses the migration
        raise MigrationBackupError(f"pre-migration backup to {dest} failed: {exc}") from exc
    return dest


def _migrate(conn: sqlite3.Connection) -> None:
    """Forward-only, idempotent column adds for a DB created by an older schema.

    ``CREATE TABLE IF NOT EXISTS`` never ALTERs an existing table, so a results table created
    before the ``section`` column existed must be back-filled here. ``ADD COLUMN`` is a no-op
    guarded by a ``PRAGMA table_info`` presence check (SQLite has no ``ADD COLUMN IF NOT
    EXISTS``); pre-existing rows get ``section = NULL`` and fall back to the joined task section
    in reads (``COALESCE(r.section, t.section, '')``), so no historical row is lost.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(results)").fetchall()}
    if "section" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN section TEXT")
    # Index created here (not in _SCHEMA) so it is never referenced before the column exists on a
    # legacy DB — idempotent for a fresh DB (the column is already present).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_section ON results(section)")

    # offers.price_norm (currency-safe USD-normalized price) — added on a DB created before the
    # column existed. Back-fill legacy rows from their currency so cheapest-first ordering + the
    # min-price conflict resolution are immediately currency-safe (no re-scrape needed).
    offer_cols = {row["name"] for row in conn.execute("PRAGMA table_info(offers)").fetchall()}
    if offer_cols and "price_norm" not in offer_cols:
        conn.execute("ALTER TABLE offers ADD COLUMN price_norm REAL")
        for r in conn.execute(
            "SELECT product_key, price_num, currency FROM offers"
        ).fetchall():
            conn.execute(
                "UPDATE offers SET price_norm = ? WHERE product_key = ?",
                (normalize_amount(r["price_num"], r["currency"]), r["product_key"]),
            )

    # results.item_key : the per-ITEM card identity, so a listing page's N products
    # stay N rows. Back-filled for every legacy row from its stored record (no row is dropped).
    if "item_key" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN item_key TEXT")
        for r in conn.execute("SELECT id, url, record_json FROM results").fetchall():
            conn.execute(
                "UPDATE results SET item_key = ? WHERE id = ?",
                (_item_key(_load(r["record_json"]), r["url"]), r["id"]),
            )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_item ON results(item_key, section)")

    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version < 2:
        _migrate_v2_seed_observations(conn)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def _migrate_v2_seed_observations(conn: sqlite3.Connection) -> None:
    """Forward migration to the observation log: seed it from an operator's EXISTING data.

    The legacy ``offers`` rows (one min-ever price per old fuzzy key) and every legacy
    ``results`` record are replayed into ``observations`` at their original timestamps, re-keyed
    with the identity key. Nothing is deleted: ``offers``/``results`` keep every row (the old
    ``offers`` table is simply no longer read). Rows that fail the shared parser (``$0`` /
    "Free" placeholders) are not seeded as offers — they stay visible in ``results``."""
    if conn.execute("SELECT 1 FROM observations LIMIT 1").fetchone() is not None:
        return
    has_offers = bool(conn.execute("PRAGMA table_info(offers)").fetchall())
    if has_offers:
        for r in conn.execute("SELECT * FROM offers ORDER BY updated_at").fetchall():
            rec = {
                "title": r["title"], "price": r["price_raw"], "link": r["link"] or r["source_url"],
                "image": r["image"],
            }
            if r["currency"]:
                rec["currency"] = r["currency"]
            _record_observation(
                conn, rec, r["source_url"] or None, r["section"],
                now=r["updated_at"], method="legacy_offer",
            )
    for r in conn.execute(
        "SELECT url, section, record_json, created_at FROM results ORDER BY created_at, id"
    ).fetchall():
        _record_observation(
            conn, _load(r["record_json"]), r["url"], r["section"],
            now=r["created_at"], method="legacy_result",
        )


def insert_mission(
    conn: sqlite3.Connection,
    mission_id: str,
    title: str = "",
    total: int = 0,
    *,
    now: float | None = None,
) -> None:
    """Upsert a mission row (idempotent on ``id``). Re-submitting keeps ``created_at``."""
    conn.execute(
        """
        INSERT INTO missions (id, title, total, status, created_at)
        VALUES (?, ?, ?, 'running', ?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, total=excluded.total
        """,
        (mission_id, title, int(total), _stamp(now)),
    )
    conn.commit()


def insert_task(
    conn: sqlite3.Connection,
    task_id: str,
    mission_id: str | None,
    kind: str = "",
    section: str | None = None,
    behavior: str | None = None,
    url: str | None = None,
    status: str = "running",
    *,
    now: float | None = None,
) -> None:
    """Upsert a task row (idempotent on ``id``) recording its section + behavior + url."""
    conn.execute(
        """
        INSERT INTO tasks (id, mission_id, kind, section, behavior, url, status, records, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(id) DO UPDATE SET
            mission_id=excluded.mission_id, kind=excluded.kind, section=excluded.section,
            behavior=excluded.behavior, url=excluded.url, updated_at=excluded.updated_at
        """,
        (task_id, mission_id, kind, section, behavior, url, status, _stamp(now)),
    )
    conn.commit()


def update_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    status: str | None = None,
    records: int | None = None,
    now: float | None = None,
) -> None:
    """Update a task's ``status`` and/or absolute ``records`` count (parameterized)."""
    sets: list[str] = ["updated_at = ?"]
    args: list[Any] = [_stamp(now)]
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if records is not None:
        sets.append("records = ?")
        args.append(int(records))
    args.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args)  # noqa: S608 - fixed column names only
    conn.commit()


def insert_result(
    conn: sqlite3.Connection,
    task_id: str | None,
    mission_id: str | None,
    url: str | None,
    record: Any,
    *,
    section: str | None = None,
    match_query: str | None = None,
    now: float | None = None,
) -> int:
    """Persist one extracted record, bump the task's record count, and log an offer observation.

    ``section`` is the ORIGIN DEPARTMENT the emitting behavior tagged this find with
    (``repository_section``) — NOT the ghost's rostered STAGE section — falling back to the owning
    task's section when absent.

    Cards dedupe per department on the ITEM (``item_key`` = the record's own ``link``, else page
    url + product identity), so a re-crawl refreshes its card while a listing page's N products
    persist as N cards . Every priced record also appends/extends an observation
    (:func:`_record_observation`) — history is kept, never overwritten. ``match_query`` (the
    department's search target) scores the record so a non-matching item can't become "best".
    """
    if section is None and task_id is not None:
        srow = conn.execute("SELECT section FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if srow is not None:
            section = srow["section"]
    record_json = json.dumps(record, default=str)
    item_key = _item_key(record, url)
    if item_key:
        existing = conn.execute(
            "SELECT id FROM results WHERE item_key = ? AND section IS ? ORDER BY id DESC LIMIT 1",
            (item_key, section),
        ).fetchone()
        if existing is not None:
            row_id = int(existing["id"])
            conn.execute(
                "UPDATE results SET task_id = ?, mission_id = ?, url = ?, record_json = ?, "
                "created_at = ? WHERE id = ?",
                (task_id, mission_id, url, record_json, _stamp(now), row_id),
            )
            _record_observation(conn, record, url, section, match_query=match_query, now=now)
            conn.commit()
            return row_id
    cur = conn.execute(
        """
        INSERT INTO results (task_id, mission_id, url, section, record_json, created_at, item_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, mission_id, url, section, record_json, _stamp(now), item_key),
    )
    if task_id is not None:
        conn.execute("UPDATE tasks SET records = records + 1 WHERE id = ?", (task_id,))
    _record_observation(conn, record, url, section, match_query=match_query, now=now)
    conn.commit()
    return int(cur.lastrowid or 0)


def mission_progress(conn: sqlite3.Connection, mission_id: str) -> dict[str, int]:
    """Return ``{total, completed, failed, records}`` for a mission.

    ``total`` is the mission's declared task count (falling back to the observed task rows);
    ``completed``/``failed`` count task rows by status; ``records`` sums extracted records.
    """
    row = conn.execute("SELECT total FROM missions WHERE id = ?", (mission_id,)).fetchone()
    declared = int(row["total"]) if row else 0
    agg = conn.execute(
        """
        SELECT
            COUNT(*) AS observed,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed,
            COALESCE(SUM(records), 0) AS records
        FROM tasks WHERE mission_id = ?
        """,
        (mission_id,),
    ).fetchone()
    observed = int(agg["observed"] or 0)
    return {
        "total": max(declared, observed),
        "completed": int(agg["completed"] or 0),
        "failed": int(agg["failed"] or 0),
        "records": int(agg["records"] or 0),
    }


def completed_missions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All missions whose every declared/observed task has finished, newest first.

    A mission is 'completed' when it has at least one task and none are still running — its
    tasks are all ``completed``/``failed``. Returns each with its progress rollup + title.
    """
    rows = conn.execute("SELECT id, title, total, created_at FROM missions").fetchall()
    out: list[dict[str, Any]] = []
    for m in rows:
        running = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE mission_id = ? AND status = 'running'",
            (m["id"],),
        ).fetchone()
        observed = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE mission_id = ?", (m["id"],)
        ).fetchone()
        if int(observed["n"]) == 0 or int(running["n"]) > 0:
            continue
        prog = mission_progress(conn, m["id"])
        out.append(
            {
                "id": m["id"],
                "title": m["title"],
                "created_at": m["created_at"],
                "progress": prog,
            }
        )
    out.sort(key=lambda d: d["created_at"], reverse=True)
    return out


def result_preview(
    conn: sqlite3.Connection, mission_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """The most recent extracted records (optionally scoped to one mission), newest first.

    Each entry is ``{id, task_id, mission_id, url, record, section}`` with ``record`` JSON-decoded
    (``id`` is the row's monotonic AUTOINCREMENT primary key — a stable client merge/sort key) —
    the data-preview table the Data Graveyard renders. ``section`` is the ORIGIN DEPARTMENT the
    behavior tagged this find with (stored on the result row), falling back to the owning task's
    rostered section for legacy rows that carry none, then ``""``. This is what makes the
    "by department" grouping correct: a background relay ghost sits at a research/extraction/
    verify stage desk, but its find belongs to (and now groups under) the department that seeded
    it — not the stage. ``limit`` is clamped to a sane ceiling.
    """
    limit = max(1, min(int(limit), 500))
    if mission_id is None:
        rows = conn.execute(
            "SELECT r.id, r.task_id, r.mission_id, r.url, r.record_json, "
            "COALESCE(r.section, t.section, '') AS section "
            "FROM results r LEFT JOIN tasks t ON r.task_id = t.id "
            "ORDER BY r.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT r.id, r.task_id, r.mission_id, r.url, r.record_json, "
            "COALESCE(r.section, t.section, '') AS section "
            "FROM results r LEFT JOIN tasks t ON r.task_id = t.id "
            "WHERE r.mission_id = ? ORDER BY r.id DESC LIMIT ?",
            (mission_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "task_id": r["task_id"],
            "mission_id": r["mission_id"],
            "url": r["url"],
            "record": _load(r["record_json"]),
            "section": r["section"],
        }
        for r in rows
    ]


def section_throughput(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-section rollup: ``{section, tasks, completed, failed, records}`` (records desc).

    Sourced from the task rows' ``section`` + ``records`` columns (single-source read) so the
    dashboard's per-section throughput never needs a second store.
    """
    rows = conn.execute(
        """
        SELECT
            COALESCE(section, '') AS section,
            COUNT(*) AS tasks,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed,
            COALESCE(SUM(records), 0) AS records
        FROM tasks GROUP BY COALESCE(section, '')
        ORDER BY records DESC, section ASC
        """
    ).fetchall()
    return [
        {
            "section": r["section"],
            "tasks": int(r["tasks"]),
            "completed": int(r["completed"] or 0),
            "failed": int(r["failed"] or 0),
            "records": int(r["records"] or 0),
        }
        for r in rows
    ]


def _stamp(now: float | None) -> float:
    return time.time() if now is None else now


def _load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
