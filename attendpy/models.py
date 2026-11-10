"""Data models for attendpy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class LessonType(str, Enum):
    """Classification of a university lesson."""

    THEORY = "THEORY"
    LAB = "LAB"

    @property
    def label(self) -> str:
        return "Theory" if self == LessonType.THEORY else "Laboratory"


class LessonStatus(str, Enum):
    """Attendance status of a lesson."""

    ATTENDED = "ATTENDED"
    SKIPPED = "SKIPPED"
    UPCOMING = "UPCOMING"


@dataclass
class LessonEvent:
    """Represents a single calendar lesson event."""

    uid: str
    raw_uid: str
    title: str
    course_name: str
    description: str
    location: str
    external_code: str
    start_time: datetime
    end_time: datetime
    lesson_type: LessonType
    status: LessonStatus
    calendar_name: str
    provider: str = "apple"
    is_newly_completed: bool = False
    short_id: str = ""

    @property
    def duration_hours(self) -> float:
        """Return lesson duration in hours."""
        delta = (self.end_time - self.start_time).total_seconds() / 3600.0
        return max(0.0, round(delta, 2))


@dataclass
class CourseConfig:
    """Configuration and targets for a single university course."""

    name: str
    tracked: bool = True
    theory_total_hours: float = 48.0
    theory_threshold_pct: float = 60.0
    has_lab: bool = False
    lab_total_hours: float = 24.0
    lab_threshold_pct: float = 80.0
    created_at: str = ""


@dataclass
class ComponentStats:
    """Calculated attendance statistics for a course component (Theory or Lab)."""

    lesson_type: LessonType
    total_expected_hours: float
    threshold_pct: float
    required_hours: float
    attended_hours: float
    skipped_hours: float
    delivered_hours: float
    scheduled_future_hours: float
    remaining_hours: float
    attended_lessons_count: int
    skipped_lessons_count: int
    scheduled_future_lessons_count: int
    avg_lesson_duration: float
    estimated_remaining_lessons: int
    current_attendance_pct: float
    overall_progress_pct: float
    max_possible_pct: float
    hours_needed_for_threshold: float
    lessons_needed_for_threshold: int
    skippable_hours_left: float
    skippable_lessons_left: int
    status_label: str


@dataclass
class CourseStats:
    """Aggregated statistics and lessons for a course."""

    course: CourseConfig
    theory: ComponentStats
    lab: Optional[ComponentStats] = None
    lessons: list[LessonEvent] = field(default_factory=list)

    @property
    def total_attended_hours(self) -> float:
        lab_h = self.lab.attended_hours if self.lab else 0.0
        return round(self.theory.attended_hours + lab_h, 2)

    @property
    def total_expected_hours(self) -> float:
        lab_h = self.lab.total_expected_hours if self.lab else 0.0
        return round(self.theory.total_expected_hours + lab_h, 2)
