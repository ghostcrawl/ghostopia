"""SQLite persistence for missions / tasks / results (STAGE 7 — Python stdlib ``sqlite3``).

The server-owned, single-operator result store: the deterministic runner / LLM brain
extract REAL records from a page; each ``result.record_extracted`` is persisted here, tasks
accrue their record counts + status, and missions track completed/failed progress. The Data
Graveyard + the global dashboard read completed missions, per-section throughput, and a data
preview from this file over the authed WS.

Design:

* **stdlib only** — ``sqlite3`` (no ORM); the ``.sqlite`` file is gitignored (``*.sqlite``).
* **parameterized statements ONLY** — every value binds via ``?`` placeholders; no SQL is
  built by string-formatting untrusted input.
* **idempotent schema** — :func:`open_db` runs ``CREATE TABLE IF NOT EXISTS`` so re-opening an
  existing DB is a no-op and prior results survive a restart (persistence across re-open).
* **section/behavior recorded per task** — so the dashboard can report per-section throughput
  and per-behavior activity without a second source of truth.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

__all__ = [
    "best_offers",
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
# per-section / per-behavior metrics need no join to a second store).
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

-- The NORMALIZED best-offer store. Each result with a parseable price is normalized on
-- write (title/price_raw/price_num/currency/link/image/source_url/section) and UPSERTed keyed by
-- a product key (normalized title, else url) keeping the MINIMUM parsed price — so N candidate
-- scrapes of one product collapse to exactly one winning row (best-price selection).
CREATE TABLE IF NOT EXISTS offers (
    product_key TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    price_raw   TEXT NOT NULL DEFAULT '',
    price_num   REAL,
    currency    TEXT,
    link        TEXT,
    image       TEXT,
    source_url  TEXT,
    section     TEXT,
    updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_mission  ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_results_mission ON results(mission_id);
CREATE INDEX IF NOT EXISTS idx_results_task    ON results(task_id);
CREATE INDEX IF NOT EXISTS idx_offers_section  ON offers(section);
"""

# Currency detection — symbol first (prefix like ``£51.77`` / ``$12.99``), else a 3-letter ISO
# code word (``1,299.00 USD`` / ``USD 12.99``). Kept small + explicit (no locale dependency).
_CURRENCY_SYMBOLS: dict[str, str] = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY"}
_CURRENCY_CODES: tuple[str, ...] = (
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "MXN", "BRL",
)
# The first number in the string, capturing a full grouped/decimal token in EITHER convention:
# US ``1,299.00`` OR European ``1.299,00`` / ``9,99``. Must start AND end on a digit so a
# trailing sentence period is never absorbed. ``_to_float`` disambiguates decimal vs thousands.
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")


def _to_float(num: str) -> float:
    """Normalize a grouped numeric token (``,``/``.`` as decimal OR thousands separators) → float.

    Heuristic (locale-free):
    * both separators present → the LAST one is the decimal point (``1.299,00`` → ``1299.00``,
      ``1,299.00`` → ``1299.00``);
    * only ``,`` present → decimal when exactly one comma with 1-2 trailing digits (European
      ``9,99`` → ``9.99``), else thousands (``1,299`` → ``1299``);
    * only ``.`` (or neither) → dot stays the decimal point (US default, ``9.50`` → ``9.5``).
    """
    has_dot = "." in num
    has_comma = "," in num
    if has_dot and has_comma:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif has_comma:
        if num.count(",") == 1 and len(num.rsplit(",", 1)[1]) in (1, 2):
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    return float(num)


def parse_price(raw: Any) -> tuple[float | None, str | None]:
    """Parse a price string → ``(price_num, currency)``; ``(None, None)`` when unparseable.

    ``price_num`` is a float (grouping separators normalized by ``_to_float`` — comma is read
    as a decimal point in the European ``9,99`` form); ``currency`` is an ISO code inferred from a
    leading symbol (``£`` → GBP) or a 3-letter code word in the string, else ``None``. A string
    with no number (``"Free"``, ``""``) — or a non-string — yields ``(None, None)`` so the
    best-offer upsert can skip it (no synthetic zero price).
    """
    if not isinstance(raw, str):
        return (None, None)
    text = raw.strip()
    if not text:
        return (None, None)
    currency: str | None = None
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            currency = code
            break
    if currency is None:
        upper = text.upper()
        for code in _CURRENCY_CODES:
            if re.search(rf"\b{code}\b", upper):
                currency = code
                break
    m = _NUM_RE.search(text)
    if m is None:
        return (None, None)
    try:
        price_num = _to_float(m.group(0))
    except ValueError:
        return (None, None)
    return (price_num, currency)


def _product_key(record: dict[str, Any], url: str | None) -> str:
    """The stable identity a best-offer row is keyed on: the normalized title (lower + collapsed
    whitespace) so the SAME product across retailers collapses to one row, falling back to the
    page url when there is no title."""
    title = record.get("title")
    if isinstance(title, str) and title.strip():
        return "t:" + re.sub(r"\s+", " ", title.strip().lower())
    return "u:" + (url or "")


