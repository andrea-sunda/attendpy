"""SQLite persistence layer for courses, lesson history, and status overrides."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from attendpy.config import AppConfig
from attendpy.models import CourseConfig, LessonEvent, LessonStatus


class Database:
    """Local SQLite store for course configurations and seen calendar lessons."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or AppConfig.db_path(ensure_exists=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    name TEXT PRIMARY KEY,
                    tracked INTEGER NOT NULL DEFAULT 1,
                    show_in_dashboard INTEGER NOT NULL DEFAULT 1,
                    theory_total_hours REAL NOT NULL DEFAULT 48.0,
                    theory_threshold_pct REAL NOT NULL DEFAULT 60.0,
                    has_lab INTEGER NOT NULL DEFAULT 0,
                    lab_total_hours REAL NOT NULL DEFAULT 24.0,
                    lab_threshold_pct REAL NOT NULL DEFAULT 80.0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seen_lessons (
                    uid TEXT PRIMARY KEY,
                    short_id TEXT NOT NULL,
                    raw_uid TEXT NOT NULL,
                    course_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    location TEXT NOT NULL,
                    external_code TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    lesson_type TEXT NOT NULL,
                    calendar_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'apple',
                    status_override TEXT NOT NULL DEFAULT '',
                    acknowledged_completed INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_seen_lessons_short_id
                    ON seen_lessons(short_id);
                CREATE INDEX IF NOT EXISTS idx_seen_lessons_course
                    ON seen_lessons(course_name);
                """
            )
            # Ensure backward-compatible migration if `show_in_dashboard` was added later
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(courses)").fetchall()
            }
            if "show_in_dashboard" not in cols:
                conn.execute(
                    "ALTER TABLE courses ADD COLUMN show_in_dashboard INTEGER NOT NULL DEFAULT 1"
                )

    # ------------------------------------------------------------------
    # Course Operations
    # ------------------------------------------------------------------

    def get_course(self, name: str) -> Optional[CourseConfig]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM courses WHERE UPPER(name) = UPPER(?)",
                (name.strip(),),
            ).fetchone()
            if not row:
                return None
            return self._row_to_course(row)

    def list_courses(self, only_tracked: bool = False) -> list[CourseConfig]:
        query = "SELECT * FROM courses"
        if only_tracked:
            query += " WHERE tracked = 1"
        query += " ORDER BY name ASC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
            return [self._row_to_course(r) for r in rows]

    def upsert_course(self, course: CourseConfig) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        created_at = course.created_at or now_iso
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO courses (
                    name, tracked, show_in_dashboard, theory_total_hours, theory_threshold_pct,
                    has_lab, lab_total_hours, lab_threshold_pct, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    tracked = excluded.tracked,
                    show_in_dashboard = excluded.show_in_dashboard,
                    theory_total_hours = excluded.theory_total_hours,
                    theory_threshold_pct = excluded.theory_threshold_pct,
                    has_lab = excluded.has_lab,
                    lab_total_hours = excluded.lab_total_hours,
                    lab_threshold_pct = excluded.lab_threshold_pct
                """,
                (
                    course.name.upper().strip(),
                    1 if course.tracked else 0,
                    1 if course.show_in_dashboard else 0,
                    float(course.theory_total_hours),
                    float(course.theory_threshold_pct),
                    1 if course.has_lab else 0,
                    float(course.lab_total_hours),
                    float(course.lab_threshold_pct),
                    created_at,
                ),
            )

    def delete_course(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM courses WHERE UPPER(name) = UPPER(?)",
                (name.strip(),),
            )
            return cur.rowcount > 0

    def reset_database(self) -> None:
        """Clear all courses and seen lessons from the database."""
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM courses;
                DELETE FROM seen_lessons;
                """
            )

    @staticmethod
    def _row_to_course(row: sqlite3.Row) -> CourseConfig:
        keys = row.keys()
        show_dash = bool(row["show_in_dashboard"]) if "show_in_dashboard" in keys else True
        return CourseConfig(
            name=row["name"],
            tracked=bool(row["tracked"]),
            show_in_dashboard=show_dash,
            theory_total_hours=float(row["theory_total_hours"]),
            theory_threshold_pct=float(row["theory_threshold_pct"]),
            has_lab=bool(row["has_lab"]),
            lab_total_hours=float(row["lab_total_hours"]),
            lab_threshold_pct=float(row["lab_threshold_pct"]),
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Lesson Sync & Tracking Operations
    # ------------------------------------------------------------------

    def sync_lessons(
        self,
        live_events: list[LessonEvent],
        now: Optional[datetime] = None,
        acknowledge_new: bool = True,
    ) -> list[LessonEvent]:
        """Synchronize live calendar events with local seen_lessons table.

        - Applies any local status_override if calendar wasn't directly moved.
        - If the user moved the event in the calendar (e.g. to Skipped Calendar),
          clears conflicting override and honors the calendar category.
        - Identifies lessons that completed since the last check (is_newly_completed=True).
        """
        ref_now = now or datetime.now()
        now_iso = ref_now.isoformat(timespec="seconds")

        with self._connect() as conn:
            existing_rows = {
                r["uid"]: r
                for r in conn.execute("SELECT * FROM seen_lessons").fetchall()
            }

            synced: list[LessonEvent] = []
            for ev in live_events:
                is_past = ev.end_time <= ref_now
                prev = existing_rows.get(ev.uid)

                if prev is not None:
                    override = prev["status_override"]
                    prev_cal = prev["calendar_name"]
                    # If the calendar changed externally (user moved event in Apple/Google Calendar),
                    # clear local override so the calendar move takes precedence
                    if prev_cal and prev_cal != ev.calendar_name:
                        override = ""

                    if override == LessonStatus.SKIPPED.value:
                        ev.status = LessonStatus.SKIPPED
                    elif override == LessonStatus.ATTENDED.value and is_past:
                        ev.status = LessonStatus.ATTENDED

                    was_ack = bool(prev["acknowledged_completed"])
                    if is_past and not was_ack and ev.status == LessonStatus.ATTENDED:
                        ev.is_newly_completed = True
                    else:
                        ev.is_newly_completed = False

                    new_ack = 1 if (was_ack or (is_past and acknowledge_new)) else 0
                    conn.execute(
                        """
                        UPDATE seen_lessons SET
                            short_id = ?,
                            raw_uid = ?,
                            course_name = ?,
                            title = ?,
                            description = ?,
                            location = ?,
                            external_code = ?,
                            start_time = ?,
                            end_time = ?,
                            lesson_type = ?,
                            calendar_name = ?,
                            provider = ?,
                            status_override = ?,
                            acknowledged_completed = ?,
                            last_seen_at = ?
                        WHERE uid = ?
                        """,
                        (
                            ev.short_id,
                            ev.raw_uid,
                            ev.course_name,
                            ev.title,
                            ev.description,
                            ev.location,
                            ev.external_code,
                            ev.start_time.isoformat(timespec="minutes"),
                            ev.end_time.isoformat(timespec="minutes"),
                            ev.lesson_type.value,
                            ev.calendar_name,
                            ev.provider,
                            override,
                            new_ack,
                            now_iso,
                            ev.uid,
                        ),
                    )
                else:
                    # First time seeing this lesson
                    if is_past and ev.status == LessonStatus.ATTENDED:
                        ev.is_newly_completed = True
                    new_ack = 1 if (is_past and acknowledge_new) else 0
                    conn.execute(
                        """
                        INSERT INTO seen_lessons (
                            uid, short_id, raw_uid, course_name, title, description,
                            location, external_code, start_time, end_time, lesson_type,
                            calendar_name, provider, status_override,
                            acknowledged_completed, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                        """,
                        (
                            ev.uid,
                            ev.short_id,
                            ev.raw_uid,
                            ev.course_name,
                            ev.title,
                            ev.description,
                            ev.location,
                            ev.external_code,
                            ev.start_time.isoformat(timespec="minutes"),
                            ev.end_time.isoformat(timespec="minutes"),
                            ev.lesson_type.value,
                            ev.calendar_name,
                            ev.provider,
                            new_ack,
                            now_iso,
                            now_iso,
                        ),
                    )
                synced.append(ev)

        return synced

    def set_lesson_status_override(
        self,
        identifier: str,
        status: LessonStatus,
        target_calendar: Optional[str] = None,
    ) -> Optional[dict]:
        """Set status override by short_id or uid and return the lesson record."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM seen_lessons
                WHERE LOWER(short_id) = LOWER(?) OR LOWER(uid) = LOWER(?) OR raw_uid = ?
                """,
                (identifier.strip(), identifier.strip(), identifier.strip()),
            ).fetchone()
            if not row:
                return None

            cal_name = target_calendar if target_calendar else row["calendar_name"]
            conn.execute(
                """
                UPDATE seen_lessons
                SET status_override = ?, calendar_name = ?, acknowledged_completed = 1
                WHERE uid = ?
                """,
                (status.value, cal_name, row["uid"]),
            )
            return dict(row)

    def find_lesson(self, identifier: str) -> Optional[dict]:
        """Find a seen lesson by short_id, full uid, or raw_uid."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM seen_lessons
                WHERE LOWER(short_id) = LOWER(?) OR LOWER(uid) = LOWER(?) OR raw_uid = ?
                """,
                (identifier.strip(), identifier.strip(), identifier.strip()),
            ).fetchone()
            return dict(row) if row else None
