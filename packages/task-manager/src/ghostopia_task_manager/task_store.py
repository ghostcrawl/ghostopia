"""Injectable persistence for task/mission records.

The :class:`TaskManager` (the management COMMAND surface) never touches a database
directly — it holds a :class:`TaskStore`, a narrow ``Protocol`` (``create``/``get``/
``list``/``patch``/``set_status`` for BOTH task and mission records). Two adapters ship:

* :class:`InMemoryTaskStore` — the process-local default (tests + a single-operator boot).
* :class:`SqliteTaskStore` — a stdlib-``sqlite3`` adapter seam. The server binds it by
  passing an OPEN connection; this module imports ``sqlite3`` only (NEVER the server db
  module), so the package stays free of any ``ghostopia_server`` / ``ghostcrawl`` import.

A record is a plain dataclass carrying the validated :class:`~ghostopia_shared.task.TaskSpec`
/ :class:`~ghostopia_shared.task.MissionSpec` (the spec is DATA — already ``extra='forbid'``
validated by the manager before it lands here) plus mutable lifecycle state (status,
section/ghost assignment, attempts). An unknown id raises :class:`RecordNotFoundError`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ghostopia_shared.task import MissionSpec, TaskSpec

__all__ = [
    "InMemoryTaskStore",
    "MissionRecord",
    "RecordNotFoundError",
    "SqliteTaskStore",
    "TaskRecord",
    "TaskStore",
]

#: The lifecycle a task record moves through (all terminal states are the last three).
#: created -> queued -> running -> {completed | failed | cancelled}; pause toggles ``paused``;
#: retarget moves a running/queued task back to ``queued`` on the new target.
TASK_STATES = (
    "created",
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "retargeted",
)

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})


class RecordNotFoundError(KeyError):
    """Raised when a task/mission id is not present in the store."""


@dataclass
class TaskRecord:
    """One persisted task: its validated ``spec`` (DATA) + mutable lifecycle state."""

    id: str
    spec: TaskSpec
    status: str = "created"
    section: str | None = None
    ghost_ids: list[str] | None = None
    mission_id: str | None = None
    attempts: int = 0
    error_code: str | None = None
    paused: bool = False


@dataclass
class MissionRecord:
    """One persisted mission: its validated ``spec`` (DATA) + its member task ids + status."""

    id: str
    spec: MissionSpec
    task_ids: list[str] = field(default_factory=list)
    status: str = "created"


@runtime_checkable
class TaskStore(Protocol):
    """The injectable persistence seam the :class:`TaskManager` composes over.

    ``create``/``get``/``list``/``patch``/``set_status`` for task AND mission records. The
    server binds a concrete store; the manager never imports a db module. All getters raise
    :class:`RecordNotFoundError` for an unknown id.
    """

    # -- task records ------------------------------------------------------------------
    def create_task(self, record: TaskRecord) -> TaskRecord: ...
    def get_task(self, task_id: str) -> TaskRecord: ...
    def list_tasks(self) -> list[TaskRecord]: ...
    def patch_task(self, task_id: str, **fields: Any) -> TaskRecord: ...
    def set_task_status(self, task_id: str, status: str) -> TaskRecord: ...

    # -- mission records ---------------------------------------------------------------
    def create_mission(self, record: MissionRecord) -> MissionRecord: ...
    def get_mission(self, mission_id: str) -> MissionRecord: ...
    def list_missions(self) -> list[MissionRecord]: ...
    def patch_mission(self, mission_id: str, **fields: Any) -> MissionRecord: ...
    def set_mission_status(self, mission_id: str, status: str) -> MissionRecord: ...


def _apply_fields(record: Any, fields: dict[str, Any]) -> None:
    """Set only KNOWN dataclass fields on ``record`` (an unknown key raises AttributeError)."""
    for key, value in fields.items():
        if not hasattr(record, key):
            raise AttributeError(f"{type(record).__name__} has no field {key!r}")
        setattr(record, key, value)


class InMemoryTaskStore:
    """The default process-local store — dict-backed, no persistence. Ideal for tests +
    a single-operator boot. Implements the full :class:`TaskStore` Protocol."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._missions: dict[str, MissionRecord] = {}

    # -- task records ------------------------------------------------------------------

    def create_task(self, record: TaskRecord) -> TaskRecord:
        if record.id in self._tasks:
            raise ValueError(f"task {record.id!r} already exists")
        self._tasks[record.id] = record
        return record

    def get_task(self, task_id: str) -> TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise RecordNotFoundError(f"no task {task_id!r}") from None

    def list_tasks(self) -> list[TaskRecord]:
        return list(self._tasks.values())

    def patch_task(self, task_id: str, **fields: Any) -> TaskRecord:
        record = self.get_task(task_id)
        _apply_fields(record, fields)
        return record

    def set_task_status(self, task_id: str, status: str) -> TaskRecord:
        return self.patch_task(task_id, status=status)

    # -- mission records ---------------------------------------------------------------

    def create_mission(self, record: MissionRecord) -> MissionRecord:
        if record.id in self._missions:
            raise ValueError(f"mission {record.id!r} already exists")
        self._missions[record.id] = record
        return record

    def get_mission(self, mission_id: str) -> MissionRecord:
        try:
            return self._missions[mission_id]
        except KeyError:
            raise RecordNotFoundError(f"no mission {mission_id!r}") from None

    def list_missions(self) -> list[MissionRecord]:
        return list(self._missions.values())

    def patch_mission(self, mission_id: str, **fields: Any) -> MissionRecord:
        record = self.get_mission(mission_id)
        _apply_fields(record, fields)
        return record

    def set_mission_status(self, mission_id: str, status: str) -> MissionRecord:
        return self.patch_mission(mission_id, status=status)


