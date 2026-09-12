from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from recurrence import matches_recurrence, next_occurrence, occurrence_dates

SCHEMA_VERSION = 3


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    color TEXT NOT NULL DEFAULT '#526d82',
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'P4',
                    status TEXT NOT NULL DEFAULT 'active',
                    tracking_mode TEXT NOT NULL DEFAULT 'both',
                    task_work_minutes INTEGER NOT NULL DEFAULT 25,
                    task_break_minutes INTEGER NOT NULL DEFAULT 5,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    folder_id INTEGER,
                    manual_seconds INTEGER NOT NULL DEFAULT 0,
                    pomodoro_estimate INTEGER NOT NULL DEFAULT 0,
                    pomodoro_completed INTEGER NOT NULL DEFAULT 0,
                    deadline TEXT,
                    recurrence_enabled INTEGER NOT NULL DEFAULT 0,
                    recurrence_kind TEXT NOT NULL DEFAULT 'days',
                    recurrence_interval INTEGER NOT NULL DEFAULT 1,
                    recurrence_weekdays TEXT NOT NULL DEFAULT '',
                    recurrence_start TEXT,
                    recurrence_end TEXT,
                    recurrence_max INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    archived_at TEXT
                );

                CREATE TABLE IF NOT EXISTS subtasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS task_occurrences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    occurrence_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    completed_at TEXT,
                    UNIQUE(task_id, occurrence_date)
                );

                CREATE TABLE IF NOT EXISTS recurrence_exceptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    UNIQUE(task_id, start_date, end_date)
                );

                CREATE TABLE IF NOT EXISTS work_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_date TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    worked_seconds INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(start_at, end_at)
                );

                CREATE TABLE IF NOT EXISTS daily_adjustments (
                    work_date TEXT PRIMARY KEY,
                    adjustment_seconds INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                    occurrence_date TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    planned_seconds INTEGER NOT NULL,
                    actual_seconds INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(started_at, ended_at, task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
                CREATE INDEX IF NOT EXISTS idx_occurrences_date ON task_occurrences(occurrence_date);
                CREATE INDEX IF NOT EXISTS idx_work_date ON work_sessions(work_date);
                CREATE INDEX IF NOT EXISTS idx_pomodoro_date ON pomodoro_sessions(occurrence_date);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if "task_work_minutes" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN task_work_minutes INTEGER NOT NULL DEFAULT 25")
            if "task_break_minutes" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN task_break_minutes INTEGER NOT NULL DEFAULT 5")
            if "display_order" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")
            if "folder_id" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN folder_id INTEGER")

            # Indexes that depend on migrated columns must be created only after
            # those columns exist. This keeps upgrades from older PiModoro DBs safe.
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_folder ON tasks(folder_id)")

            db.execute(
                "UPDATE tasks SET display_order = id * 10 WHERE display_order = 0"
            )
            migrated = db.execute(
                "SELECT value FROM settings WHERE key = 'today_scope_migrated_v1'"
            ).fetchone()
            if migrated is None:
                db.execute(
                    "UPDATE tasks SET deadline = ?, updated_at = ? "
                    "WHERE recurrence_enabled = 0 AND deadline IS NULL AND status != 'archived'",
                    (date.today().isoformat(), now_iso()),
                )
                db.execute(
                    "INSERT INTO settings(key, value) VALUES('today_scope_migrated_v1', 'true')"
                )

            db.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    # ---------- Settings ----------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        with self.transaction() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, payload),
            )

    # ---------- Project folders ----------

    def get_project_folders(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM project_folders ORDER BY position, name COLLATE NOCASE, id"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_project_folder(self, folder_id: int) -> dict[str, Any] | None:
        return row_dict(
            self.connection.execute(
                "SELECT * FROM project_folders WHERE id = ?", (int(folder_id),)
            ).fetchone()
        )

    def create_project_folder(self, name: str, color: str) -> int:
        clean = " ".join(str(name).split())
        if not clean:
            raise ValueError("Folder name cannot be empty")
        stamp = now_iso()
        row = self.connection.execute(
            "SELECT COALESCE(MAX(position), 0) + 10 AS next_position FROM project_folders"
        ).fetchone()
        position = int(row["next_position"] if row else 10)
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO project_folders(name, color, position, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (clean, str(color), position, stamp, stamp),
            )
        return int(cursor.lastrowid)

    def update_project_folder(self, folder_id: int, name: str, color: str) -> None:
        clean = " ".join(str(name).split())
        if not clean:
            raise ValueError("Folder name cannot be empty")
        with self.transaction() as db:
            db.execute(
                "UPDATE project_folders SET name = ?, color = ?, updated_at = ? WHERE id = ?",
                (clean, str(color), now_iso(), int(folder_id)),
            )

    def delete_project_folder(self, folder_id: int) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE tasks SET folder_id = NULL, updated_at = ? WHERE folder_id = ?",
                (now_iso(), int(folder_id)),
            )
            db.execute("DELETE FROM project_folders WHERE id = ?", (int(folder_id),))

    def tasks_for_folder(self, folder_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE folder_id = ? AND status != 'archived' "
            "ORDER BY CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline, display_order, created_at, id",
            (int(folder_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def assign_task_folder(self, task_id: int, folder_id: int | None) -> None:
        self.update_task(task_id, {"folder_id": folder_id})

    # ---------- Tasks ----------

    def create_task(self, values: Mapping[str, Any]) -> int:
        stamp = now_iso()
        fields = {
            "title": str(values.get("title", "")).strip(),
            "notes": str(values.get("notes", "")),
            "priority": str(values.get("priority", "P4")),
            "status": str(values.get("status", "active")),
            "tracking_mode": str(values.get("tracking_mode", "both")),
            "task_work_minutes": max(1, int(values.get("task_work_minutes", 25) or 25)),
            "task_break_minutes": max(1, int(values.get("task_break_minutes", 5) or 5)),
            "display_order": int(values.get("display_order") or self._next_task_order()),
            "folder_id": int(values["folder_id"]) if values.get("folder_id") not in (None, "", 0, "0") else None,
            "manual_seconds": max(0, int(values.get("manual_seconds", 0) or 0)),
            "pomodoro_estimate": max(0, int(values.get("pomodoro_estimate", 0) or 0)),
            "pomodoro_completed": max(0, int(values.get("pomodoro_completed", 0) or 0)),
            "deadline": values.get("deadline") or None,
            "recurrence_enabled": int(bool(values.get("recurrence_enabled", False))),
            "recurrence_kind": str(values.get("recurrence_kind", "days")),
            "recurrence_interval": max(1, int(values.get("recurrence_interval", 1) or 1)),
            "recurrence_weekdays": str(values.get("recurrence_weekdays", "")),
            "recurrence_start": values.get("recurrence_start") or None,
            "recurrence_end": values.get("recurrence_end") or None,
            "recurrence_max": values.get("recurrence_max") or None,
            "created_at": values.get("created_at") or stamp,
            "updated_at": stamp,
        }
        if not fields["title"]:
            raise ValueError("Task title cannot be empty")
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        with self.transaction() as db:
            cursor = db.execute(
                f"INSERT INTO tasks({columns}) VALUES({placeholders})",
                tuple(fields.values()),
            )
            task_id = int(cursor.lastrowid)
        for index, text in enumerate(values.get("subtasks", []) or []):
            clean = str(text).strip()
            if clean:
                self.add_subtask(task_id, clean, position=index)
        return task_id

    def update_task(self, task_id: int, values: Mapping[str, Any]) -> None:
        allowed = {
            "title",
            "notes",
            "priority",
            "tracking_mode",
            "task_work_minutes",
            "task_break_minutes",
            "display_order",
            "folder_id",
            "manual_seconds",
            "pomodoro_estimate",
            "pomodoro_completed",
            "deadline",
            "recurrence_enabled",
            "recurrence_kind",
            "recurrence_interval",
            "recurrence_weekdays",
            "recurrence_start",
            "recurrence_end",
            "recurrence_max",
            "status",
        }
        updates: dict[str, Any] = {key: values[key] for key in allowed if key in values}
        if "title" in updates:
            updates["title"] = str(updates["title"]).strip()
            if not updates["title"]:
                raise ValueError("Task title cannot be empty")
        for key in ("manual_seconds", "pomodoro_estimate", "pomodoro_completed", "display_order"):
            if key in updates:
                updates[key] = max(0, int(updates[key] or 0))
        for key in ("task_work_minutes", "task_break_minutes"):
            if key in updates:
                updates[key] = max(1, min(720, int(updates[key] or 1)))
        if "recurrence_enabled" in updates:
            updates["recurrence_enabled"] = int(bool(updates["recurrence_enabled"]))
        if "recurrence_interval" in updates:
            updates["recurrence_interval"] = max(1, int(updates["recurrence_interval"] or 1))
        for key in ("deadline", "recurrence_start", "recurrence_end", "recurrence_max", "folder_id"):
            if key in updates and updates[key] in ("", 0, "0"):
                updates[key] = None
        if "folder_id" in updates and updates["folder_id"] is not None:
            updates["folder_id"] = int(updates["folder_id"])
        updates["updated_at"] = now_iso()
        clause = ", ".join(f"{key} = ?" for key in updates)
        with self.transaction() as db:
            db.execute(
                f"UPDATE tasks SET {clause} WHERE id = ?",
                (*updates.values(), task_id),
            )

    def _next_task_order(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(display_order), 0) + 10 AS next_order FROM tasks"
        ).fetchone()
        return int(row["next_order"] if row else 10)

    def set_task_order(self, task_ids: Iterable[int]) -> None:
        ordered = [int(task_id) for task_id in task_ids]
        with self.transaction() as db:
            for position, task_id in enumerate(ordered, start=1):
                db.execute(
                    "UPDATE tasks SET display_order = ?, updated_at = ? WHERE id = ?",
                    (position * 10, now_iso(), task_id),
                )

    def replace_subtasks(self, task_id: int, texts: Iterable[str]) -> None:
        clean = [str(text).strip() for text in texts if str(text).strip()]
        with self.transaction() as db:
            db.execute("DELETE FROM subtasks WHERE task_id = ?", (task_id,))
            db.executemany(
                "INSERT INTO subtasks(task_id, text, done, position) VALUES(?, ?, 0, ?)",
                [(task_id, text, index) for index, text in enumerate(clean)],
            )

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return row_dict(
            self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        )

    def get_exceptions(self, task_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM recurrence_exceptions WHERE task_id = ? ORDER BY start_date",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_exceptions(self, task_id: int, ranges: Iterable[tuple[str, str]]) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM recurrence_exceptions WHERE task_id = ?", (task_id,))
            db.executemany(
                "INSERT OR IGNORE INTO recurrence_exceptions(task_id, start_date, end_date) "
                "VALUES(?, ?, ?)",
                [(task_id, start, end) for start, end in ranges],
            )

    def get_active_tasks(self, target: date) -> list[dict[str, Any]]:
        """Return only tasks scheduled for the requested day, including completed ones for strike-through."""
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE status != 'archived' ORDER BY display_order, created_at, id"
        ).fetchall()
        result: list[dict[str, Any]] = []
        target_iso = target.isoformat()
        for raw in rows:
            task = dict(raw)
            task["display_done"] = task.get("status") == "completed"
            if task["recurrence_enabled"]:
                exceptions = self.get_exceptions(task["id"])
                occurrence = self.connection.execute(
                    "SELECT status FROM task_occurrences WHERE task_id = ? AND occurrence_date = ?",
                    (task["id"], target_iso),
                ).fetchone()
                due_today = matches_recurrence(task, target, exceptions)
                completed_today = bool(occurrence and occurrence["status"] == "completed")
                if not due_today and not completed_today:
                    continue
                task["display_done"] = completed_today
            else:
                if str(task.get("deadline") or "") != target_iso:
                    continue
            result.append(task)
        return result

    def due_tasks_for_range(self, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {}
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE status = 'active' AND recurrence_enabled = 1 "
            "ORDER BY display_order, created_at, id"
        ).fetchall()
        for raw in rows:
            task = dict(raw)
            exceptions = self.get_exceptions(task["id"])
            for due_date in occurrence_dates(task, start, end, exceptions):
                occurrence = self.connection.execute(
                    "SELECT status FROM task_occurrences WHERE task_id = ? AND occurrence_date = ?",
                    (task["id"], due_date.isoformat()),
                ).fetchone()
                if occurrence and occurrence["status"] == "completed":
                    continue
                output.setdefault(due_date.isoformat(), []).append(task)
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE status = 'active' AND recurrence_enabled = 0 "
            "AND deadline BETWEEN ? AND ? ORDER BY display_order, created_at, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for raw in rows:
            task = dict(raw)
            output.setdefault(task["deadline"], []).append(task)
        return output

    def calendar_tasks_for_range(self, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {}
        recurring = self.connection.execute(
            "SELECT * FROM tasks WHERE status != 'archived' AND recurrence_enabled = 1 "
            "ORDER BY display_order, created_at, id"
        ).fetchall()
        for raw in recurring:
            task = dict(raw)
            exceptions = self.get_exceptions(int(task["id"]))
            for due_date in occurrence_dates(task, start, end, exceptions):
                occurrence = self.connection.execute(
                    "SELECT status FROM task_occurrences WHERE task_id = ? AND occurrence_date = ?",
                    (task["id"], due_date.isoformat()),
                ).fetchone()
                item = dict(task)
                item["occurrence_date"] = due_date.isoformat()
                item["occurrence_completed"] = bool(
                    occurrence and occurrence["status"] == "completed"
                )
                output.setdefault(due_date.isoformat(), []).append(item)

        one_off = self.connection.execute(
            "SELECT * FROM tasks WHERE status != 'archived' AND recurrence_enabled = 0 "
            "AND deadline BETWEEN ? AND ? ORDER BY display_order, created_at, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for raw in one_off:
            task = dict(raw)
            task["occurrence_date"] = task["deadline"]
            task["occurrence_completed"] = task.get("status") == "completed"
            output.setdefault(str(task["deadline"]), []).append(task)
        return output

    def complete_task(self, task_id: int, occurrence_date: date | None = None) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        stamp = now_iso()
        with self.transaction() as db:
            if task["recurrence_enabled"]:
                day = (occurrence_date or date.today()).isoformat()
                db.execute(
                    "INSERT INTO task_occurrences(task_id, occurrence_date, status, completed_at) "
                    "VALUES(?, ?, 'completed', ?) "
                    "ON CONFLICT(task_id, occurrence_date) DO UPDATE SET "
                    "status = 'completed', completed_at = excluded.completed_at",
                    (task_id, day, stamp),
                )
                occurrence_day = date.fromisoformat(day)
                future = next_occurrence(
                    task, occurrence_day + timedelta(days=1), self.get_exceptions(task_id)
                )
                if future is None and (task.get("recurrence_end") or task.get("recurrence_max")):
                    db.execute(
                        "UPDATE tasks SET status = 'completed', completed_at = ?, updated_at = ? "
                        "WHERE id = ?",
                        (stamp, stamp, task_id),
                    )
            else:
                db.execute(
                    "UPDATE tasks SET status = 'completed', completed_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (stamp, stamp, task_id),
                )

    def reopen_task(self, task_id: int, occurrence_date: date | None = None) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        with self.transaction() as db:
            if task["recurrence_enabled"]:
                day = (occurrence_date or date.today()).isoformat()
                db.execute(
                    "DELETE FROM task_occurrences WHERE task_id = ? AND occurrence_date = ?",
                    (task_id, day),
                )
                db.execute(
                    "UPDATE tasks SET status = 'active', completed_at = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (now_iso(), task_id),
                )
            else:
                db.execute(
                    "UPDATE tasks SET status = 'active', completed_at = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (now_iso(), task_id),
                )

    def archive_completed_before(self, day: date) -> int:
        """Archive completed one-off tasks after the calendar day they were created."""
        stamp = now_iso()
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE tasks SET status = 'archived', archived_at = ?, updated_at = ? "
                "WHERE status = 'completed' AND recurrence_enabled = 0 "
                "AND substr(created_at, 1, 10) < ?",
                (stamp, stamp, day.isoformat()),
            )
        return int(cursor.rowcount or 0)

    def archive_task(self, task_id: int) -> None:
        stamp = now_iso()
        with self.transaction() as db:
            db.execute(
                "UPDATE tasks SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, task_id),
            )

    def delete_task(self, task_id: int) -> None:
        """Permanently delete a task and its dependent task records."""
        with self.transaction() as db:
            db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def restore_task(self, task_id: int) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE tasks SET status = 'active', archived_at = NULL, completed_at = NULL, "
                "updated_at = ? WHERE id = ?",
                (now_iso(), task_id),
            )

    def add_subtask(self, task_id: int, text: str, position: int | None = None) -> int:
        if position is None:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM subtasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            position = int(row["next"])
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO subtasks(task_id, text, position) VALUES(?, ?, ?)",
                (task_id, text.strip(), position),
            )
            return int(cursor.lastrowid)

    def get_subtasks(self, task_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY position, id", (task_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def toggle_subtask(self, subtask_id: int, done: bool) -> None:
        with self.transaction() as db:
            db.execute("UPDATE subtasks SET done = ? WHERE id = ?", (int(done), subtask_id))

    def delete_subtask(self, subtask_id: int) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))

    def set_task_notes(self, task_id: int, notes: str) -> None:
        self.update_task(task_id, {"notes": notes})

    def adjust_task_time(self, task_id: int, seconds: int) -> None:
        task = self.get_task(task_id)
        if task:
            self.update_task(task_id, {"manual_seconds": max(0, int(task["manual_seconds"]) + seconds)})

    # ---------- Work clock ----------

    def add_work_session(
        self,
        work_date: str,
        start_at: str,
        end_at: str,
        worked_seconds: int,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO work_sessions(work_date, start_at, end_at, worked_seconds, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (work_date, start_at, end_at, max(0, int(worked_seconds)), now_iso()),
            )

    def base_work_seconds(self, day: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(worked_seconds), 0) AS total FROM work_sessions WHERE work_date = ?",
            (day,),
        ).fetchone()
        return int(row["total"])

    def work_seconds(self, day: str) -> int:
        base = self.base_work_seconds(day)
        row = self.connection.execute(
            "SELECT adjustment_seconds FROM daily_adjustments WHERE work_date = ?", (day,)
        ).fetchone()
        return max(0, base + (int(row["adjustment_seconds"]) if row else 0))

    def set_daily_total(self, day: str, total_seconds: int) -> None:
        adjustment = max(0, int(total_seconds)) - self.base_work_seconds(day)
        with self.transaction() as db:
            db.execute(
                "INSERT INTO daily_adjustments(work_date, adjustment_seconds, updated_at) "
                "VALUES(?, ?, ?) ON CONFLICT(work_date) DO UPDATE SET "
                "adjustment_seconds = excluded.adjustment_seconds, updated_at = excluded.updated_at",
                (day, adjustment, now_iso()),
            )

    def work_totals(self, start: date, end: date) -> dict[str, int]:
        result: dict[str, int] = {}
        cursor = start
        while cursor <= end:
            result[cursor.isoformat()] = self.work_seconds(cursor.isoformat())
            cursor += timedelta(days=1)
        return result

    def work_sessions(self, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM work_sessions"
        params: list[Any] = []
        where: list[str] = []
        if start:
            where.append("work_date >= ?")
            params.append(start.isoformat())
        if end:
            where.append("work_date <= ?")
            params.append(end.isoformat())
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY start_at DESC"
        return [dict(row) for row in self.connection.execute(query, params).fetchall()]

    # ---------- Pomodoro ----------

    def add_pomodoro_session(
        self,
        task_id: int | None,
        started_at: str,
        ended_at: str,
        planned_seconds: int,
        actual_seconds: int,
        completed: bool,
    ) -> bool:
        occurrence_date = ended_at[:10]
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO pomodoro_sessions(" 
                "task_id, occurrence_date, started_at, ended_at, planned_seconds, actual_seconds, completed" 
                ") VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    occurrence_date,
                    started_at,
                    ended_at,
                    int(planned_seconds),
                    int(actual_seconds),
                    int(completed),
                ),
            )
            inserted = cursor.rowcount > 0
            if inserted and task_id is not None and completed:
                task = db.execute(
                    "SELECT tracking_mode FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task and task["tracking_mode"] in ("pomodoro", "both"):
                    db.execute(
                        "UPDATE tasks SET pomodoro_completed = pomodoro_completed + 1, "
                        "updated_at = ? WHERE id = ?",
                        (now_iso(), task_id),
                    )
            return inserted

    def pomodoro_totals(self, start: date, end: date) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT occurrence_date, COALESCE(SUM(actual_seconds), 0) AS total "
            "FROM pomodoro_sessions WHERE completed = 1 AND occurrence_date BETWEEN ? AND ? "
            "GROUP BY occurrence_date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        result = {dict(row)["occurrence_date"]: int(dict(row)["total"]) for row in rows}
        cursor = start
        while cursor <= end:
            result.setdefault(cursor.isoformat(), 0)
            cursor += timedelta(days=1)
        return result

    # ---------- History and CSV helpers ----------

    def search_history(
        self,
        search: str = "",
        status: str = "all",
        priority: str = "all",
        recurring: str = "all",
        deadline: str = "all",
        date_from: str = "",
        date_to: str = "",
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where: list[str] = []
        if search.strip():
            where.append("(LOWER(title) LIKE ? OR LOWER(notes) LIKE ?)")
            needle = f"%{search.strip().lower()}%"
            params.extend([needle, needle])
        if priority != "all":
            where.append("priority = ?")
            params.append(priority)
        if recurring == "yes":
            where.append("recurrence_enabled = 1")
        elif recurring == "no":
            where.append("recurrence_enabled = 0")
        if status in {"active", "completed", "archived"}:
            where.append("status = ?")
            params.append(status)
        today_iso = date.today().isoformat()
        if deadline == "overdue":
            where.append("deadline IS NOT NULL AND deadline < ? AND status = 'active'")
            params.append(today_iso)
        elif deadline == "today":
            where.append("deadline = ?")
            params.append(today_iso)
        elif deadline == "has":
            where.append("deadline IS NOT NULL")
        elif deadline == "none":
            where.append("deadline IS NULL")
        if date_from:
            where.append("date(created_at) >= ?")
            params.append(date_from)
        if date_to:
            where.append("date(created_at) <= ?")
            params.append(date_to)
        query = "SELECT * FROM tasks"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY updated_at DESC, id DESC"
        result = [dict(row) | {"record_type": "task"} for row in self.connection.execute(query, params)]

        occurrence_query = (
            "SELECT t.*, o.occurrence_date, o.completed_at AS occurrence_completed_at "
            "FROM task_occurrences o JOIN tasks t ON t.id = o.task_id WHERE o.status = 'completed'"
        )
        occurrence_params: list[Any] = []
        occurrence_where: list[str] = []
        if status in ("all", "completed"):
            if search.strip():
                occurrence_where.append("(LOWER(t.title) LIKE ? OR LOWER(t.notes) LIKE ?)")
                needle = f"%{search.strip().lower()}%"
                occurrence_params.extend([needle, needle])
            if priority != "all":
                occurrence_where.append("t.priority = ?")
                occurrence_params.append(priority)
            if recurring == "no":
                occurrence_where.append("0")
            if deadline == "overdue":
                occurrence_where.append("t.deadline IS NOT NULL AND t.deadline < ?")
                occurrence_params.append(today_iso)
            elif deadline == "today":
                occurrence_where.append("t.deadline = ?")
                occurrence_params.append(today_iso)
            elif deadline == "has":
                occurrence_where.append("t.deadline IS NOT NULL")
            elif deadline == "none":
                occurrence_where.append("t.deadline IS NULL")
            if date_from:
                occurrence_where.append("o.occurrence_date >= ?")
                occurrence_params.append(date_from)
            if date_to:
                occurrence_where.append("o.occurrence_date <= ?")
                occurrence_params.append(date_to)
            if occurrence_where:
                occurrence_query += " AND " + " AND ".join(occurrence_where)
            occurrence_query += " ORDER BY o.occurrence_date DESC"
            result.extend(
                dict(row) | {"record_type": "occurrence", "status": "completed"}
                for row in self.connection.execute(occurrence_query, occurrence_params)
            )
        result.sort(
            key=lambda item: item.get("occurrence_completed_at")
            or item.get("updated_at")
            or item.get("created_at")
            or "",
            reverse=True,
        )
        return result

    def all_tasks(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM tasks ORDER BY id")]

    def all_pomodoros(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT p.*, t.title AS task_title FROM pomodoro_sessions p "
                "LEFT JOIN tasks t ON t.id = p.task_id ORDER BY p.started_at"
            )
        ]
