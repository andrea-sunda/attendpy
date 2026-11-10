"""Interactive step-by-step setup wizard and new-course discovery prompts."""

from __future__ import annotations

import sys
from datetime import datetime

from attendpy.config import AppConfig
from attendpy.db import Database
from attendpy.models import CourseConfig, LessonEvent, LessonType
from attendpy.providers import get_provider
from attendpy.ui.interactive import select_from_list
from attendpy.ui.rich_compat import (
    Confirm,
    FloatPrompt,
    Panel,
    Prompt,
    Rule,
    Table,
    box,
    console,
)


def run_initial_setup_wizard(config: AppConfig) -> AppConfig:
    """Run the step-by-step first-time configuration wizard."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]Welcome to attendpy — University Attendance Tracker[/]\n\n"
            "This wizard will guide you step-by-step through connecting your calendar and\n"
            "setting up the [bold]Two-Calendar Attendance Workflow[/]:\n\n"
            "  [bold green]1. Attended Lessons Calendar[/] (e.g. [italic]'Lessons'[/] or [italic]'Lezioni'[/])\n"
            "     Contains your active university timetable. Every completed lesson in this\n"
            "     calendar is automatically counted as [bold green]ATTENDED[/] without manual check-ins.\n\n"
            "  [bold yellow]2. Skipped Lessons Calendar[/] (e.g. [italic]'Skipped Lessons'[/] or [italic]'Lezioni saltate'[/])\n"
            "     Contains lessons you missed. Whenever you skip a lesson, either move it to\n"
            "     this calendar in your calendar app or skip it directly inside [bold]attendpy[/].\n"
            "     Both calendars will be verified now and created automatically if missing.",
            title="[bold bright_white]First-Time Setup Wizard[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    # ------------------------------------------------------------------
    # Step 1: Select Calendar Provider
    # ------------------------------------------------------------------
    console.print(Rule("[bold cyan]Step 1 of 3 — Select Calendar Provider[/]"))
    provider_choice = select_from_list(
        title="Select Calendar Provider",
        options=[
            ("apple", "Native macOS Apple Calendar / iCloud (also reads Google accounts synced to Mac)"),
            ("google", "Direct Google Calendar connection via iCal (.ics) feeds or OAuth token"),
            ("hybrid", "Combine both Apple Calendar and direct Google Calendar feeds"),
        ],
        default_value=config.provider or "apple",
    )
    config.provider = provider_choice.strip().lower()

    if config.provider in ("google", "hybrid"):
        console.print(
            "\n[dim]Optional: Enter Google Calendar iCal (.ics) URLs or local .ics paths "
            "(press Enter to skip or use local/Apple bridge):[/]"
        )
        config.google_attended_ics = Prompt.ask(
            "  Google Attended Calendar .ics URL/path",
            default=config.google_attended_ics or "",
        ).strip()
        config.google_skipped_ics = Prompt.ask(
            "  Google Skipped Calendar .ics URL/path",
            default=config.google_skipped_ics or "",
        ).strip()

    # ------------------------------------------------------------------
    # Step 2: Configure Attended & Skipped Calendar Names
    # ------------------------------------------------------------------
    console.print()
    console.print(Rule("[bold cyan]Step 2 of 3 — Configure Calendar Categories[/]"))

    provider = get_provider(config)
    if hasattr(provider, "list_cloud_sources"):
        cloud_sources = provider.list_cloud_sources()  # type: ignore[attr-defined]
        if cloud_sources:
            src_list_str = ", ".join(f"[bold cyan]{s}[/]" for s in cloud_sources)
            console.print(f"Detected Cloud account(s): {src_list_str}")
            if len(cloud_sources) > 1:
                config.cloud_account = select_from_list(
                    title="Select Cloud Account for Calendars",
                    options=[(s, f"{s} Cloud Account") for s in cloud_sources],
                    default_value=cloud_sources[0],
                )
            else:
                config.cloud_account = cloud_sources[0]

    existing_calendars = provider.list_calendars()
    if existing_calendars:
        cal_list_str = ", ".join(f"[bold]{c}[/]" for c in existing_calendars)
        console.print(f"Detected existing Cloud calendars: {cal_list_str}\n")
    else:
        console.print(
            "[dim]No existing Cloud calendars detected yet — new ones will be created on iCloud / Cloud automatically.[/]\n"
        )

    console.print(
        "[bold green]A. Attended Lessons Calendar[/]\n"
        "Enter the exact name of the calendar category where your university lessons are scheduled.\n"
        "If it does not exist yet, [bold]attendpy[/] will create it for you."
    )
    while True:
        attended_name = Prompt.ask(
            "[bold green]Attended lessons calendar name[/]",
            default=config.attended_calendar or "Lessons",
        ).strip()
        if attended_name:
            break
        console.print("[red]Calendar name cannot be empty. Please enter a valid name.[/]")

    console.print(
        "\n[bold yellow]B. Skipped Lessons Calendar[/]\n"
        "Enter the exact name of the calendar category dedicated to lessons you skip\n"
        "(for example: [italic]'Skipped Lessons'[/] or [italic]'Lezioni saltate'[/]).\n"
        "If it does not exist yet, [bold]attendpy[/] will create it for you automatically."
    )
    while True:
        skipped_name = Prompt.ask(
            "[bold yellow]Skipped lessons calendar name[/]",
            default=config.skipped_calendar or "Skipped Lessons",
        ).strip()
        if not skipped_name:
            console.print("[red]Calendar name cannot be empty.[/]")
            continue
        if skipped_name.lower() == attended_name.lower():
            console.print(
                "[red]The Skipped calendar must have a different name from the Attended calendar.[/]"
            )
            continue
        break

    config.attended_calendar = attended_name
    config.skipped_calendar = skipped_name

    # ------------------------------------------------------------------
    # Step 3: Verify and Auto-Create Both Calendars
    # ------------------------------------------------------------------
    console.print()
    console.print(Rule("[bold cyan]Step 3 of 3 — Verifying & Creating Calendars[/]"))

    provider = get_provider(config)
    ok_att, msg_att = provider.ensure_calendar_exists(config.attended_calendar)
    ok_skp, msg_skp = provider.ensure_calendar_exists(config.skipped_calendar)

    verify_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    verify_table.add_column("Role", style="bold")
    verify_table.add_column("Calendar Name", style="bright_white")
    verify_table.add_column("Status")

    verify_table.add_row(
        "Attended Lessons",
        config.attended_calendar,
        f"[green][OK] {msg_att}[/]" if ok_att else f"[red][ERR] {msg_att}[/]",
    )
    verify_table.add_row(
        "Skipped Lessons",
        config.skipped_calendar,
        f"[green][OK] {msg_skp}[/]" if ok_skp else f"[red][ERR] {msg_skp}[/]",
    )
    console.print(verify_table)

    config.initialized = True
    config.last_sync_at = datetime.now().isoformat(timespec="seconds")
    saved_path = config.save()
    console.print(
        f"[bold green]Configuration saved to[/] [underline]{saved_path}[/]\n"
    )
    return config


def discover_and_prompt_new_courses(
    db: Database,
    events: list[LessonEvent],
    config: AppConfig,
    interactive: bool = True,
) -> list[CourseConfig]:
    """Detect any new courses in calendar events and prompt the user to configure them."""
    known_courses = {c.name.upper(): c for c in db.list_courses(only_tracked=False)}
    events_by_course: dict[str, list[LessonEvent]] = {}
    for ev in events:
        events_by_course.setdefault(ev.course_name.upper(), []).append(ev)

    newly_configured: list[CourseConfig] = []
    can_prompt = interactive and sys.stdin.isatty()

    for course_name, course_events in sorted(events_by_course.items()):
        if course_name in known_courses:
            continue

        theory_evs = [e for e in course_events if e.lesson_type == LessonType.THEORY]
        lab_evs = [e for e in course_events if e.lesson_type == LessonType.LAB]
        detected_has_lab = len(lab_evs) > 0

        if not can_prompt:
            # Non-interactive fallback: register course with sensible defaults
            auto_course = CourseConfig(
                name=course_name,
                tracked=True,
                show_in_dashboard=True,
                theory_total_hours=config.default_theory_hours,
                theory_threshold_pct=config.default_theory_threshold,
                has_lab=detected_has_lab,
                lab_total_hours=config.default_lab_hours if detected_has_lab else 0.0,
                lab_threshold_pct=config.default_lab_threshold,
            )
            db.upsert_course(auto_course)
            newly_configured.append(auto_course)
            continue

        # Compact single-line header for new course discovery
        console.print(
            f"\n[bold yellow]New course:[/] [bold bright_white]{course_name}[/] "
            f"[dim]({len(theory_evs)} Theory, {len(lab_evs)} Lab in calendar)[/]"
        )

        track = Confirm.ask(
            "  Track attendance?",
            default=True,
        )
        if not track:
            ignored = CourseConfig(
                name=course_name,
                tracked=False,
                show_in_dashboard=False,
                theory_total_hours=config.default_theory_hours,
                theory_threshold_pct=config.default_theory_threshold,
                has_lab=detected_has_lab,
                lab_total_hours=config.default_lab_hours,
                lab_threshold_pct=config.default_lab_threshold,
            )
            db.upsert_course(ignored)
            console.print(f"  [dim]↳ Ignored '{course_name}'.[/]")
            continue

        theory_hours = FloatPrompt.ask(
            "  Total [bold cyan]Theory[/] hours",
            default=config.default_theory_hours,
        )
        theory_threshold = FloatPrompt.ask(
            "  [bold cyan]Theory[/] threshold %",
            default=config.default_theory_threshold,
        )

        has_lab = Confirm.ask(
            "  Has [bold magenta]Lab[/]?",
            default=detected_has_lab,
        )
        lab_hours = 0.0
        lab_threshold = config.default_lab_threshold
        if has_lab:
            lab_hours = FloatPrompt.ask(
                "  Total [bold magenta]Lab[/] hours",
                default=config.default_lab_hours,
            )
            lab_threshold = FloatPrompt.ask(
                "  [bold magenta]Lab[/] threshold %",
                default=config.default_lab_threshold,
            )

        new_course = CourseConfig(
            name=course_name,
            tracked=True,
            show_in_dashboard=True,
            theory_total_hours=max(1.0, theory_hours),
            theory_threshold_pct=max(0.0, min(100.0, theory_threshold)),
            has_lab=has_lab,
            lab_total_hours=max(1.0, lab_hours) if has_lab else 0.0,
            lab_threshold_pct=max(0.0, min(100.0, lab_threshold)),
        )
        db.upsert_course(new_course)
        newly_configured.append(new_course)
        console.print(f"  [bold green]Saved '{course_name}'.[/]")

    return newly_configured