def _str_field(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _upsert_best_offer(
    conn: sqlite3.Connection,
    record: Any,
    url: str | None,
    section: str | None,
    *,
    now: float | None = None,
) -> None:
    """Normalize ``record`` and UPSERT the best (minimum-price) offer for its product key.

    A no-op unless ``record`` is a dict carrying a parseable ``price`` (``parse_price``). ``link``
    falls back to the page ``url`` (the one field always available) so the export/graveyard link
    column is never blank. The ``ON CONFLICT ... WHERE excluded.price_num < offers.price_num``
    clause keeps the CHEAPEST candidate — N scrapes of one product converge to one winning row.
    Does NOT commit (the caller commits once)."""
    if not isinstance(record, dict):
        return
    price_num, currency = parse_price(record.get("price"))
    if price_num is None:
        return
    key = _product_key(record, url)
    link = _str_field(record.get("link")) or (url or "")
    conn.execute(
        """
        INSERT INTO offers
            (product_key, title, price_raw, price_num, currency, link, image, source_url, section, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
            title=excluded.title, price_raw=excluded.price_raw, price_num=excluded.price_num,
            currency=excluded.currency, link=excluded.link, image=excluded.image,
            source_url=excluded.source_url, section=excluded.section, updated_at=excluded.updated_at
        WHERE excluded.price_num < offers.price_num
        """,
        (
            key,
            _str_field(record.get("title")),
            _str_field(record.get("price")),
            price_num,
            currency,
            link,
            _str_field(record.get("image")),
            url or "",
            section,
            _stamp(now),
        ),
    )


def best_offers(
    conn: sqlite3.Connection, section: str | None = None
) -> list[dict[str, Any]]:
    """The winning (minimum-price) offers, cheapest first — optionally scoped to one ``section``.

    Each row is the best-price record kept for a product key: ``{product_key, title, price_raw,
    price_num, currency, link, image, source_url, section}``. Surfaced on the
    ``result.mission_progress`` envelope for the export + Data Graveyard "best" badge (R5)."""
    if section is None:
        rows = conn.execute(
            "SELECT * FROM offers ORDER BY price_num ASC, product_key ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM offers WHERE section = ? ORDER BY price_num ASC, product_key ASC",
            (section,),
        ).fetchall()
    return [
        {
            "product_key": r["product_key"],
            "title": r["title"],
            "price_raw": r["price_raw"],
            "price_num": r["price_num"],
            "currency": r["currency"],
            "link": r["link"],
            "image": r["image"],
            "source_url": r["source_url"],
            "section": r["section"],
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


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
    now: float | None = None,
) -> int:
    """Persist one extracted record and bump the owning task's record count.

    ``record`` is JSON-serialized (a dict/list/scalar the extract step produced). Returns the
    new row id. Bumping ``tasks.records`` keeps per-section throughput a single-source read.

    ``section`` is the ORIGIN DEPARTMENT the emitting behavior tagged this find with
    (``repository_section``) — NOT the ghost's rostered STAGE section. It is stored ON the result
    row (so a background relay ghost's find groups under its department, never the research/
    extraction/verify desk it happens to sit at) and drives the best-offer bucket. When the event
    carries no section (a mission run that never set one), it falls back to the owning task's
    rostered section so behavior is unchanged for those rows.
    """
    if section is None and task_id is not None:
        srow = conn.execute("SELECT section FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if srow is not None:
            section = srow["section"]
    record_json = json.dumps(record, default=str)
    # DEDUPE per department: a looping department ghost re-crawls the SAME product pages every
    # cycle (books.toscrape / any static listing is stable), so without this each pass inserts a
    # duplicate row and the Data Graveyard shows the same book/product many times over. Upsert by
    # (section, url) instead — one card per distinct product per department, its content refreshed
    # to the latest scrape. Rows with no url (rare) are never merged (kept as-is).
    if url:
        existing = conn.execute(
            "SELECT id FROM results WHERE url = ? AND section IS ? ORDER BY id DESC LIMIT 1",
            (url, section),
        ).fetchone()
        if existing is not None:
            row_id = int(existing["id"])
            conn.execute(
                "UPDATE results SET task_id = ?, mission_id = ?, record_json = ?, created_at = ? "
                "WHERE id = ?",
                (task_id, mission_id, record_json, _stamp(now), row_id),
            )
            _upsert_best_offer(conn, record, url, section, now=now)
            conn.commit()
            return row_id
    cur = conn.execute(
        """
        INSERT INTO results (task_id, mission_id, url, section, record_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, mission_id, url, section, record_json, _stamp(now)),
    )
    if task_id is not None:
        conn.execute("UPDATE tasks SET records = records + 1 WHERE id = ?", (task_id,))
    # Also normalize this record into the best-offer store (min-price per product). The
    # section is this result's ORIGIN department (resolved above); a record without a parseable
    # price is skipped inside the upsert.
    _upsert_best_offer(conn, record, url, section, now=now)
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
