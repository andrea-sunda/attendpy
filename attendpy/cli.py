"""GNU-compliant CLI interface and entrypoint for attendpy."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from attendpy import __prog__, __version__
from attendpy.config import AppConfig, get_app_dir, reset_all_data
from attendpy.db import Database
from attendpy.models import CourseConfig
from attendpy.providers import get_provider
from attendpy.ui.app import run_interactive_tui, sync_and_calculate
from attendpy.ui.chart import render_course_cartesian_chart
from attendpy.ui.dashboard import render_dashboard, render_newly_completed_banner
from attendpy.ui.lessons import (
    render_lessons_table,
    restore_lesson_by_id,
    skip_lesson_by_id,
)
from attendpy.ui.rich_compat import Confirm, Table, box, console
from attendpy.ui.wizard import run_initial_setup_wizard


def usage() -> str:
    """Return a comprehensive GNU-standard usage and help manual."""
    return f"""Usage: {__prog__} [OPTION]...
Track university course attendance hours across Theory and Laboratory sessions
using a Two-Calendar workflow (Attended Calendar + Skipped Calendar) for
Apple Calendar and Google Calendar.

With no OPTION, launches the interactive Terminal User Interface (TUI).
On first launch, runs the step-by-step setup wizard to verify or automatically
create the Attended Lessons calendar and Skipped Lessons calendar.

Calendar & Provider Configuration Options:
  -c, --attended-calendar=NAME   set the calendar category name for attended and
                                 scheduled lessons (e.g. "Lessons")
  -k, --skipped-calendar=NAME    set the calendar category name for skipped lessons
                                 (e.g. "Skipped Lessons")
  -p, --provider=TYPE            select calendar backend: 'apple', 'google', or 'hybrid'
      --cloud-account=NAME       target Cloud account in Apple Calendar (default: "iCloud")
      --google-attended-ics=URI  set Google Calendar .ics URL or file path for attended lessons
      --google-skipped-ics=URI   set Google Calendar .ics URL or file path for skipped lessons
      --lab-pattern=REGEX        set regex used to classify Laboratory lessons
                                 (default: '\\b(LABORATORIO|LABORATORY|LAB)\\b')
      --reconfigure              run the interactive step-by-step setup wizard
      --sync                     verify/create calendars and synchronize lessons now
      --reset                    factory reset: delete all saved configuration, courses,
                                 and lesson history to start from scratch
  -y, --yes                      automatic yes to confirmation prompts (e.g. with --reset)

Reporting & Inspection Options:
  -s, --stats[=COURSE]           display attendance statistics (and charts) and exit
      --no-chart                 omit Cartesian charts when running with --stats
      --chart[=COURSE]           display only the Cartesian attendance chart(s) and exit
  -l, --lessons[=COURSE]         list individual lessons with ID, date, time, hours,
                                 Theory/Lab classification, location, and status
      --new-lessons              list newly completed lessons since the last check
      --courses                  list all registered courses (tracked and ignored)
      --list-calendars           list all calendar categories available in the provider

Attendance & Course Modification Options:
  -x, --skip=LESSON_ID           mark lesson LESSON_ID as skipped and move it to the
      --cancel=LESSON_ID         Skipped Lessons calendar
  -r, --restore=LESSON_ID        restore skipped lesson LESSON_ID to attended and move
                                 it back to the Attended Lessons calendar
      --edit-course=COURSE       create or modify configuration for COURSE (combine with
                                 the course flags below)
      --theory-hours=HOURS       set total expected Theory hours (used with --edit-course)
      --theory-threshold=PCT     set Theory attendance threshold % (default: 60)
      --has-lab                  enable Laboratory tracking for the course
      --no-lab                   disable Laboratory tracking for the course
      --lab-hours=HOURS          set total expected Laboratory hours (used with --edit-course)
      --lab-threshold=PCT        set Laboratory attendance threshold % (default: 80)
      --track                    enable tracking for the course (un-ignore)
      --ignore                   ignore the course in attendance calculations

Miscellaneous Options:
  -h, --help                     display this help manual and exit
  -v, --version                  output version information and exit

Exit status:
  0  if OK,
  1  if a runtime, synchronization, or lookup error occurs,
  2  if invalid command-line arguments are supplied.

Configuration files:
  User configuration and local SQLite database are stored in:
  {get_app_dir()} (override with ATTENDPY_HOME environment variable).

