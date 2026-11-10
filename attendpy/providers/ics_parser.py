"""Pure standard-library RFC 5545 iCalendar (.ics) parser and writer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from attendpy.models import LessonEvent, LessonStatus
from attendpy.parser import (
    compute_lesson_uid,
    detect_lesson_type,
    extract_external_code,
    extract_location,
    normalize_course_name,
)


def _unfold_ics_lines(content: str) -> list[str]:
    """Unfold multi-line RFC 5545 properties (lines starting with space or tab)."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    unfolded = re.sub(r"\n[ \t]", "", normalized)
    return [line for line in unfolded.split("\n") if line.strip()]


def _unescape_ics_text(value: str) -> str:
    """Unescape RFC 5545 escaped sequences in text fields."""
    return (
        value.replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .replace(r"\,", ",")
        .replace(r"\;", ";")
        .replace(r"\\", "\\")
        .strip()
    )


def _parse_ics_datetime(raw_val: str) -> Optional[datetime]:
    """Parse an iCalendar DTSTART/DTEND value into a local naive datetime."""
    val = raw_val.strip()
    is_utc = val.endswith("Z")
    if is_utc:
        val = val[:-1]

    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            dt = datetime.strptime(val, fmt)
            if is_utc:
                dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


def parse_ics_content(
    ics_text: str,
    calendar_name: str,
    is_skipped_calendar: bool,
    provider: str = "google",
    lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b",
    now: Optional[datetime] = None,
) -> list[LessonEvent]:
    """Parse raw .ics content into a list of LessonEvent instances."""
    ref_now = now or datetime.now()
    lines = _unfold_ics_lines(ics_text)

    events: list[LessonEvent] = []
    in_event = False
    props: dict[str, str] = {}

    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            props = {}
            continue
        if upper == "END:VEVENT":
            in_event = False
            event = _build_event_from_props(
                props=props,
                calendar_name=calendar_name,
                is_skipped_calendar=is_skipped_calendar,
                provider=provider,
                lab_pattern=lab_pattern,
                ref_now=ref_now,
            )
            if event is not None:
                events.append(event)
            continue

        if in_event and ":" in line:
            key_part, val_part = line.split(":", 1)
            prop_name = key_part.split(";", 1)[0].upper().strip()
            props[prop_name] = val_part

    return events


def _build_event_from_props(
    props: dict[str, str],
    calendar_name: str,
    is_skipped_calendar: bool,
    provider: str,
    lab_pattern: str,
    ref_now: datetime,
) -> Optional[LessonEvent]:
    summary = _unescape_ics_text(props.get("SUMMARY", ""))
    if not summary:
        return None

    dtstart = _parse_ics_datetime(props.get("DTSTART", ""))
    dtend = _parse_ics_datetime(props.get("DTEND", ""))
    if not dtstart:
        return None
    if not dtend or dtend <= dtstart:
        dtend = dtstart

    description = _unescape_ics_text(props.get("DESCRIPTION", ""))
    raw_loc = _unescape_ics_text(props.get("LOCATION", ""))
    raw_uid = _unescape_ics_text(props.get("UID", "")) or f"{summary}-{dtstart.isoformat()}"

    course_name = normalize_course_name(summary)
    lesson_type = detect_lesson_type(summary, description, lab_pattern)
    ext_code = extract_external_code(description, summary)
    location = extract_location(raw_loc, description, summary)
    uid, short_id = compute_lesson_uid(raw_uid, course_name, dtstart, ext_code)

    if is_skipped_calendar:
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
        provider=provider,
        short_id=short_id,
    )


def ensure_local_ics_file(ics_dir: Path, calendar_name: str) -> Path:
    """Create an empty valid .ics file for a local calendar if it doesn't exist."""
    ics_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", calendar_name.strip())
    file_path = ics_dir / f"{safe_name}.ics"
    if not file_path.exists():
        content = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//attendpy//University Attendance Tracker//EN\n"
            f"X-WR-CALNAME:{calendar_name}\n"
            "END:VCALENDAR\n"
        )
        file_path.write_text(content, encoding="utf-8")
    return file_path
