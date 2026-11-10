"""Main interactive TUI application loop and synchronization coordinator."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from attendpy.config import AppConfig, reset_all_data
from attendpy.db import Database
from attendpy.models import CourseConfig, CourseStats, LessonEvent
from attendpy.providers import get_provider
from attendpy.stats import calculate_course_stats
from attendpy.ui.dashboard import render_dashboard
from attendpy.ui.lessons import (
    interactive_skip_or_restore_prompt,
    render_lessons_table,
)
from attendpy.ui.rich_compat import (
    Confirm,
    FloatPrompt,
    Panel,
    Prompt,
    Table,
    box,
    console,
)
from attendpy.ui.wizard import (
    discover_and_prompt_new_courses,
    run_initial_setup_wizard,
)


def sync_and_calculate(
    config: AppConfig,
    db: Database,
    interactive_discovery: bool = True,
    acknowledge_new: bool = True,
    course_filter: Optional[str] = None,
) -> tuple[list[CourseStats], list[LessonEvent], list[LessonEvent]]:
    """Fetch calendar events, discover new courses, and calculate all statistics."""
    provider = get_provider(config)

    # Ensure both Attended and Skipped calendars exist
    provider.ensure_calendar_exists(config.attended_calendar)
    provider.ensure_calendar_exists(config.skipped_calendar)

    live_events = provider.fetch_lessons(
        attended_calendar=config.attended_calendar,
        skipped_calendar=config.skipped_calendar,
        lab_pattern=config.lab_pattern,
    )

    synced_events = db.sync_lessons(
        live_events=live_events,
        acknowledge_new=acknowledge_new,
    )

    discover_and_prompt_new_courses(
        db=db,
        events=synced_events,
        config=config,
        interactive=interactive_discovery,
    )

    config.last_sync_at = datetime.now().isoformat(timespec="seconds")
    config.save()

    tracked_courses = db.list_courses(only_tracked=True)
    if course_filter:
        cf = course_filter.strip().upper()
        tracked_courses = [c for c in tracked_courses if cf in c.name.upper()]

    tracked_names = {c.name.upper() for c in tracked_courses}
    events_by_course: dict[str, list[LessonEvent]] = {}
    for ev in synced_events:
        events_by_course.setdefault(ev.course_name.upper(), []).append(ev)

    all_stats: list[CourseStats] = []
    for course in tracked_courses:
        c_events = events_by_course.get(course.name.upper(), [])
        all_stats.append(calculate_course_stats(course, c_events))

    newly_completed = [
        ev
        for ev in synced_events
        if ev.is_newly_completed and ev.course_name.upper() in tracked_names
    ]
    filtered_events = [
        ev for ev in synced_events if ev.course_name.upper() in tracked_names
    ]

    return all_stats, filtered_events, newly_completed


def interactive_manage_courses(db: Database, config: AppConfig) -> None:
    """Interactive menu to view and edit course hours, thresholds, and tracking status."""
    courses = db.list_courses(only_tracked=False)
    if not courses:
        console.print("[yellow]No courses registered yet.[/]")
        return

    table = Table(
        title="[bold bright_white]Registered University Courses[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("#", justify="right", style="bold cyan", width=4)
    table.add_column("Course Name", style="bold bright_white")
    table.add_column("Tracked", justify="center")
    table.add_column("In Dashboard", justify="center")
    table.add_column("Theory Hours", justify="right")
    table.add_column("Theory Threshold", justify="right")
    table.add_column("Has Lab", justify="center")
    table.add_column("Lab Hours", justify="right")
    table.add_column("Lab Threshold", justify="right")

    for idx, c in enumerate(courses, start=1):
        table.add_row(
            str(idx),
            c.name,
            "[green]Yes[/]" if c.tracked else "[red]Ignored[/]",
            "[green]Shown[/]" if (c.tracked and c.show_in_dashboard) else "[dim]Hidden[/]",
            f"{c.theory_total_hours:.1f}h",
            f"{c.theory_threshold_pct:.0f}%",
            "[magenta]Yes[/]" if c.has_lab else "[dim]No[/]",
            f"{c.lab_total_hours:.1f}h" if c.has_lab else "-",
            f"{c.lab_threshold_pct:.0f}%" if c.has_lab else "-",
        )
    console.print(table)

    choice = Prompt.ask(
        "\nEnter course number or name to edit (or press Enter to go back)",
        default="",
    ).strip()
    if not choice:
        return

    selected: Optional[CourseConfig] = None
    if choice.isdigit():
        num = int(choice)
        if 1 <= num <= len(courses):
            selected = courses[num - 1]
    else:
        for c in courses:
            if choice.upper() in c.name.upper():
                selected = c
                break

    if not selected:
        console.print(f"[red]Course '{choice}' not found.[/]")
        return

    console.print(f"\n[bold cyan]Editing Course:[/] [bold]{selected.name}[/]")
    selected.tracked = Confirm.ask("  Track attendance for this course?", default=selected.tracked)
    if selected.tracked:
        selected.show_in_dashboard = Confirm.ask(
            "  Show this course in the Dashboard?",
            default=selected.show_in_dashboard,
        )
        selected.theory_total_hours = FloatPrompt.ask(
            "  Total expected Theory hours",
            default=selected.theory_total_hours,
        )
        selected.theory_threshold_pct = FloatPrompt.ask(
            "  Theory attendance threshold (%)",
            default=selected.theory_threshold_pct,
        )
        selected.has_lab = Confirm.ask(
            "  Does this course include Laboratory sessions?",
            default=selected.has_lab,
        )
        if selected.has_lab:
            selected.lab_total_hours = FloatPrompt.ask(
                "  Total expected Laboratory hours",
                default=selected.lab_total_hours or config.default_lab_hours,
            )
            selected.lab_threshold_pct = FloatPrompt.ask(
                "  Laboratory attendance threshold (%)",
                default=selected.lab_threshold_pct or config.default_lab_threshold,
            )

    db.upsert_course(selected)
    console.print(f"[bold green]✔ Updated course '{selected.name}'.[/]")


def interactive_configure_view(db: Database, config: AppConfig) -> None:
    """Interactive menu to choose which courses and charts are displayed in the Dashboard."""
    courses = db.list_courses(only_tracked=True)
    if not courses:
        console.print("[yellow]No tracked courses available to configure.[/]")
        return

    table = Table(
        title="[bold bright_white]Dashboard View Settings (Visible Courses & Charts)[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("#", justify="right", style="bold cyan", width=4)
    table.add_column("Course Name", style="bold bright_white")
    table.add_column("Dashboard Visibility", justify="center")

    for idx, c in enumerate(courses, start=1):
        vis = "[bold green]✔ Shown[/]" if c.show_in_dashboard else "[dim]✘ Hidden[/]"
        table.add_row(str(idx), c.name, vis)

    console.print(table)
    charts_state = "[bold green]ON[/]" if config.show_charts else "[dim]OFF[/]"
    console.print(
        f"  Cartesian Charts in Dashboard: {charts_state}\n\n"
        "[bold]Options:[/]\n"
        "  • Enter course number(s) (e.g. [bold cyan]1 2[/]) to toggle visibility\n"
        "  • Type [bold cyan]all[/] to show all courses, or [bold cyan]none[/] to hide all\n"
        "  • Type [bold cyan]charts[/] to toggle Cartesian charts ON/OFF\n"
        "  • Press [bold]Enter[/] to go back"
    )

    raw = Prompt.ask("[bold cyan]View option[/]", default="").strip().lower()
    if not raw:
        return

    if raw == "all":
        for c in courses:
            c.show_in_dashboard = True
            db.upsert_course(c)
        console.print("[bold green]✔ All tracked courses are now shown in the Dashboard.[/]")
        return

    if raw == "none":
        for c in courses:
            c.show_in_dashboard = False
            db.upsert_course(c)
        console.print("[yellow]All courses hidden from Dashboard view.[/]")
        return

    if raw == "charts":
        config.show_charts = not config.show_charts
        config.save()
        st = "enabled" if config.show_charts else "disabled"
        console.print(f"[bold green]✔ Cartesian charts {st} in Dashboard.[/]")
        return

    tokens = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    for tok in tokens:
        target: Optional[CourseConfig] = None
        if tok.isdigit():
            n = int(tok)
            if 1 <= n <= len(courses):
                target = courses[n - 1]
        else:
            for c in courses:
                if tok.upper() in c.name.upper():
                    target = c
                    break
        if target:
            target.show_in_dashboard = not target.show_in_dashboard
            db.upsert_course(target)
            state_lbl = "SHOWN" if target.show_in_dashboard else "HIDDEN"
            console.print(f"[bold green]✔ '{target.name}' is now {state_lbl} in Dashboard.[/]")
        else:
            console.print(f"[red]Course '{tok}' not found.[/]")


def run_interactive_tui(config: AppConfig, db: Database) -> int:
    """Launch the interactive Rich TUI session."""
    if not config.initialized:
        config = run_initial_setup_wizard(config)

    all_stats, lessons, newly_completed = sync_and_calculate(
        config=config,
        db=db,
        interactive_discovery=True,
        acknowledge_new=True,
    )

    render_dashboard(
        config=config,
        all_stats=all_stats,
        newly_completed=newly_completed,
    )

    while True:
        console.print()
        menu_panel = Panel(
            r"[bold cyan]\[d][/] Dashboard   "
            r"[bold cyan]\[v][/] View Filter   "
            r"[bold cyan]\[l][/] Lessons   "
            r"[bold cyan]\[s][/] Skip/Restore   "
            r"[bold cyan]\[n][/] New Lessons   "
            r"[bold cyan]\[c][/] Edit Courses   "
            r"[bold cyan]\[w][/] Wizard   "
            r"[bold red]\[r][/] Reset   "
            r"[bold cyan]\[q][/] Quit",
            title="[bold bright_white]attendpy Interactive Menu[/]",
            box=box.ROUNDED,
            border_style="bright_blue",
        )
        console.print(menu_panel)

        try:
            action = (
                Prompt.ask(
                    "[bold cyan]Select action[/]",
                    choices=["d", "v", "l", "s", "n", "c", "w", "r", "q"],
                    default="q",
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            return 0

        if action == "q":
            console.print("[dim]Goodbye![/]")
            return 0

        if action == "d":
            all_stats, lessons, _ = sync_and_calculate(
                config=config,
                db=db,
                interactive_discovery=True,
                acknowledge_new=True,
            )
            render_dashboard(
                config=config,
                all_stats=all_stats,
                newly_completed=newly_completed,
            )
        elif action == "v":
            interactive_configure_view(db=db, config=config)
            render_dashboard(
                config=config,
                all_stats=all_stats,
                newly_completed=newly_completed,
            )
        elif action == "l":
            course_q = Prompt.ask(
                "Filter by course name (press Enter for all courses)",
                default="",
            ).strip()
            render_lessons_table(
                lessons,
                course_filter=course_q or None,
            )
        elif action == "s":
            interactive_skip_or_restore_prompt(
                db=db,
                config=config,
                lessons=lessons,
                only_new=False,
            )
            all_stats, lessons, _ = sync_and_calculate(
                config=config,
                db=db,
                interactive_discovery=False,
                acknowledge_new=True,
            )
        elif action == "n":
            if not newly_completed:
                console.print(
                    "[green]No newly completed lessons from this session require review.[/]"
                )
            else:
                interactive_skip_or_restore_prompt(
                    db=db,
                    config=config,
                    lessons=newly_completed,
                    only_new=True,
                )
                newly_completed = [e for e in newly_completed if e.is_newly_completed]
                all_stats, lessons, _ = sync_and_calculate(
                    config=config,
                    db=db,
                    interactive_discovery=False,
                    acknowledge_new=True,
                )
        elif action == "c":
            interactive_manage_courses(db=db, config=config)
            all_stats, lessons, _ = sync_and_calculate(
                config=config,
                db=db,
                interactive_discovery=False,
                acknowledge_new=True,
            )
        elif action == "w":
            config = run_initial_setup_wizard(config)
            all_stats, lessons, newly_completed = sync_and_calculate(
                config=config,
                db=db,
                interactive_discovery=True,
                acknowledge_new=True,
            )
            render_dashboard(
                config=config,
                all_stats=all_stats,
                newly_completed=newly_completed,
                show_charts=True,
            )
        elif action == "r":
            if Confirm.ask(
                "[bold red]Reset all settings, courses, and lesson records and restart from zero?[/]",
                default=False,
            ):
                reset_all_data()
                db = Database()
                config = AppConfig()
                console.print("[bold green]✔ All data wiped. Starting fresh setup wizard...[/]")
                config = run_initial_setup_wizard(config)
                all_stats, lessons, newly_completed = sync_and_calculate(
                    config=config,
                    db=db,
                    interactive_discovery=True,
                    acknowledge_new=True,
                )
                render_dashboard(
                    config=config,
                    all_stats=all_stats,
                    newly_completed=newly_completed,
                    show_charts=True,
                )