# --------------------------------------------------------------------------------------
# SQLite adapter seam — the server binds a real connection; specs are stored as JSON.
# --------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_tasks (
    id         TEXT PRIMARY KEY,
    spec_json  TEXT NOT NULL,
    status     TEXT NOT NULL,
    section    TEXT,
    ghost_ids  TEXT,
    mission_id TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    paused     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tm_missions (
    id        TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    task_ids  TEXT NOT NULL,
    status    TEXT NOT NULL
);
"""


class SqliteTaskStore:
    """A stdlib-``sqlite3`` :class:`TaskStore` — the durable adapter seam the server binds.

    The caller passes an OPEN ``sqlite3.Connection`` (the server owns its lifecycle); this
    class only reads/writes its two tables. Specs are serialized with Pydantic
    ``model_dump_json`` / ``model_validate_json`` so the stored form is the validated spec.
    NO ``ghostopia_server`` / ``ghostcrawl`` import — the package never depends on the server.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- task records ------------------------------------------------------------------

    def create_task(self, record: TaskRecord) -> TaskRecord:
        try:
            self._conn.execute(
                "INSERT INTO tm_tasks "
                "(id, spec_json, status, section, ghost_ids, mission_id, attempts, "
                " error_code, paused) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.spec.model_dump_json(),
                    record.status,
                    record.section,
                    json.dumps(record.ghost_ids) if record.ghost_ids is not None else None,
                    record.mission_id,
                    record.attempts,
                    record.error_code,
                    int(record.paused),
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"task {record.id!r} already exists") from None
        self._conn.commit()
        return record

    def get_task(self, task_id: str) -> TaskRecord:
        row = self._conn.execute(
            "SELECT id, spec_json, status, section, ghost_ids, mission_id, attempts, "
            "error_code, paused FROM tm_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"no task {task_id!r}")
        return _row_to_task(row)

    def list_tasks(self) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT id, spec_json, status, section, ghost_ids, mission_id, attempts, "
            "error_code, paused FROM tm_tasks"
        ).fetchall()
        return [_row_to_task(row) for row in rows]

    def patch_task(self, task_id: str, **fields: Any) -> TaskRecord:
        record = self.get_task(task_id)
        _apply_fields(record, fields)
        self._conn.execute(
            "UPDATE tm_tasks SET spec_json=?, status=?, section=?, ghost_ids=?, "
            "mission_id=?, attempts=?, error_code=?, paused=? WHERE id=?",
            (
                record.spec.model_dump_json(),
                record.status,
                record.section,
                json.dumps(record.ghost_ids) if record.ghost_ids is not None else None,
                record.mission_id,
                record.attempts,
                record.error_code,
                int(record.paused),
                record.id,
            ),
        )
        self._conn.commit()
        return record

    def set_task_status(self, task_id: str, status: str) -> TaskRecord:
        return self.patch_task(task_id, status=status)

    # -- mission records ---------------------------------------------------------------

    def create_mission(self, record: MissionRecord) -> MissionRecord:
        try:
            self._conn.execute(
                "INSERT INTO tm_missions (id, spec_json, task_ids, status) VALUES (?,?,?,?)",
                (
                    record.id,
                    record.spec.model_dump_json(),
                    json.dumps(record.task_ids),
                    record.status,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"mission {record.id!r} already exists") from None
        self._conn.commit()
        return record

    def get_mission(self, mission_id: str) -> MissionRecord:
        row = self._conn.execute(
            "SELECT id, spec_json, task_ids, status FROM tm_missions WHERE id = ?",
            (mission_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"no mission {mission_id!r}")
        return _row_to_mission(row)

    def list_missions(self) -> list[MissionRecord]:
        rows = self._conn.execute(
            "SELECT id, spec_json, task_ids, status FROM tm_missions"
        ).fetchall()
        return [_row_to_mission(row) for row in rows]

    def patch_mission(self, mission_id: str, **fields: Any) -> MissionRecord:
        record = self.get_mission(mission_id)
        _apply_fields(record, fields)
        self._conn.execute(
            "UPDATE tm_missions SET spec_json=?, task_ids=?, status=? WHERE id=?",
            (
                record.spec.model_dump_json(),
                json.dumps(record.task_ids),
                record.status,
                record.id,
            ),
        )
        self._conn.commit()
        return record

    def set_mission_status(self, mission_id: str, status: str) -> MissionRecord:
        return self.patch_mission(mission_id, status=status)


def _row_to_task(row: Any) -> TaskRecord:
    return TaskRecord(
        id=row[0],
        spec=TaskSpec.model_validate_json(row[1]),
        status=row[2],
        section=row[3],
        ghost_ids=json.loads(row[4]) if row[4] is not None else None,
        mission_id=row[5],
        attempts=row[6],
        error_code=row[7],
        paused=bool(row[8]),
    )


def _row_to_mission(row: Any) -> MissionRecord:
    return MissionRecord(
        id=row[0],
        spec=MissionSpec.model_validate_json(row[1]),
        task_ids=json.loads(row[2]),
        status=row[3],
    )
