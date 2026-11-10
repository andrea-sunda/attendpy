"""Abstract base interface for calendar providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from attendpy.models import LessonEvent


class CalendarProvider(ABC):
    """Base interface implemented by Apple, Google, and Hybrid calendar providers."""

    provider_name: str = "base"

    @abstractmethod
    def list_calendars(self) -> list[str]:
        """Return the names of all available calendar categories."""

    @abstractmethod
    def ensure_calendar_exists(self, calendar_name: str) -> tuple[bool, str]:
        """Verify if a calendar exists; if not, create it automatically.

        Returns:
            (success, status_message)
        """

    @abstractmethod
    def fetch_lessons(
        self,
        attended_calendar: str,
        skipped_calendar: str,
        lab_pattern: str = r"\b(LABORATORIO|LABORATORY|LAB)\b",
        now: Optional[datetime] = None,
    ) -> list[LessonEvent]:
        """Fetch and parse lessons from both the Attended and Skipped calendars."""

    @abstractmethod
    def move_event(
        self,
        raw_uid: str,
        title: str,
        start_time: datetime,
        from_calendar: str,
        to_calendar: str,
    ) -> tuple[bool, str]:
        """Move a calendar event from one calendar category to another."""
