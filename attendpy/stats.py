"""Attendance calculation, lesson estimation, and time-series engine."""

from __future__ import annotations

import math
from typing import Optional

from attendpy.models import (
    ComponentStats,
    CourseConfig,
    CourseStats,
    LessonEvent,
    LessonStatus,
    LessonType,
)


def calculate_component_stats(
    lesson_type: LessonType,
    configured_total_hours: float,
    threshold_pct: float,
    lessons: list[LessonEvent],
) -> ComponentStats:
    """Calculate comprehensive attendance metrics for Theory or Laboratory."""
    attended = [e for e in lessons if e.status == LessonStatus.ATTENDED]
    skipped = [e for e in lessons if e.status == LessonStatus.SKIPPED]
    upcoming = [e for e in lessons if e.status == LessonStatus.UPCOMING]

    attended_hours = round(sum(e.duration_hours for e in attended), 2)
    skipped_hours = round(sum(e.duration_hours for e in skipped), 2)
    delivered_hours = round(attended_hours + skipped_hours, 2)
    scheduled_future_hours = round(sum(e.duration_hours for e in upcoming), 2)

    total_expected_hours = max(
        float(configured_total_hours),
        round(delivered_hours + scheduled_future_hours, 2),
        1.0,
    )
    remaining_hours = max(0.0, round(total_expected_hours - delivered_hours, 2))

    all_durations = [e.duration_hours for e in lessons if e.duration_hours > 0]
    avg_duration = (
        round(sum(all_durations) / len(all_durations), 2)
        if all_durations
        else 2.0
    )
    if avg_duration <= 0:
        avg_duration = 2.0

    estimated_remaining_lessons = (
        math.ceil(remaining_hours / avg_duration) if remaining_hours > 0 else 0
    )

    required_hours = round((threshold_pct / 100.0) * total_expected_hours, 2)

    current_attendance_pct = (
        round((attended_hours / delivered_hours) * 100.0, 1)
        if delivered_hours > 0
        else 100.0
    )
    overall_progress_pct = round(
        (attended_hours / total_expected_hours) * 100.0, 1
    )
    max_possible_pct = round(
        ((attended_hours + remaining_hours) / total_expected_hours) * 100.0, 1
    )

    hours_needed = max(0.0, round(required_hours - attended_hours, 2))
    lessons_needed = (
        math.ceil(hours_needed / avg_duration) if hours_needed > 0 else 0
    )

    skippable_hours_left = max(
        0.0, round((attended_hours + remaining_hours) - required_hours, 2)
    )
    skippable_lessons_left = (
        math.floor(skippable_hours_left / avg_duration)
        if skippable_hours_left > 0
        else 0
    )

    if attended_hours >= required_hours:
        status_label = "SAFE"
    elif max_possible_pct + 1e-6 < threshold_pct:
        status_label = "IMPOSSIBLE"
    elif delivered_hours > 0 and current_attendance_pct + 1e-6 < threshold_pct:
        status_label = "BELOW TARGET"
    elif skippable_lessons_left <= 1:
        status_label = "WARNING"
    else:
        status_label = "ON TRACK"

    return ComponentStats(
        lesson_type=lesson_type,
        total_expected_hours=total_expected_hours,
        threshold_pct=threshold_pct,
        required_hours=required_hours,
        attended_hours=attended_hours,
        skipped_hours=skipped_hours,
        delivered_hours=delivered_hours,
        scheduled_future_hours=scheduled_future_hours,
        remaining_hours=remaining_hours,
        attended_lessons_count=len(attended),
        skipped_lessons_count=len(skipped),
        scheduled_future_lessons_count=len(upcoming),
        avg_lesson_duration=avg_duration,
        estimated_remaining_lessons=estimated_remaining_lessons,
        current_attendance_pct=current_attendance_pct,
        overall_progress_pct=overall_progress_pct,
        max_possible_pct=max_possible_pct,
        hours_needed_for_threshold=hours_needed,
        lessons_needed_for_threshold=lessons_needed,
        skippable_hours_left=skippable_hours_left,
        skippable_lessons_left=skippable_lessons_left,
        status_label=status_label,
    )


def calculate_course_stats(
    course: CourseConfig,
    course_lessons: list[LessonEvent],
) -> CourseStats:
    """Compute statistics for a course (both Theory and optional Lab)."""
    sorted_lessons = sorted(course_lessons, key=lambda e: e.start_time)

    if course.has_lab:
        theory_lessons = [
            e for e in sorted_lessons if e.lesson_type == LessonType.THEORY
        ]
        lab_lessons = [
            e for e in sorted_lessons if e.lesson_type == LessonType.LAB
        ]
    else:
        theory_lessons = sorted_lessons
        lab_lessons = []

    theory_stats = calculate_component_stats(
        lesson_type=LessonType.THEORY,
        configured_total_hours=course.theory_total_hours,
        threshold_pct=course.theory_threshold_pct,
        lessons=theory_lessons,
    )

    lab_stats: Optional[ComponentStats] = None
    if course.has_lab:
        lab_stats = calculate_component_stats(
            lesson_type=LessonType.LAB,
            configured_total_hours=course.lab_total_hours,
            threshold_pct=course.lab_threshold_pct,
            lessons=lab_lessons,
        )

    return CourseStats(
        course=course,
        theory=theory_stats,
        lab=lab_stats,
        lessons=sorted_lessons,
    )


def build_attendance_series(
    lessons: list[LessonEvent],
    lesson_type: Optional[LessonType],
    total_expected_hours: float,
) -> list[tuple[str, float, float]]:
    """Build cumulative attendance trajectory for plotting on a Cartesian chart.

    Returns a list of (date_str, live_attendance_pct, total_progress_pct) for past lessons.
    """
    filtered = [
        e
        for e in sorted(lessons, key=lambda x: x.start_time)
        if e.status in (LessonStatus.ATTENDED, LessonStatus.SKIPPED)
        and (lesson_type is None or e.lesson_type == lesson_type)
    ]

    points: list[tuple[str, float, float]] = []
    cum_attended = 0.0
    cum_delivered = 0.0
    denom_total = max(total_expected_hours, 1.0)

    for ev in filtered:
        dur = ev.duration_hours
        cum_delivered += dur
        if ev.status == LessonStatus.ATTENDED:
            cum_attended += dur
        live_pct = (cum_attended / cum_delivered * 100.0) if cum_delivered > 0 else 100.0
        tot_pct = min(100.0, (cum_attended / denom_total) * 100.0)
        date_label = ev.start_time.strftime("%d/%m")
        points.append((date_label, round(live_pct, 1), round(tot_pct, 1)))

    return points
