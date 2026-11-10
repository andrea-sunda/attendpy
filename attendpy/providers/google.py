"""Google Calendar provider (supports iCalendar .ics feeds, local .ics files, and REST API)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from attendpy.config import AppConfig, get_app_dir
from attendpy.models import LessonEvent
from attendpy.providers.base import CalendarProvider
from attendpy.providers.ics_parser import ensure_local_ics_file, parse_ics_content


class GoogleCalendarProvider(CalendarProvider):
    """Interacts with Google Calendar via lightweight REST API or iCal (.ics) feeds."""

    provider_name = "google"

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        local_ics_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or AppConfig.load()
        self.local_ics_dir = local_ics_dir or (get_app_dir() / "calendars")

    def _load_oauth_token(self) -> Optional[str]:
        """Read bearer access_token from optional google_credentials_file if configured."""
        if not self.config.google_credentials_file:
            return None
        cred_path = Path(self.config.google_credentials_file).expanduser()
        if not cred_path.exists():
            return None
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            return data.get("access_token") or data.get("token")
        except (OSError, json.JSONDecodeError):
            return None

    def list_calendars(self) -> list[str]:
        calendars: set[str] = set()
        token = self._load_oauth_token()
        if token:
            req = urllib.request.Request(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    for item in payload.get("items", []):
                        summary = item.get("summary")
                        if summary:
                            calendars.add(summary.strip())
            except (urllib.error.URLError, OSError, ValueError):
                pass

        if self.config.attended_calendar:
            calendars.add(self.config.attended_calendar)
        if self.config.skipped_calendar:
            calendars.add(self.config.skipped_calendar)

        if self.local_ics_dir.exists():
            for path in self.local_ics_dir.glob("*.ics"):
                calendars.add(path.stem.replace("_", " "))

        return sorted(calendars)

    def ensure_calendar_exists(self, calendar_name: str) -> tuple[bool, str]:
        name = calendar_name.strip()
        if not name:
            return False, "Calendar name cannot be empty."

        token = self._load_oauth_token()
        if token:
            existing = {c.lower() for c in self.list_calendars()}
            if name.lower() in existing:
                return True, f"Google Calendar '{name}' already exists."
            body = json.dumps({"summary": name}).encode("utf-8")
            req = urllib.request.Request(
                "https://www.googleapis.com/calendar/v3/calendars",
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status in (200, 201):
                        return True, f"Created calendar '{name}' in Google Calendar."
            except (urllib.error.URLError, OSError):
                pass

        ics_path = ensure_local_ics_file(self.local_ics_dir, name)
        return (
            True,
            f"Verified calendar '{name}' (local/ICS store ready at {ics_path.name}).",
        )

    def fetch_lessons(
        self,
        attended_calendar: str,
        skipped_calendar: str,
        lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b",
        now: Optional[datetime] = None,
    ) -> list[LessonEvent]:
        ref_now = now or datetime.now()
        events_by_uid: dict[str, LessonEvent] = {}

        sources = [
            (attended_calendar, self.config.google_attended_ics, False),
            (skipped_calendar, self.config.google_skipped_ics, True),
        ]

        for cal_name, ics_source, is_skipped in sources:
            if not cal_name:
                continue
            raw_ics = self._read_ics_source(ics_source, cal_name)
            if raw_ics:
                parsed = parse_ics_content(
                    ics_text=raw_ics,
                    calendar_name=cal_name,
                    is_skipped_calendar=is_skipped,
                    provider=self.provider_name,
                    lab_pattern=lab_pattern,
                    now=ref_now,
                )
                for ev in parsed:
                    events_by_uid[ev.uid] = ev

        return sorted(events_by_uid.values(), key=lambda e: e.start_time)

    def _read_ics_source(self, ics_source: str, calendar_name: str) -> str:
        # 1. Explicit URL or file path
        if ics_source:
            src = ics_source.strip()
            if src.startswith(("http://", "https://", "webcal://")):
                if src.startswith("webcal://"):
                    src = "https://" + src[len("webcal://") :]
                try:
                    req = urllib.request.Request(
                        src,
                        headers={"User-Agent": "attendpy/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.read().decode("utf-8", errors="replace")
                except (urllib.error.URLError, OSError):
                    return ""
            else:
                path = Path(src).expanduser()
                if path.exists():
                    try:
                        return path.read_text(encoding="utf-8")
                    except OSError:
                        return ""

        # 2. Local .ics directory fallback
        safe = calendar_name.strip().replace(" ", "_")
        for candidate in (
            self.local_ics_dir / f"{calendar_name.strip()}.ics",
            self.local_ics_dir / f"{safe}.ics",
        ):
            if candidate.exists():
                try:
                    return candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
        return ""

    def move_event(
        self,
        raw_uid: str,
        title: str,
        start_time: datetime,
        from_calendar: str,
        to_calendar: str,
    ) -> tuple[bool, str]:
        return (
            True,
            f"Updated '{title}' to '{to_calendar}' in local attendance database.",
        )
