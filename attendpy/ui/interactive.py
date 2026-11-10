"""Arrow-key interactive TUI selectors and live status editors for attendpy."""

from __future__ import annotations

import os
import select
import sys
from typing import Callable, Optional

from attendpy.config import AppConfig
from attendpy.db import Database
from attendpy.models import CourseConfig, LessonEvent, LessonStatus, LessonType
from attendpy.ui.rich_compat import (
    FloatPrompt,
    Group,
    Live,
    Panel,
    Prompt,
    Table,
    Text,
    box,
    clear_terminal_screen,
    console,
)


def read_key() -> str:
    """Read a single keypress from stdin (supports arrows, Space, Enter, Esc).

    Restores normal terminal attributes immediately after reading so Rich Live
    rendering is never affected by raw terminal flags.
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            return "ESC"
        stripped = line.strip()
        return stripped if stripped else "ENTER"

    if sys.platform == "win32":  # pragma: no cover
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(ch2, "")
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == " ":
            return "SPACE"
        if ch == "\x1b":
            return "ESC"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch

    import termios

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    try:
        new_attrs = termios.tcgetattr(fd)
        # Disable canonical mode and echo on input only; keep output flags intact
        new_attrs[3] = new_attrs[3] & ~(termios.ICANON | termios.ECHO)
        new_attrs[6][termios.VMIN] = 1
        new_attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)

        b1 = os.read(fd, 1).decode("utf-8", errors="ignore")
        if b1 == "\x03":
            raise KeyboardInterrupt
        if b1 in ("\r", "\n"):
            return "ENTER"
        if b1 == " ":
            return "SPACE"
        if b1 in ("\x7f", "\x08"):
            return "BACKSPACE"
        if b1 == "\x1b":
            rlist, _, _ = select.select([fd], [], [], 0.04)
            if not rlist:
                return "ESC"
            b2 = os.read(fd, 1).decode("utf-8", errors="ignore")
            if b2 in ("[", "O"):
                rlist2, _, _ = select.select([fd], [], [], 0.04)
                if rlist2:
                    b3 = os.read(fd, 1).decode("utf-8", errors="ignore")
                    if b3 == "A":
                        return "UP"
                    if b3 == "B":
                        return "DOWN"
                    if b3 == "C":
                        return "RIGHT"
                    if b3 == "D":
                        return "LEFT"
                    if b3 in ("5", "6"):
                        # Consume trailing '~' for PageUp / PageDown
                        select.select([fd], [], [], 0.02)
                        os.read(fd, 1)
                        return "PGUP" if b3 == "5" else "PGDN"
            return "ESC"
        return b1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def select_from_list(
    title: str,
    options: list[tuple[str, str]],
    default_value: Optional[str] = None,
) -> str:
    """Interactive arrow-key single choice selector.

    Args:
        title: Title of the selection panel.
        options: List of (value, label) tuples.
        default_value: Initially highlighted option value.
    """
    if not options:
        return ""
    if not sys.stdin.isatty():
        return default_value or options[0][0]

    idx = 0
    if default_value:
        for i, (val, _) in enumerate(options):
            if val.lower() == default_value.lower():
                idx = i
                break

    def _render() -> Panel:
        tbl = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
        tbl.add_column("Cursor", width=3)
        tbl.add_column("Option")
        for i, (val, label) in enumerate(options):
            is_sel = i == idx
            cur = "[bold cyan]>[/]" if is_sel else " "
            style = "bold bright_white on rgb(25,45,75)" if is_sel else ""
            tbl.add_row(cur, f"[bold cyan]{val}[/] — {label}", style=style)
        return Panel(
            tbl,
            title=f"[bold bright_white]{title}[/]",
            subtitle="[dim]↑/↓ Move   •   Enter/Space Confirm[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )

    with Live(_render(), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = read_key()
            if key in ("UP", "k"):
                idx = (idx - 1) % len(options)
                live.update(_render(), refresh=True)
            elif key in ("DOWN", "j"):
                idx = (idx + 1) % len(options)
                live.update(_render(), refresh=True)
            elif key in ("ENTER", "SPACE"):
                return options[idx][0]
            elif key in ("ESC", "q"):
                return options[idx][0]


def interactive_lessons_selector(
    db: Database,
    config: AppConfig,
    lessons: list[LessonEvent],
    toggle_callback: Callable[[Database, AppConfig, LessonEvent], tuple[bool, str]],
    title: str = "Interactive Lessons Manager",
    only_new: bool = False,
    initial_course_filter: Optional[str] = None,
) -> None:
    """Interactive arrow-key lesson browser and status toggler.

    - Up/Down arrows scroll through lessons.
    - Space or Enter toggles a lesson between ATTENDED (green) and SKIPPED (red)
      immediately and updates its calendar.
    - 'f' cycles through course filters.
    - 't' cycles through Theory / Lab filters.
    - 'q' or Esc returns to the main menu.
    """
    base_lessons = (
        [e for e in lessons if e.is_newly_completed] if only_new else list(lessons)
    )
    base_lessons.sort(key=lambda x: x.start_time)

    if not base_lessons:
        console.print(
            Panel(
                "[yellow]No lessons available in this view.[/]",
                title=f"[bold]{title}[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        return

    all_courses = sorted({e.course_name for e in base_lessons})
    course_filters: list[Optional[str]] = [None, *all_courses]
    c_filter_idx = 0
    if initial_course_filter:
        for i, cf in enumerate(course_filters):
            if cf and initial_course_filter.upper() in cf.upper():
                c_filter_idx = i
                break

    type_filters: list[Optional[LessonType]] = [None, LessonType.THEORY, LessonType.LAB]
    t_filter_idx = 0

    cursor = 0
    page_size = 14
    last_feedback = "Use ↑/↓ to navigate, press Space or Enter to toggle status (Attended ⇄ Skipped)."

    def _filtered_list() -> list[LessonEvent]:
        cf = course_filters[c_filter_idx]
        tf = type_filters[t_filter_idx]
        return [
            e
            for e in base_lessons
            if (cf is None or e.course_name == cf)
            and (tf is None or e.lesson_type == tf)
        ]

    def _render() -> Panel:
        nonlocal cursor
        items = _filtered_list()
        if not items:
            cursor = 0
        else:
            cursor = max(0, min(cursor, len(items) - 1))

        cf_label = course_filters[c_filter_idx] or "ALL COURSES"
        tf_obj = type_filters[t_filter_idx]
        tf_label = tf_obj.label if tf_obj else "ALL TYPES"

        filter_bar = Text()
        filter_bar.append("  Filter Course [f]: ", style="dim")
        filter_bar.append(f"{cf_label}", style="bold cyan")
        filter_bar.append("   •   Filter Type [t]: ", style="dim")
        filter_bar.append(f"{tf_label}", style="bold magenta")
        filter_bar.append(f"   •   Total: {len(items)} lesson(s)\n", style="dim")

        table = Table(
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("", width=2)
        table.add_column("Date", width=10)
        table.add_column("Time", width=11)
        table.add_column("Hrs", justify="right", width=5)
        table.add_column("Course", ratio=3, min_width=16)
        table.add_column("Type", justify="center", width=8)
        table.add_column("Status (Space/Enter)", justify="center", width=18)
        table.add_column("Calendar", width=15)

        if not items:
            table.add_row(
                "",
                "-",
                "-",
                "-",
                "[yellow]No lessons match current filter[/]",
                "-",
                "-",
                "-",
            )
        else:
            start_idx = max(
                0,
                min(
                    cursor - page_size // 2,
                    max(0, len(items) - page_size),
                ),
            )
            end_idx = min(len(items), start_idx + page_size)

            for i in range(start_idx, end_idx):
                ev = items[i]
                is_sel = i == cursor
                ptr = "[bold cyan]>[/]" if is_sel else " "
                t_badge = (
                    "[bold magenta]Lab[/]"
                    if ev.lesson_type == LessonType.LAB
                    else "[bold cyan]Theory[/]"
                )

                if ev.status == LessonStatus.ATTENDED:
                    status_badge = (
                        "[bold white on green] ATTENDED [/]"
                        if is_sel
                        else "[bold green]ATTENDED[/]"
                    )
                elif ev.status == LessonStatus.SKIPPED:
                    status_badge = (
                        "[bold white on red] SKIPPED  [/]"
                        if is_sel
                        else "[bold red]SKIPPED[/]"
                    )
                else:
                    status_badge = (
                        "[bold black on yellow] UPCOMING [/]"
                        if is_sel
                        else "[yellow]UPCOMING[/]"
                    )

                row_style = "bold on rgb(25,45,75)" if is_sel else ""
                new_dot = " [bold yellow]*[/]" if ev.is_newly_completed else ""

                table.add_row(
                    ptr,
                    ev.start_time.strftime("%Y-%m-%d"),
                    f"{ev.start_time.strftime('%H:%M')}–{ev.end_time.strftime('%H:%M')}",
                    f"{ev.duration_hours:.1f}h",
                    f"{ev.course_name}{new_dot}",
                    t_badge,
                    status_badge,
                    ev.calendar_name,
                    style=row_style,
                )

        footer = Text()
        footer.append(f"\n  {last_feedback}\n", style="bold bright_white")
        footer.append(
            "  [↑/↓] Navigate   [Space/Enter] Toggle Status (Attended ⇄ Skipped)   "
            "[f] Filter Course   [t] Filter Type   [q/Esc] Back",
            style="dim",
        )

        return Panel(
            Group(filter_bar, table, footer),
            title=f"[bold bright_white]{title}[/]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )

    clear_terminal_screen()
    with Live(_render(), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = read_key()
            items = _filtered_list()
            if key in ("q", "Q", "ESC"):
                break
            elif key in ("UP", "k") and items:
                cursor = (cursor - 1) % len(items)
                live.update(_render(), refresh=True)
            elif key in ("DOWN", "j") and items:
                cursor = (cursor + 1) % len(items)
                live.update(_render(), refresh=True)
            elif key == "PGUP" and items:
                cursor = max(0, cursor - page_size)
                live.update(_render(), refresh=True)
            elif key == "PGDN" and items:
                cursor = min(len(items) - 1, cursor + page_size)
                live.update(_render(), refresh=True)
            elif key in ("f", "F"):
                c_filter_idx = (c_filter_idx + 1) % len(course_filters)
                cursor = 0
                live.update(_render(), refresh=True)
            elif key in ("t", "T"):
                t_filter_idx = (t_filter_idx + 1) % len(type_filters)
                cursor = 0
                live.update(_render(), refresh=True)
            elif key in ("SPACE", "ENTER") and items:
                selected_ev = items[cursor]
                ok, msg = toggle_callback(db, config, selected_ev)
                last_feedback = f"{'[OK]' if ok else '[ERR]'} {msg}"
                live.update(_render(), refresh=True)


def interactive_view_filter_selector(db: Database, config: AppConfig) -> None:
    """Arrow-key interactive selector for Dashboard course visibility and charts."""
    courses = db.list_courses(only_tracked=True)
    if not courses:
        console.print("[yellow]No tracked courses available to configure.[/]")
        return

    # Row 0..N-1 = courses; Row N = Cartesian Charts toggle
    cursor = 0
    total_rows = len(courses) + 1
    feedback = "Use ↑/↓ and Space/Enter to toggle visibility."

    def _render() -> Panel:
        table = Table(
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("", width=2)
        table.add_column("Item / Course Name", ratio=3)
        table.add_column("Dashboard Status (Space/Enter)", justify="center", width=22)

        for i, c in enumerate(courses):
            is_sel = i == cursor
            ptr = "[bold cyan]>[/]" if is_sel else " "
            if c.show_in_dashboard:
                badge = (
                    "[bold white on green] SHOWN  [/]"
                    if is_sel
                    else "[bold green]SHOWN[/]"
                )
            else:
                badge = (
                    "[bold white on red] HIDDEN [/]"
                    if is_sel
                    else "[bold red]HIDDEN[/]"
                )
            row_style = "bold on rgb(25,45,75)" if is_sel else ""
            table.add_row(ptr, c.name, badge, style=row_style)

        # Charts toggle row
        is_chart_sel = cursor == len(courses)
        ptr_c = "[bold cyan]>[/]" if is_chart_sel else " "
        if config.show_charts:
            c_badge = (
                "[bold white on green] CHARTS ON  [/]"
                if is_chart_sel
                else "[bold green]CHARTS ON[/]"
            )
        else:
            c_badge = (
                "[bold white on red] CHARTS OFF [/]"
                if is_chart_sel
                else "[bold red]CHARTS OFF[/]"
            )
        table.add_row(
            ptr_c,
            "[bold cyan]Display Cartesian Attendance Charts[/]",
            c_badge,
            style="bold on rgb(25,45,75)" if is_chart_sel else "",
        )

        footer = Text()
        footer.append(f"\n  {feedback}\n", style="bold bright_white")
        footer.append(
            "  [↑/↓] Navigate   [Space/Enter] Toggle Status   [a] Show All   [n] Hide All   [q/Esc] Done",
            style="dim",
        )
        return Panel(
            Group(table, footer),
            title="[bold bright_white]Dashboard View Filter (Select Courses & Charts to Show)[/]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )

    clear_terminal_screen()
    with Live(_render(), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = read_key()
            if key in ("q", "Q", "ESC"):
                break
            elif key in ("UP", "k"):
                cursor = (cursor - 1) % total_rows
                live.update(_render(), refresh=True)
            elif key in ("DOWN", "j"):
                cursor = (cursor + 1) % total_rows
                live.update(_render(), refresh=True)
            elif key in ("a", "A"):
                for c in courses:
                    c.show_in_dashboard = True
                    db.upsert_course(c)
                feedback = "All courses set to SHOWN in Dashboard."
                live.update(_render(), refresh=True)
            elif key in ("n", "N"):
                for c in courses:
                    c.show_in_dashboard = False
                    db.upsert_course(c)
                feedback = "All courses set to HIDDEN in Dashboard."
                live.update(_render(), refresh=True)
            elif key in ("SPACE", "ENTER"):
                if cursor < len(courses):
                    target = courses[cursor]
                    target.show_in_dashboard = not target.show_in_dashboard
                    db.upsert_course(target)
                    st = "SHOWN" if target.show_in_dashboard else "HIDDEN"
                    feedback = f"'{target.name}' is now {st} in Dashboard."
                else:
                    config.show_charts = not config.show_charts
                    config.save()
                    st = "ON" if config.show_charts else "OFF"
                    feedback = f"Cartesian Charts are now {st}."
                live.update(_render(), refresh=True)


def _interactive_single_course_editor(
    db: Database,
    course: CourseConfig,
) -> None:
    """Arrow-key editor for a single course's toggles, hours, and thresholds."""
    cursor = 0
    num_fields = 8
    feedback = "Use ↑/↓ to select field, Space/Enter to toggle or edit, ←/→ to adjust numbers, q/Esc to save & exit."

    def _render() -> Panel:
        tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan", expand=True)
        tbl.add_column("", width=2)
        tbl.add_column("Property", ratio=2)
        tbl.add_column("Current Value (Space / Enter / ← →)", justify="center", width=26)

        rows = [
            (
                "1. Attendance Tracking",
                "[bold white on green] TRACKED [/]"
                if course.tracked
                else "[bold white on red] IGNORED [/]",
            ),
            (
                "2. Show in Dashboard",
                "[bold white on green] SHOWN   [/]"
                if course.show_in_dashboard
                else "[bold white on red] HIDDEN  [/]",
            ),
            (
                "3. Total Theory Hours",
                f"[bold cyan]<  {course.theory_total_hours:.1f} h  >[/]",
            ),
            (
                "4. Theory Threshold %",
                f"[bold yellow]<  {course.theory_threshold_pct:.0f} %  >[/]",
            ),
            (
                "5. Laboratory Sessions",
                "[bold white on green] HAS LAB [/]"
                if course.has_lab
                else "[bold white on red] NO LAB  [/]",
            ),
            (
                "6. Total Laboratory Hours",
                f"[bold magenta]<  {course.lab_total_hours:.1f} h  >[/]"
                if course.has_lab
                else "[dim]— (Enable Lab first)[/]",
            ),
            (
                "7. Laboratory Threshold %",
                f"[bold bright_red]<  {course.lab_threshold_pct:.0f} %  >[/]"
                if course.has_lab
                else "[dim]— (Enable Lab first)[/]",
            ),
            (
                "8. Save & Return to Courses List",
                "[bold green][OK] Confirm & Back[/]",
            ),
        ]

        for i, (label, val_str) in enumerate(rows):
            is_sel = i == cursor
            ptr = "[bold cyan]>[/]" if is_sel else " "
            style = "bold on rgb(25,45,75)" if is_sel else ""
            tbl.add_row(ptr, label, val_str, style=style)

        footer = Text()
        footer.append(f"\n  {feedback}\n", style="bold bright_white")
        footer.append(
            "  [↑/↓] Move   [Space/Enter] Toggle / Type Value   [←/→] Adjust Hours/Threshold   [q/Esc] Save & Back",
            style="dim",
        )
        return Panel(
            Group(tbl, footer),
            title=f"[bold bright_white]Edit Course — {course.name}[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )

    clear_terminal_screen()
    with Live(_render(), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = read_key()
            if key in ("q", "Q", "ESC"):
                db.upsert_course(course)
                break
            elif key in ("UP", "k"):
                cursor = (cursor - 1) % num_fields
                live.update(_render(), refresh=True)
            elif key in ("DOWN", "j"):
                cursor = (cursor + 1) % num_fields
                live.update(_render(), refresh=True)
            elif key == "LEFT":
                if cursor == 2:
                    course.theory_total_hours = max(1.0, course.theory_total_hours - 1.0)
                elif cursor == 3:
                    course.theory_threshold_pct = max(0.0, course.theory_threshold_pct - 5.0)
                elif cursor == 5 and course.has_lab:
                    course.lab_total_hours = max(1.0, course.lab_total_hours - 1.0)
                elif cursor == 6 and course.has_lab:
                    course.lab_threshold_pct = max(0.0, course.lab_threshold_pct - 5.0)
                db.upsert_course(course)
                live.update(_render(), refresh=True)
            elif key == "RIGHT":
                if cursor == 2:
                    course.theory_total_hours += 1.0
                elif cursor == 3:
                    course.theory_threshold_pct = min(100.0, course.theory_threshold_pct + 5.0)
                elif cursor == 5 and course.has_lab:
                    course.lab_total_hours += 1.0
                elif cursor == 6 and course.has_lab:
                    course.lab_threshold_pct = min(100.0, course.lab_threshold_pct + 5.0)
                db.upsert_course(course)
                live.update(_render(), refresh=True)
            elif key in ("SPACE", "ENTER"):
                if cursor == 0:
                    course.tracked = not course.tracked
                    db.upsert_course(course)
                elif cursor == 1:
                    course.show_in_dashboard = not course.show_in_dashboard
                    db.upsert_course(course)
                elif cursor == 4:
                    course.has_lab = not course.has_lab
                    if course.has_lab and course.lab_total_hours <= 0:
                        course.lab_total_hours = 24.0
                    db.upsert_course(course)
                elif cursor == 7:
                    db.upsert_course(course)
                    break
                elif cursor in (2, 3, 5, 6):
                    if cursor in (5, 6) and not course.has_lab:
                        feedback = "Enable Laboratory Sessions (row 5) first."
                        live.update(_render(), refresh=True)
                        continue
                    live.stop()
                    try:
                        if cursor == 2:
                            course.theory_total_hours = max(
                                1.0,
                                FloatPrompt.ask(
                                    "  New Total Theory Hours",
                                    default=course.theory_total_hours,
                                ),
                            )
                        elif cursor == 3:
                            course.theory_threshold_pct = max(
                                0.0,
                                min(
                                    100.0,
                                    FloatPrompt.ask(
                                        "  New Theory Threshold %",
                                        default=course.theory_threshold_pct,
                                    ),
                                ),
                            )
                        elif cursor == 5:
                            course.lab_total_hours = max(
                                1.0,
                                FloatPrompt.ask(
                                    "  New Total Laboratory Hours",
                                    default=course.lab_total_hours,
                                ),
                            )
                        elif cursor == 6:
                            course.lab_threshold_pct = max(
                                0.0,
                                min(
                                    100.0,
                                    FloatPrompt.ask(
                                        "  New Laboratory Threshold %",
                                        default=course.lab_threshold_pct,
                                    ),
                                ),
                            )
                        db.upsert_course(course)
                        feedback = f"Updated '{course.name}'."
                    finally:
                        live.start()
                        live.update(_render(), refresh=True)
                    continue
                live.update(_render(), refresh=True)


def interactive_courses_editor(db: Database, config: AppConfig) -> None:
    """Arrow-key interactive course list manager."""
    courses = db.list_courses(only_tracked=False)
    if not courses:
        console.print("[yellow]No courses registered yet.[/]")
        return

    cursor = 0
    feedback = "Use ↑/↓ to select course, Space to toggle Tracking, 'v' for Dashboard visibility, Enter to edit details."

    def _render() -> Panel:
        table = Table(
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("", width=2)
        table.add_column("Course Name", ratio=3, min_width=18)
        table.add_column("Tracking (Space)", justify="center", width=16)
        table.add_column("Dashboard [v]", justify="center", width=15)
        table.add_column("Theory", justify="right", width=14)
        table.add_column("Lab [l]", justify="right", width=16)

        for i, c in enumerate(courses):
            is_sel = i == cursor
            ptr = "[bold cyan]>[/]" if is_sel else " "
            trk_badge = (
                ("[bold white on green] TRACKED [/]" if is_sel else "[bold green]TRACKED[/]")
                if c.tracked
                else ("[bold white on red] IGNORED [/]" if is_sel else "[bold red]IGNORED[/]")
            )
            vis_badge = (
                ("[bold white on green] SHOWN [/]" if is_sel else "[bold green]SHOWN[/]")
                if (c.tracked and c.show_in_dashboard)
                else ("[bold white on red] HIDDEN [/]" if is_sel else "[bold red]HIDDEN[/]")
            )
            theory_str = f"[cyan]{c.theory_total_hours:.0f}h ({c.theory_threshold_pct:.0f}%)[/]"
            lab_str = (
                f"[magenta]{c.lab_total_hours:.0f}h ({c.lab_threshold_pct:.0f}%)[/]"
                if c.has_lab
                else "[dim]No Lab[/]"
            )
            row_style = "bold on rgb(25,45,75)" if is_sel else ""
            table.add_row(ptr, c.name, trk_badge, vis_badge, theory_str, lab_str, style=row_style)

        footer = Text()
        footer.append(f"\n  {feedback}\n", style="bold bright_white")
        footer.append(
            "  [↑/↓] Navigate   [Enter] Edit Hours & Thresholds   [Space] Toggle Tracked   "
            "[v] Toggle Dashboard   [l] Toggle Lab   [q/Esc] Back",
            style="dim",
        )
        return Panel(
            Group(table, footer),
            title="[bold bright_white]Interactive Courses Manager[/]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )

    clear_terminal_screen()
    with Live(_render(), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = read_key()
            if key in ("q", "Q", "ESC"):
                break
            elif key in ("UP", "k"):
                cursor = (cursor - 1) % len(courses)
                live.update(_render(), refresh=True)
            elif key in ("DOWN", "j"):
                cursor = (cursor + 1) % len(courses)
                live.update(_render(), refresh=True)
            elif key == "SPACE":
                c = courses[cursor]
                c.tracked = not c.tracked
                db.upsert_course(c)
                st = "TRACKED" if c.tracked else "IGNORED"
                feedback = f"'{c.name}' is now {st}."
                live.update(_render(), refresh=True)
            elif key in ("v", "V"):
                c = courses[cursor]
                c.show_in_dashboard = not c.show_in_dashboard
                db.upsert_course(c)
                st = "SHOWN" if c.show_in_dashboard else "HIDDEN"
                feedback = f"'{c.name}' Dashboard visibility set to {st}."
                live.update(_render(), refresh=True)
            elif key in ("l", "L"):
                c = courses[cursor]
                c.has_lab = not c.has_lab
                if c.has_lab and c.lab_total_hours <= 0:
                    c.lab_total_hours = config.default_lab_hours
                db.upsert_course(c)
                st = "ENABLED" if c.has_lab else "DISABLED"
                feedback = f"Laboratory sessions {st} for '{c.name}'."
                live.update(_render(), refresh=True)
            elif key == "ENTER":
                live.stop()
                try:
                    _interactive_single_course_editor(db, courses[cursor])
                    feedback = f"Saved changes for '{courses[cursor].name}'."
                finally:
                    live.start()
                    live.update(_render(), refresh=True)
