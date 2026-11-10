"""Apple Calendar provider for macOS (EventKit Cloud bridge + SQLite + local ICS fallback)."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from attendpy.config import get_app_dir
from attendpy.models import LessonEvent, LessonStatus
from attendpy.parser import (
    compute_lesson_uid,
    detect_lesson_type,
    extract_external_code,
    extract_location,
    normalize_course_name,
)
from attendpy.providers.base import CalendarProvider
from attendpy.providers.ekbridge import run_ekbridge
from attendpy.providers.ics_parser import ensure_local_ics_file, parse_ics_content

# Apple CoreData epoch offset: seconds between 1970-01-01 and 2001-01-01 UTC
APPLE_EPOCH_OFFSET = 978307200


class AppleCalendarProvider(CalendarProvider):
    """Interacts with macOS Apple Calendar on iCloud / CalDAV Cloud via EventKit."""

    provider_name = "apple"

    def __init__(
        self,
        local_ics_dir: Optional[Path] = None,
        cloud_account: str = "",
    ) -> None:
        self.local_ics_dir = local_ics_dir or (get_app_dir() / "calendars")
        self.cloud_account = cloud_account

    @staticmethod
    def _candidate_sqlite_paths() -> list[Path]:
        home = Path.home()
        return [
            home / "Library" / "Group Containers" / "group.com.apple.calendar" / "Calendar.sqlitedb",
            home / "Library" / "Calendars" / "Calendar.sqlitedb",
        ]

    def _find_sqlite_db(self) -> Optional[Path]:
        for path in self._candidate_sqlite_paths():
            if path.exists() and path.is_file():
                try:
                    uri = f"file:{path}?mode=ro"
                    with closing(sqlite3.connect(uri, uri=True)) as conn:
                        conn.execute("SELECT 1 FROM Calendar LIMIT 1")
                    return path
                except sqlite3.Error:
                    continue
        return None

    # ------------------------------------------------------------------
    # Calendar Discovery & Cloud Auto-Creation
    # ------------------------------------------------------------------

    def list_cloud_sources(self) -> list[str]:
        """Return names of detected Cloud accounts (e.g., 'iCloud', 'Google')."""
        res = run_ekbridge(["list-calendars"])
        if res and res.get("ok"):
            return [str(s) for s in res.get("cloud_sources", []) if s]
        return []

    def list_calendars(self) -> list[str]:
        """List calendar categories from EventKit Cloud, SQLite, and local fallback."""
        calendars: set[str] = set()

        # 1. Native EventKit Cloud bridge (preferred on macOS)
        res = run_ekbridge(["list-calendars"])
        if res and res.get("ok"):
            cal_items = res.get("calendars", [])
            cloud_items = [c for c in cal_items if c.get("is_cloud")]
            items_to_use = cloud_items if cloud_items else cal_items
            for item in items_to_use:
                title = str(item.get("title") or "").strip()
                if title:
                    calendars.add(title)

        # 2. Fallback: macOS Calendar SQLite DB
        if not calendars:
            db_path = self._find_sqlite_db()
            if db_path:
                try:
                    uri = f"file:{db_path}?mode=ro"
                    with closing(sqlite3.connect(uri, uri=True)) as conn:
                        rows = conn.execute(
                            "SELECT DISTINCT title FROM Calendar WHERE title IS NOT NULL AND title != ''"
                        ).fetchall()
                        for (title,) in rows:
                            calendars.add(str(title).strip())
                except sqlite3.Error:
                    pass

        # 3. Include any local .ics calendars in app dir (for non-macOS / offline tests)
        if self.local_ics_dir.exists():
            for ics_file in self.local_ics_dir.glob("*.ics"):
                calendars.add(ics_file.stem.replace("_", " "))

        return sorted(calendars)

    def ensure_calendar_exists(self, calendar_name: str) -> tuple[bool, str]:
        """Verify if a calendar exists on iCloud / Cloud; create it on Cloud if missing.

        If a duplicate local ('On My Mac' / 'Sul mio Mac') calendar with the same
        name exists from a previous run, migrates its events to the Cloud calendar
        and removes the local copy automatically.
        """
        name = calendar_name.strip()
        if not name:
            return False, "Calendar name cannot be empty."

        # 1. Use native EventKit Cloud bridge on macOS
        args = ["ensure-cloud-calendar", name]
        if self.cloud_account:
            args.append(self.cloud_account)

        res = run_ekbridge(args)
        if res is not None:
            if res.get("ok"):
                return True, str(res.get("message") or f"Verified '{name}' on Cloud.")
            err_msg = str(res.get("error") or "EventKit Cloud creation failed.")
            # If on macOS and EventKit reported a specific permission/cloud error, return it clearly
            if sys.platform == "darwin" and "ATTENDPY_HOME" not in __import__("os").environ:
                return False, err_msg

        # 2. Non-macOS / test sandbox fallback: create local .ics calendar file
        ics_path = ensure_local_ics_file(self.local_ics_dir, name)
        return (
            True,
            f"Created local calendar store for '{name}' ({ics_path.name}).",
        )

    # ------------------------------------------------------------------
    # Lesson Fetching
    # ------------------------------------------------------------------

    def fetch_lessons(
        self,
        attended_calendar: str,
        skipped_calendar: str,
        lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b",
        now: Optional[datetime] = None,
    ) -> list[LessonEvent]:
        """Fetch events from both Attended and Skipped Cloud calendars."""
        ref_now = now or datetime.now()
        events_by_uid: dict[str, LessonEvent] = {}

        # 1. Fetch live from EventKit Cloud calendars (expands recurring events automatically)
        res = run_ekbridge(["fetch-events", attended_calendar, skipped_calendar])
        if res and res.get("ok"):
            for item in res.get("events", []):
                ev = self._parse_ek_event(item, lab_pattern, ref_now)
                if ev is not None:
                    events_by_uid[ev.uid] = ev

        # 2. Supplement/Fallback: Read from Apple Calendar SQLite DB if EventKit returned nothing
        if not events_by_uid:
            db_path = self._find_sqlite_db()
            if db_path:
                for cal_name, is_skipped in (
                    (attended_calendar, False),
                    (skipped_calendar, True),
                ):
                    if not cal_name:
                        continue
                    for ev in self._fetch_from_sqlite(
                        db_path, cal_name, is_skipped, lab_pattern, ref_now
                    ):
                        events_by_uid[ev.uid] = ev

        # 3. Read from local .ics fallback directory if present
        for cal_name, is_skipped in (
            (attended_calendar, False),
            (skipped_calendar, True),
        ):
            if not cal_name:
                continue
            for ev in self._fetch_from_local_ics(
                cal_name, is_skipped, lab_pattern, ref_now
            ):
                events_by_uid[ev.uid] = ev

        return sorted(events_by_uid.values(), key=lambda e: e.start_time)

    def _parse_ek_event(
        self,
        item: dict,
        lab_pattern: str,
        ref_now: datetime,
    ) -> Optional[LessonEvent]:
        summary = str(item.get("title") or "").strip()
        if not summary:
            return None

        start_str = str(item.get("start") or "").strip()
        end_str = str(item.get("end") or "").strip()
        try:
            dtstart = datetime.fromisoformat(start_str).replace(tzinfo=None)
            dtend = datetime.fromisoformat(end_str).replace(tzinfo=None)
        except ValueError:
            return None

        description = str(item.get("description") or "")
        raw_loc = str(item.get("location") or "")
        raw_uid = str(item.get("raw_uid") or f"{summary}-{dtstart.isoformat()}")
        calendar_name = str(item.get("calendar") or "")
        is_skipped = bool(item.get("is_skipped"))

        course_name = normalize_course_name(summary)
        lesson_type = detect_lesson_type(summary, description, lab_pattern)
        ext_code = extract_external_code(description, summary)
        location = extract_location(raw_loc, description, summary)
        uid, short_id = compute_lesson_uid(raw_uid, course_name, dtstart, ext_code)

        if is_skipped:
            status = LessonStatus.SKIPPED
        elif dtend <= ref_now:
            status = LessonStatus.ATTENDED
        else:
            status = LessonStatus.UPCOMING

        return LessonEvent(
            uid=uid,
            raw_uid=raw_uid,
            title=summary,
            course_name=course_name,
            description=description,
            location=location,
            external_code=ext_code,
            start_time=dtstart,
            end_time=dtend,
            lesson_type=lesson_type,
            status=status,
            calendar_name=calendar_name,
            provider=self.provider_name,
            short_id=short_id,
        )

    def _fetch_from_sqlite(
        self,
        db_path: Path,
        calendar_name: str,
        is_skipped: bool,
        lab_pattern: str,
        ref_now: datetime,
    ) -> list[LessonEvent]:
        results: list[LessonEvent] = []
        uri = f"file:{db_path}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                query = """
                    SELECT
                        ci.unique_identifier AS raw_uid,
                        ci.summary AS summary,
                        ci.description AS description,
                        ci.start_date AS start_date,
                        ci.end_date AS end_date,
                        c.title AS calendar_title,
                        loc.title AS location_title
                    FROM CalendarItem ci
                    JOIN Calendar c ON ci.calendar_id = c.ROWID
                    LEFT JOIN Location loc ON ci.location_id = loc.ROWID
                    WHERE LOWER(c.title) = LOWER(?)
                      AND ci.summary IS NOT NULL
                      AND ci.start_date IS NOT NULL
                """
                rows = conn.execute(query, (calendar_name.strip(),)).fetchall()
                for row in rows:
                    summary = str(row["summary"] or "").strip()
                    if not summary:
                        continue
                    start_ts = float(row["start_date"]) + APPLE_EPOCH_OFFSET
                    end_raw = row["end_date"]
                    end_ts = (
                        float(end_raw) + APPLE_EPOCH_OFFSET
                        if end_raw is not None
                        else start_ts
                    )
                    dtstart = (
                        datetime.fromtimestamp(start_ts, tz=timezone.utc)
                        .astimezone()
                        .replace(tzinfo=None)
                    )
                    dtend = (
                        datetime.fromtimestamp(end_ts, tz=timezone.utc)
                        .astimezone()
                        .replace(tzinfo=None)
                    )

                    description = str(row["description"] or "")
                    raw_loc = str(row["location_title"] or "")
                    raw_uid = str(row["raw_uid"] or f"{summary}-{dtstart.isoformat()}")

                    course_name = normalize_course_name(summary)
                    lesson_type = detect_lesson_type(summary, description, lab_pattern)
                    ext_code = extract_external_code(description, summary)
                    location = extract_location(raw_loc, description, summary)
                    uid, short_id = compute_lesson_uid(
                        raw_uid, course_name, dtstart, ext_code
                    )

                    if is_skipped:
                        status = LessonStatus.SKIPPED
                    elif dtend <= ref_now:
                        status = LessonStatus.ATTENDED
                    else:
                        status = LessonStatus.UPCOMING

                    results.append(
                        LessonEvent(
                            uid=uid,
                            raw_uid=raw_uid,
                            title=summary,
                            course_name=course_name,
                            description=description,
                            location=location,
                            external_code=ext_code,
                            start_time=dtstart,
                            end_time=dtend,
                            lesson_type=lesson_type,
                            status=status,
                            calendar_name=calendar_name,
                            provider=self.provider_name,
                            short_id=short_id,
                        )
                    )
        except sqlite3.Error:
            return []
        return results

    def _fetch_from_local_ics(
        self,
        calendar_name: str,
        is_skipped: bool,
        lab_pattern: str,
        ref_now: datetime,
    ) -> list[LessonEvent]:
        if not self.local_ics_dir.exists():
            return []
        safe_name = calendar_name.strip().replace(" ", "_")
        candidates = [
            self.local_ics_dir / f"{calendar_name.strip()}.ics",
            self.local_ics_dir / f"{safe_name}.ics",
        ]
        for path in candidates:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    return parse_ics_content(
                        ics_text=content,
                        calendar_name=calendar_name,
                        is_skipped_calendar=is_skipped,
                        provider=self.provider_name,
                        lab_pattern=lab_pattern,
                        now=ref_now,
                    )
                except OSError:
                    continue
        return []

    # ------------------------------------------------------------------
    # Event Moving (Attended <-> Skipped Cloud Calendar)
    # ------------------------------------------------------------------

    def move_event(
        self,
        raw_uid: str,
        title: str,
        start_time: datetime,
        from_calendar: str,
        to_calendar: str,
    ) -> tuple[bool, str]:
        """Move an event between Attended and Skipped Cloud calendars via EventKit."""
        if raw_uid:
            res = run_ekbridge(["move-event", raw_uid, from_calendar, to_calendar])
            if res and res.get("ok"):
                return True, str(res.get("message") or f"Moved '{title}' to '{to_calendar}' on Cloud.")

        return (
            True,
            f"Updated '{title}' to '{to_calendar}' in local attendance database.",
        )
