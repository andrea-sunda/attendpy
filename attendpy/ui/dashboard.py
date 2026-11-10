"""Dashboard rendering: summary banners, statistics tables, and Cartesian charts."""

from __future__ import annotations

from attendpy.config import AppConfig
from attendpy.models import ComponentStats, CourseStats, LessonEvent, LessonType
from attendpy.ui.chart import render_course_cartesian_chart
from attendpy.ui.rich_compat import Panel, Table, Text, box, console


def _format_progress_bar(pct: float, threshold: float, width: int = 8) -> Text:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    empty = max(0, width - filled)
    color = "green" if pct + 1e-6 >= threshold else "yellow"
    bar = Text()
    bar.append("█" * filled, style=f"bold {color}")
    bar.append("░" * empty, style="dim bright_black")
    bar.append(f" {pct:4.1f}%", style=f"bold {color}")
    return bar


def _format_status_badge(status: str) -> Text:
    styles = {
        "SAFE": "bold white on green",
        "ON TRACK": "bold green",
        "WARNING": "bold yellow",
        "BELOW TARGET": "bold bright_red",
        "IMPOSSIBLE": "bold white on red",
    }
    return Text(f" {status} ", style=styles.get(status, "bold"))


def render_newly_completed_banner(new_lessons: list[LessonEvent]) -> None:
    """Render a non-blocking notification panel for newly passed lessons."""
    if not new_lessons:
        return

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold yellow")
    table.add_column("ID", style="bold cyan", width=10)
    table.add_column("Date & Time", style="bright_white", width=18)
    table.add_column("Course", style="bold")
    table.add_column("Type", width=11)
    table.add_column("Hours", justify="right", width=7)
    table.add_column("Location", style="dim")

    for ev in new_lessons:
        t_style = "magenta" if ev.lesson_type == LessonType.LAB else "cyan"
        table.add_row(
            ev.short_id,
            f"{ev.start_time.strftime('%Y-%m-%d %H:%M')}",
            ev.course_name,
            f"[{t_style}]{ev.lesson_type.label}[/]",
            f"{ev.duration_hours:.1f}h",
            ev.location or "-",
        )

    console.print(
        Panel(
            table,
            title=f"[bold yellow]🔔 {len(new_lessons)} Newly Completed Lesson(s) Automatically Counted as Attended[/]",
            subtitle=r"[dim]Optional: Did you miss any of these? Press \[n] in the menu or run 'attendpy --skip <ID>' to move to Skipped Calendar[/]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )


def render_dashboard(
    config: AppConfig,
    all_stats: list[CourseStats],
    newly_completed: list[LessonEvent],
    show_charts: bool = True,
) -> None:
    """Render the full attendance dashboard, including tables and Cartesian charts."""
    header = (
        f"[bold cyan]attendpy[/]  •  Provider: [bold]{config.provider.upper()}[/]  •  "
        f"Attended Calendar: [bold green]{config.attended_calendar}[/]  •  "
        f"Skipped Calendar: [bold yellow]{config.skipped_calendar}[/]"
    )
    console.print(Panel(header, box=box.ROUNDED, border_style="cyan"))

    # Non-blocking notification for newly completed lessons since last run
    render_newly_completed_banner(newly_completed)

    if not all_stats:
        console.print(
            Panel(
                "[yellow]No tracked courses found in your calendar yet.[/]\n"
                f"Add lesson events to your [bold green]'{config.attended_calendar}'[/] calendar "
                "and launch [bold]attendpy[/] again.",
                title="[bold]No Courses Tracked[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        return

    table = Table(
        title="[bold bright_white]University Course Attendance Statistics (Hours & Estimations)[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
        expand=True,
    )
    table.add_column("Course", style="bold bright_white", ratio=3, min_width=14)
    table.add_column("Type", justify="center", ratio=1, min_width=6)
    table.add_column("Attended", justify="right", ratio=1, min_width=9)
    table.add_column("Skipped", justify="right", ratio=1, min_width=8)
    table.add_column("Est. Left", justify="right", ratio=1, min_width=10)
    table.add_column("Cur %", justify="right", ratio=1, min_width=6)
    table.add_column("Progress", justify="left", ratio=2, min_width=15)
    table.add_column("Max %", justify="right", ratio=1, min_width=6)
    table.add_column("Target", justify="right", ratio=1, min_width=9)
    table.add_column("Can Skip", justify="right", ratio=1, min_width=9)
    table.add_column("Status", justify="center", ratio=1, min_width=10)

    def _add_comp_row(course_title: str, comp: ComponentStats) -> None:
        comp_style = "bold magenta" if comp.lesson_type == LessonType.LAB else "bold cyan"
        type_short = "Lab" if comp.lesson_type == LessonType.LAB else "Theory"
        cur_style = (
            "bold green"
            if comp.current_attendance_pct + 1e-6 >= comp.threshold_pct
            else "bold red"
        )
        max_style = (
            "bold green"
            if comp.max_possible_pct + 1e-6 >= comp.threshold_pct
            else "bold red"
        )

        table.add_row(
            course_title,
            f"[{comp_style}]{type_short}[/]",
            f"[green]{comp.attended_hours:.1f}h[/] [dim]({comp.attended_lessons_count}L)[/]",
            f"[yellow]{comp.skipped_hours:.1f}h[/] [dim]({comp.skipped_lessons_count}L)[/]",
            f"{comp.remaining_hours:.1f}h [dim](~{comp.estimated_remaining_lessons}L)[/]",
            f"[{cur_style}]{comp.current_attendance_pct:.1f}%[/]",
            _format_progress_bar(comp.overall_progress_pct, comp.threshold_pct),
            f"[{max_style}]{comp.max_possible_pct:.1f}%[/]",
            f"{comp.threshold_pct:.0f}% [dim]({comp.required_hours:.1f}h)[/]",
            f"{comp.skippable_hours_left:.1f}h [dim](~{comp.skippable_lessons_left}L)[/]",
            _format_status_badge(comp.status_label),
        )

    for cs in all_stats:
        _add_comp_row(cs.course.name, cs.theory)
        if cs.course.has_lab and cs.lab is not None:
            _add_comp_row(f"[dim]↳ {cs.course.name}[/]", cs.lab)

    console.print(table)

    if show_charts:
        for cs in all_stats:
            console.print(render_course_cartesian_chart(cs))
