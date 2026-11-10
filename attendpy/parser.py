"""Event title and description parser for university lessons."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

from attendpy.models import LessonType

# Matches bracketed university IDs like [ES_ID_SCIENZE_2_437_1495673]
EXTERNAL_ID_RE = re.compile(r"\[([A-Za-z0-9_:-]+)\]")

# Matches laboratory keywords (Italian & English)
DEFAULT_LAB_RE = re.compile(r"\b(LABORATORIO|LABORATORY|LAB)\b", re.IGNORECASE)

# Matches trailing laboratory tags in course titles so Theory and Lab map to the same Course
TITLE_LAB_SUFFIX_RE = re.compile(
    r"\s*[-–—:/(\[]+\s*(?:LABORATORIO|LABORATORY|LAB)\s*[\])]?\s*$",
    re.IGNORECASE,
)


def normalize_course_name(raw_title: str) -> str:
    """Normalize a calendar event title into a canonical course name.

    Example:
        'LINGUAGGI DI PROGRAMMAZIONE - Laboratorio' -> 'LINGUAGGI DI PROGRAMMAZIONE'
        '  linguaggi di programmazione  ' -> 'LINGUAGGI DI PROGRAMMAZIONE'
    """
    if not raw_title:
        return "UNKNOWN COURSE"
    cleaned = EXTERNAL_ID_RE.sub("", raw_title).strip()
    cleaned = TITLE_LAB_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.upper() if cleaned else "UNKNOWN COURSE"


def detect_lesson_type(
    title: str,
    description: str = "",
    custom_pattern: Optional[str] = None,
) -> LessonType:
    """Classify a lesson as LAB or THEORY based on its description and title.

    Example event:
        Title: 'LINGUAGGI DI PROGRAMMAZIONE'
        Description: 'LINGUAGGI DI PROGRAMMAZIONE - LABORATORIO - Aula Magna ... [ES_ID_SCIENZE_2_437_1495673]'
    """
    regex = re.compile(custom_pattern, re.IGNORECASE) if custom_pattern else DEFAULT_LAB_RE
    combined = f"{title or ''}\n{description or ''}"
    if regex.search(combined):
        return LessonType.LAB
    return LessonType.THEORY


def extract_external_code(description: str = "", title: str = "") -> str:
    """Extract bracketed university course/event code such as [ES_ID_SCIENZE_2_437_1495673]."""
    for text in (description or "", title or ""):
        match = EXTERNAL_ID_RE.search(text)
        if match:
            return match.group(1)
    return ""


def extract_location(
    raw_location: str = "",
    description: str = "",
    title: str = "",
) -> str:
    """Extract classroom/building location from event metadata or description.

    Handles descriptions formatted like:
    'LINGUAGGI DI PROGRAMMAZIONE- Aula Magna Matematica - Palazzo delle Scienze[ES_ID_SCIENZE_2_437_1495673]'
    """
    if raw_location and raw_location.strip():
        return raw_location.strip()

    if not description:
        return ""

    desc_no_id = EXTERNAL_ID_RE.sub("", description).strip()
    # Remove leading course title prefix if repeated at the start of the description
    course = normalize_course_name(title)
    if course and desc_no_id.upper().startswith(course):
        desc_no_id = desc_no_id[len(course) :].lstrip(" -–—:\t")

    # Strip standalone LABORATORIO tags from location string while keeping room names
    parts = [p.strip() for p in re.split(r"\s*-\s*|\n+", desc_no_id) if p.strip()]
    filtered_parts = [p for p in parts if p.upper() not in ("LABORATORIO", "LABORATORY", "TEORIA", "THEORY")]
    return " - ".join(filtered_parts)


def compute_lesson_uid(
    raw_uid: str,
    course_name: str,
    start_time: datetime,
    external_code: str = "",
) -> tuple[str, str]:
    """Generate a deterministic UID and a human-friendly 8-character short ID for a lesson."""
    iso_start = start_time.strftime("%Y-%m-%dT%H:%M")
    base_key = f"{course_name}|{external_code or raw_uid}|{iso_start}"
    digest = hashlib.sha1(base_key.encode("utf-8")).hexdigest()
    short_id = digest[:8]
    full_uid = f"{course_name}:{iso_start}:{short_id}"
    return full_uid, short_id
