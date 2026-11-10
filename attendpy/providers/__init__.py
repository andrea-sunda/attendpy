"""Factory and hybrid coordinator for calendar providers."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from attendpy.config import AppConfig
from attendpy.models import LessonEvent
from attendpy.providers.apple import AppleCalendarProvider
from attendpy.providers.base import CalendarProvider
from attendpy.providers.google import GoogleCalendarProvider


class HybridCalendarProvider(CalendarProvider):
    """Combines Apple Calendar and Google Calendar providers transparently."""

    provider_name = "hybrid"

    def __init__(
        self,
        apple: Optional[AppleCalendarProvider] = None,
        google: Optional[GoogleCalendarProvider] = None,
    ) -> None:
        self.apple = apple or AppleCalendarProvider()
        self.google = google or GoogleCalendarProvider()

    def list_calendars(self) -> list[str]:
        combined = set(self.apple.list_calendars()) | set(self.google.list_calendars())
        return sorted(combined)

    def ensure_calendar_exists(self, calendar_name: str) -> tuple[bool, str]:
        ok_a, msg_a = self.apple.ensure_calendar_exists(calendar_name)
        ok_g, msg_g = self.google.ensure_calendar_exists(calendar_name)
        return (ok_a or ok_g), f"{msg_a} | {msg_g}"

    def fetch_lessons(
        self,
        attended_calendar: str,
        skipped_calendar: str,
        lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b",
        now: Optional[datetime] = None,
    ) -> list[LessonEvent]:
        merged: dict[str, LessonEvent] = {}
        for ev in self.google.fetch_lessons(attended_calendar, skipped_calendar, lab_pattern, now):
            merged[ev.uid] = ev
        for ev in self.apple.fetch_lessons(attended_calendar, skipped_calendar, lab_pattern, now):
            merged[ev.uid] = ev
        return sorted(merged.values(), key=lambda e: e.start_time)

    def move_event(
        self,
        raw_uid: str,
        title: str,
        start_time: datetime,
        from_calendar: str,
        to_calendar: str,
    ) -> tuple[bool, str]:
        return self.apple.move_event(raw_uid, title, start_time, from_calendar, to_calendar)


def get_provider(config: AppConfig) -> CalendarProvider:
    """Instantiate the configured calendar provider ('apple', 'google', or 'hybrid')."""
    p = (config.provider or "apple").lower().strip()
    if p == "google":
        return GoogleCalendarProvider(config=config)
    if p == "hybrid":
        return HybridCalendarProvider(
            apple=AppleCalendarProvider(cloud_account=config.cloud_account),
            google=GoogleCalendarProvider(config=config),
        )
    return AppleCalendarProvider(cloud_account=config.cloud_account)