Examples:
  {__prog__}
      Launch the interactive TUI (runs the setup wizard on first launch).

  {__prog__} -p apple -c "Lessons" -k "Skipped Lessons" --sync
      Configure Apple Calendar with "Lessons" and "Skipped Lessons" categories,
      creating either calendar automatically if missing.

  {__prog__} --stats
      Print the attendance dashboard and Cartesian charts for all courses.

  {__prog__} --lessons "LINGUAGGI DI PROGRAMMAZIONE"
      List all Theory and Laboratory lessons for a specific course.

  {__prog__} --skip a1b2c3d4
      Mark lesson 'a1b2c3d4' as skipped and move it to the Skipped calendar.

  {__prog__} --edit-course "LINGUAGGI DI PROGRAMMAZIONE" --theory-hours 48 --theory-threshold 60 --has-lab --lab-hours 24 --lab-threshold 80
      Configure total semester hours and thresholds for Theory (60%) and Lab (80%).

  {__prog__} --reset
      Wipe all stored courses, lesson history, and settings to start from scratch.
"""


def version_text() -> str:
    """Return GNU-style version output."""
    return (
        f"{__prog__} {__version__}\n"
        "Copyright (C) 2026 Andrea.\n"
        "License MIT: <https://opensource.org/licenses/MIT>.\n"
        "This is free software: you are free to change and redistribute it.\n"
        "There is NO WARRANTY, to the extent permitted by law."
    )


class GNUArgumentParser(argparse.ArgumentParser):
    """ArgumentParser customized to emit GNU-compliant usage and error messages."""

    def format_help(self) -> str:
        return usage()

    def error(self, message: str) -> None:
        sys.stderr.write(f"{__prog__}: error: {message}\n")
        sys.stderr.write(f"Try '{__prog__} --help' for more information.\n")
        raise SystemExit(2)


def build_parser() -> GNUArgumentParser:
    parser = GNUArgumentParser(prog=__prog__, add_help=False)

    # Calendar & Provider Configuration
    parser.add_argument("-c", "--attended-calendar", dest="attended_calendar", metavar="NAME")
    parser.add_argument("-k", "--skipped-calendar", dest="skipped_calendar", metavar="NAME")
    parser.add_argument(
        "-p",
        "--provider",
        dest="provider",
        choices=["apple", "google", "hybrid"],
        metavar="TYPE",
    )
    parser.add_argument("--cloud-account", dest="cloud_account", metavar="NAME")
    parser.add_argument("--google-attended-ics", dest="google_attended_ics", metavar="URI")
    parser.add_argument("--google-skipped-ics", dest="google_skipped_ics", metavar="URI")
    parser.add_argument("--lab-pattern", dest="lab_pattern", metavar="REGEX")
    parser.add_argument("--reconfigure", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true")

    # Reporting & Inspection
    parser.add_argument("-s", "--stats", nargs="?", const="__ALL__", default=None, metavar="COURSE")
    parser.add_argument("--no-chart", action="store_true")
    parser.add_argument("--chart", nargs="?", const="__ALL__", default=None, metavar="COURSE")
    parser.add_argument("-l", "--lessons", nargs="?", const="__ALL__", default=None, metavar="COURSE")
    parser.add_argument("--new-lessons", action="store_true")
    parser.add_argument("--courses", action="store_true")
    parser.add_argument("--list-calendars", action="store_true")

    # Attendance & Course Modification
    parser.add_argument("-x", "--skip", "--cancel", dest="skip_id", metavar="LESSON_ID")
    parser.add_argument("-r", "--restore", dest="restore_id", metavar="LESSON_ID")
    parser.add_argument("--edit-course", dest="edit_course", metavar="COURSE")
    parser.add_argument("--theory-hours", dest="theory_hours", type=float, metavar="HOURS")
    parser.add_argument("--theory-threshold", dest="theory_threshold", type=float, metavar="PCT")
    parser.add_argument("--has-lab", dest="has_lab", action="store_true", default=None)
    parser.add_argument("--no-lab", dest="no_lab", action="store_true", default=False)
    parser.add_argument("--lab-hours", dest="lab_hours", type=float, metavar="HOURS")
    parser.add_argument("--lab-threshold", dest="lab_threshold", type=float, metavar="PCT")
    parser.add_argument("--track", dest="track_course", action="store_true", default=False)
    parser.add_argument("--ignore", dest="ignore_course", action="store_true", default=False)

    # Misc
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-v", "--version", action="store_true")

    return parser


def _print_courses_table(db: Database) -> None:
    courses = db.list_courses(only_tracked=False)
    if not courses:
        console.print("[yellow]No courses configured yet.[/]")
        return

    table = Table(
        title="[bold bright_white]Configured University Courses[/]",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Course Name", style="bold bright_white")
    table.add_column("Tracked", justify="center")
    table.add_column("Theory Total Hours", justify="right")
    table.add_column("Theory Threshold", justify="right")
    table.add_column("Has Lab", justify="center")
    table.add_column("Lab Total Hours", justify="right")
    table.add_column("Lab Threshold", justify="right")

    for c in courses:
        table.add_row(
            c.name,
            "[green]Yes[/]" if c.tracked else "[red]Ignored[/]",
            f"{c.theory_total_hours:.1f}h",
            f"{c.theory_threshold_pct:.0f}%",
            "[magenta]Yes[/]" if c.has_lab else "[dim]No[/]",
            f"{c.lab_total_hours:.1f}h" if c.has_lab else "-",
            f"{c.lab_threshold_pct:.0f}%" if c.has_lab else "-",
        )
    console.print(table)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main CLI dispatcher."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.help:
        sys.stdout.write(usage())
        return 0

    if args.version:
        sys.stdout.write(version_text() + "\n")
        return 0

    if args.reset:
        if not args.yes and sys.stdin.isatty():
            confirmed = Confirm.ask(
                "[bold red]Are you sure you want to reset all attendpy configuration, courses, and lesson history?[/]",
                default=False,
            )
            if not confirmed:
                console.print("[yellow]Reset cancelled.[/]")
                return 0
        removed = reset_all_data()
        if removed:
            for p in removed:
                console.print(f"[dim]Removed: {p}[/]")
        console.print(
            "[bold green]✔ Factory reset complete. All settings, courses, and lesson records have been wiped.[/]"
        )
        return 0

    config = AppConfig.load()
    db = Database()

    # 1. Apply any CLI configuration updates
    config_modified = False
    if args.provider:
        config.provider = args.provider
        config_modified = True
    if args.cloud_account:
        config.cloud_account = args.cloud_account.strip()
        config_modified = True
    if args.attended_calendar:
        config.attended_calendar = args.attended_calendar.strip()
        config_modified = True
    if args.skipped_calendar:
        config.skipped_calendar = args.skipped_calendar.strip()
        config_modified = True
    if args.google_attended_ics is not None:
        config.google_attended_ics = args.google_attended_ics.strip()
        config_modified = True
    if args.google_skipped_ics is not None:
        config.google_skipped_ics = args.google_skipped_ics.strip()
        config_modified = True
    if args.lab_pattern:
        config.lab_pattern = args.lab_pattern
        config_modified = True

    if config_modified:
        if config.attended_calendar.lower() == config.skipped_calendar.lower():
            sys.stderr.write(
                f"{__prog__}: error: attended calendar and skipped calendar must have distinct names.\n"
            )
            return 2
        config.initialized = True
        provider = get_provider(config)
        ok_a, msg_a = provider.ensure_calendar_exists(config.attended_calendar)
        ok_s, msg_s = provider.ensure_calendar_exists(config.skipped_calendar)
        config.save()
        console.print(f"[green]✔ Configuration updated.[/] ({msg_a} | {msg_s})")

    if args.reconfigure:
        config = run_initial_setup_wizard(config)
        if not any(
            [
                args.stats,
                args.chart,
                args.lessons,
                args.new_lessons,
                args.courses,
                args.list_calendars,
                args.skip_id,
                args.restore_id,
                args.edit_course,
                args.sync,
            ]
        ):
            return 0

    if args.list_calendars:
        provider = get_provider(config)
        cals = provider.list_calendars()
        if not cals:
            console.print("[yellow]No calendars detected in provider.[/]")
        else:
            console.print(f"[bold cyan]Available Calendars ({config.provider}):[/]")
            for c in cals:
                marker = ""
                if c.lower() == config.attended_calendar.lower():
                    marker = " [bold green](Attended Calendar)[/]"
                elif c.lower() == config.skipped_calendar.lower():
                    marker = " [bold yellow](Skipped Calendar)[/]"
                console.print(f"  • {c}{marker}")
        return 0

    if args.edit_course:
        cname = args.edit_course.strip().upper()
        existing = db.get_course(cname) or CourseConfig(
            name=cname,
            tracked=True,
            theory_total_hours=config.default_theory_hours,
            theory_threshold_pct=config.default_theory_threshold,
            has_lab=False,
            lab_total_hours=config.default_lab_hours,
            lab_threshold_pct=config.default_lab_threshold,
        )
        if args.theory_hours is not None:
            if args.theory_hours <= 0:
                sys.stderr.write(f"{__prog__}: error: --theory-hours must be > 0\n")
                return 2
            existing.theory_total_hours = args.theory_hours
        if args.theory_threshold is not None:
            if not (0 <= args.theory_threshold <= 100):
                sys.stderr.write(
                    f"{__prog__}: error: --theory-threshold must be between 0 and 100\n"
                )
                return 2
            existing.theory_threshold_pct = args.theory_threshold
        if args.has_lab:
            existing.has_lab = True
        if args.no_lab:
            existing.has_lab = False
        if args.lab_hours is not None:
            if args.lab_hours <= 0:
                sys.stderr.write(f"{__prog__}: error: --lab-hours must be > 0\n")
                return 2
            existing.lab_total_hours = args.lab_hours
            existing.has_lab = True
        if args.lab_threshold is not None:
            if not (0 <= args.lab_threshold <= 100):
                sys.stderr.write(
                    f"{__prog__}: error: --lab-threshold must be between 0 and 100\n"
                )
                return 2
            existing.lab_threshold_pct = args.lab_threshold
        if args.track_course:
            existing.tracked = True
        if args.ignore_course:
            existing.tracked = False

        db.upsert_course(existing)
        console.print(
            f"[bold green]✔ Course '{existing.name}' saved:[/] "
            f"tracked={existing.tracked}, theory={existing.theory_total_hours:.1f}h "
            f"({existing.theory_threshold_pct:.0f}%), has_lab={existing.has_lab}"
            + (
                f", lab={existing.lab_total_hours:.1f}h ({existing.lab_threshold_pct:.0f}%)"
                if existing.has_lab
                else ""
            )
        )
        if not any([args.stats, args.chart, args.lessons, args.courses]):
            return 0

    if args.courses:
        _print_courses_table(db)
        return 0

    if args.skip_id:
        # Ensure lessons are synced first so short IDs exist in DB
        sync_and_calculate(config, db, interactive_discovery=False, acknowledge_new=False)
        ok, msg = skip_lesson_by_id(db, config, args.skip_id)
        if not ok:
            console.print(f"[bold red]✘ {msg}[/]")
            return 1
        console.print(f"[bold green]✔ {msg}[/]")
        return 0

    if args.restore_id:
        sync_and_calculate(config, db, interactive_discovery=False, acknowledge_new=False)
        ok, msg = restore_lesson_by_id(db, config, args.restore_id)
        if not ok:
            console.print(f"[bold red]✘ {msg}[/]")
            return 1
        console.print(f"[bold green]✔ {msg}[/]")
        return 0

    if args.sync:
        all_stats, lessons, newly = sync_and_calculate(
            config, db, interactive_discovery=False, acknowledge_new=False
        )
        console.print(
            f"[bold green]✔ Synchronized {len(lessons)} lesson(s) across {len(all_stats)} tracked course(s).[/]"
        )
        return 0

    if args.new_lessons:
        _, _, newly = sync_and_calculate(
            config, db, interactive_discovery=False, acknowledge_new=True
        )
        if not newly:
            console.print("[green]No newly completed lessons since last check.[/]")
        else:
            render_newly_completed_banner(newly)
        return 0

    if args.lessons is not None:
        c_filter = None if args.lessons == "__ALL__" else args.lessons
        _, lessons, _ = sync_and_calculate(
            config,
            db,
            interactive_discovery=False,
            acknowledge_new=False,
            course_filter=c_filter,
        )
        render_lessons_table(lessons, course_filter=c_filter)
        return 0

    if args.chart is not None:
        c_filter = None if args.chart == "__ALL__" else args.chart
        all_stats, _, _ = sync_and_calculate(
            config,
            db,
            interactive_discovery=False,
            acknowledge_new=False,
            course_filter=c_filter,
        )
        if not all_stats:
            console.print("[yellow]No matching tracked courses found.[/]")
            return 1
        for cs in all_stats:
            console.print(render_course_cartesian_chart(cs))
        return 0

    if args.stats is not None:
        c_filter = None if args.stats == "__ALL__" else args.stats
        all_stats, _, newly = sync_and_calculate(
            config,
            db,
            interactive_discovery=False,
            acknowledge_new=True,
            course_filter=c_filter,
        )
        render_dashboard(
            config=config,
            all_stats=all_stats,
            newly_completed=newly,
            show_charts=not args.no_chart,
        )
        return 0

    if config_modified:
        return 0

    # No action flags supplied -> Launch interactive TUI
    return run_interactive_tui(config=config, db=db)


if __name__ == "__main__":
    raise SystemExit(main())
