"""Lesson list inspection and interactive skip/restore operations."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from attendpy.config import AppConfig
from attendpy.db import Database
from attendpy.models import LessonEvent, LessonStatus, LessonType
from attendpy.providers import get_provider
from attendpy.ui.interactive import interactive_lessons_selector
from attendpy.ui.rich_compat import Panel, Table, box, console


def render_lessons_table(
    lessons: list[LessonEvent],
    title: str = "Course Lessons (Attended, Skipped, and Upcoming)",
    course_filter: Optional[str] = None,
) -> None:
    """Display a detailed table of individual lessons with Theory/Lab, time, date, and status."""
    filtered = lessons
    if course_filter:
        cf = course_filter.strip().upper()
        filtered = [e for e in lessons if cf in e.course_name.upper()]

    if not filtered:
        console.print(
            Panel(
                "[yellow]No matching lessons found.[/]",
                title=f"[bold]{title}[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        return

    table = Table(
        title=f"[bold bright_white]{title}[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("ID", style="bold cyan", width=10)
    table.add_column("Date", width=11)
    table.add_column("Time", width=13)
    table.add_column("Hours", justify="right", width=6)
    table.add_column("Course", style="bold bright_white", min_width=20)
    table.add_column("Type", justify="center", width=11)
    table.add_column("Status", justify="center", width=12)
    table.add_column("Calendar", width=16)
    table.add_column("Location / Code", style="dim")

    for ev in sorted(filtered, key=lambda x: x.start_time):
        t_style = "bold magenta" if ev.lesson_type == LessonType.LAB else "bold cyan"
        if ev.status == LessonStatus.ATTENDED:
            s_badge = "[bold green]ATTENDED[/]"
        elif ev.status == LessonStatus.SKIPPED:
            s_badge = "[bold yellow]SKIPPED[/]"
        else:
            s_badge = "[dim]UPCOMING[/]"

        loc_info = ev.location or ""
        if ev.external_code:
            loc_info = f"{loc_info} \\[{ev.external_code}]".strip()

        new_tag = " [bold yellow]*NEW*[/]" if ev.is_newly_completed else ""

        table.add_row(
            ev.short_id,
            ev.start_time.strftime("%Y-%m-%d"),
            f"{ev.start_time.strftime('%H:%M')}–{ev.end_time.strftime('%H:%M')}",
            f"{ev.duration_hours:.1f}h",
            f"{ev.course_name}{new_tag}",
            f"[{t_style}]{ev.lesson_type.label}[/]",
            s_badge,
            ev.calendar_name,
            loc_info or "-",
        )

    console.print(table)


def skip_lesson_by_id(
    db: Database,
    config: AppConfig,
    lesson_id: str,
) -> tuple[bool, str]:
    """Mark a lesson as SKIPPED and move it to the Skipped Calendar."""
    record = db.set_lesson_status_override(
        identifier=lesson_id,
        status=LessonStatus.SKIPPED,
        target_calendar=config.skipped_calendar,
    )
    if not record:
        return False, f"No lesson found matching ID '{lesson_id}'."

    provider = get_provider(config)
    start_dt = datetime.fromisoformat(record["start_time"])
    _, prov_msg = provider.move_event(
        raw_uid=record["raw_uid"],
        title=record["title"],
        start_time=start_dt,
        from_calendar=config.attended_calendar,
        to_calendar=config.skipped_calendar,
    )
    return (
        True,
        f"Lesson \\[{record['short_id']}] '{record['course_name']}' ({record['start_time']}) "
        f"marked as SKIPPED ({prov_msg})",
    )


def restore_lesson_by_id(
    db: Database,
    config: AppConfig,
    lesson_id: str,
) -> tuple[bool, str]:
    """Restore a skipped lesson back to ATTENDED and move it to the Attended Calendar."""
    record = db.set_lesson_status_override(
        identifier=lesson_id,
        status=LessonStatus.ATTENDED,
        target_calendar=config.attended_calendar,
    )
    if not record:
        return False, f"No lesson found matching ID '{lesson_id}'."

    provider = get_provider(config)
    start_dt = datetime.fromisoformat(record["start_time"])
    _, prov_msg = provider.move_event(
        raw_uid=record["raw_uid"],
        title=record["title"],
        start_time=start_dt,
        from_calendar=config.skipped_calendar,
        to_calendar=config.attended_calendar,
    )
    return (
        True,
        f"Lesson \\[{record['short_id']}] '{record['course_name']}' ({record['start_time']}) "
        f"restored to ATTENDED ({prov_msg})",
    )


def toggle_lesson_event_status(
    db: Database,
    config: AppConfig,
    ev: LessonEvent,
) -> tuple[bool, str]:
    """Toggle a lesson event between ATTENDED and SKIPPED in-place and in DB/Calendar."""
    if ev.status == LessonStatus.SKIPPED:
        ok, msg = restore_lesson_by_id(db, config, ev.short_id)
        if ok:
            ev.status = LessonStatus.ATTENDED
            ev.calendar_name = config.attended_calendar
            ev.is_newly_completed = False
        return ok, msg
    else:
        ok, msg = skip_lesson_by_id(db, config, ev.short_id)
        if ok:
            ev.status = LessonStatus.SKIPPED
            ev.calendar_name = config.skipped_calendar
            ev.is_newly_completed = False
        return ok, msg


def interactive_skip_or_restore_prompt(
    db: Database,
    config: AppConfig,
    lessons: list[LessonEvent],
    only_new: bool = False,
    initial_course_filter: Optional[str] = None,
) -> None:
    """Launch the arrow-key interactive lesson selector and status toggler."""
    title = (
        "Review Newly Completed Lessons (↑/↓ & Space/Enter to Toggle)"
        if only_new
        else "Interactive Lessons Browser & Status Editor (↑/↓ & Space/Enter)"
    )
    interactive_lessons_selector(
        db=db,
        config=config,
        lessons=lessons,
        toggle_callback=toggle_lesson_event_status,
        title=title,
        only_new=only_new,
        initial_course_filter=initial_course_filter,
    )
