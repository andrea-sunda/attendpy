"""Clean, discrete-lesson Unicode Cartesian chart renderer for Rich."""

from __future__ import annotations

from typing import Optional

from attendpy.models import (
    ComponentStats,
    CourseStats,
    LessonEvent,
    LessonStatus,
    LessonType,
)
from attendpy.ui.rich_compat import Columns, Group, Panel, Text, box


def _build_component_points(
    lessons: list[LessonEvent],
    lesson_type: Optional[LessonType],
) -> list[tuple[str, str, float, LessonStatus]]:
    """Build discrete per-lesson points for a Cartesian plot.

    Returns a list of (lesson_label, date_str, live_attendance_pct, lesson_status)
    for all past (ATTENDED or SKIPPED) lessons in chronological order.
    """
    filtered = [
        e
        for e in sorted(lessons, key=lambda x: x.start_time)
        if e.status in (LessonStatus.ATTENDED, LessonStatus.SKIPPED)
        and (lesson_type is None or e.lesson_type == lesson_type)
    ]

    points: list[tuple[str, str, float, LessonStatus]] = []
    cum_attended = 0.0
    cum_delivered = 0.0

    for idx, ev in enumerate(filtered, start=1):
        dur = max(ev.duration_hours, 0.5)
        cum_delivered += dur
        if ev.status == LessonStatus.ATTENDED:
            cum_attended += dur
        live_pct = (cum_attended / cum_delivered * 100.0) if cum_delivered > 0 else 100.0
        lesson_lbl = f"L{idx}"
        date_lbl = ev.start_time.strftime("%d/%m")
        points.append((lesson_lbl, date_lbl, round(live_pct, 1), ev.status))

    return points


def _render_component_plot(
    course_name: str,
    comp: ComponentStats,
    points: list[tuple[str, str, float, LessonStatus]],
    line_color: str = "cyan",
    max_lessons_shown: int = 10,
) -> Panel:
    """Render a clean 0%..100% (10% step) Cartesian plot for a single component."""
    # If there are more than `max_lessons_shown` past lessons, show the most recent ones
    display_pts = points[-max_lessons_shown:] if len(points) > max_lessons_shown else points

    step_x = 6
    min_cols = 44
    needed_cols = max(min_cols, len(display_pts) * step_x + 14)
    width = min(68, needed_cols)

    # 11 rows: row 0 = 100%, row 1 = 90%, ..., row 10 = 0%
    height = 11
    grid_char: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]
    grid_style: list[list[str]] = [["" for _ in range(width)] for _ in range(height)]

    def pct_to_row(pct: float) -> int:
        clamped = max(0.0, min(100.0, pct))
        return int(round((100.0 - clamped) / 10.0))

    # 1. Subtle horizontal reference lines at 20%, 40%, 60%, 80%, 100%
    for r in (0, 2, 4, 6, 8):
        for c in range(width):
            grid_char[r][c] = "·"
            grid_style[r][c] = "dim bright_black"

    # 2. Target threshold line (e.g. 60% or 80%)
    thresh_row = pct_to_row(comp.threshold_pct)
    thresh_color = "yellow" if comp.lesson_type == LessonType.THEORY else "bright_red"
    for c in range(width):
        grid_char[thresh_row][c] = "┄"
        grid_style[thresh_row][c] = thresh_color

    thresh_tag = f" Target {comp.threshold_pct:.0f}%"
    if len(thresh_tag) < width - 4:
        start_tag = width - len(thresh_tag)
        for i, ch in enumerate(thresh_tag):
            grid_char[thresh_row][start_tag + i] = ch
            grid_style[thresh_row][start_tag + i] = f"bold {thresh_color}"

    # 3. Compute X column for each discrete lesson point
    xs = [2 + i * step_x for i in range(len(display_pts))]
    ys = [pct_to_row(pt[2]) for pt in display_pts]

    # 4. Draw box-drawing step connectors between consecutive lessons
    for i in range(len(display_pts) - 1):
        x1, y1 = xs[i], ys[i]
        x2, y2 = xs[i + 1], ys[i + 1]
        if x2 >= width:
            break
        mid_x = (x1 + x2) // 2

        if y1 == y2:
            for c in range(x1 + 1, x2):
                grid_char[y1][c] = "─"
                grid_style[y1][c] = line_color
        elif y2 < y1:
            # Curve goes UP (row index decreases)
            for c in range(x1 + 1, mid_x):
                grid_char[y1][c] = "─"
                grid_style[y1][c] = line_color
            grid_char[y1][mid_x] = "╯"
            grid_style[y1][mid_x] = line_color
            for r in range(y2 + 1, y1):
                grid_char[r][mid_x] = "│"
                grid_style[r][mid_x] = line_color
            grid_char[y2][mid_x] = "╭"
            grid_style[y2][mid_x] = line_color
            for c in range(mid_x + 1, x2):
                grid_char[y2][c] = "─"
                grid_style[y2][c] = line_color
        else:
            # Curve goes DOWN (row index increases)
            for c in range(x1 + 1, mid_x):
                grid_char[y1][c] = "─"
                grid_style[y1][c] = line_color
            grid_char[y1][mid_x] = "╮"
            grid_style[y1][mid_x] = line_color
            for r in range(y1 + 1, y2):
                grid_char[r][mid_x] = "│"
                grid_style[r][mid_x] = line_color
            grid_char[y2][mid_x] = "╰"
            grid_style[y2][mid_x] = line_color
            for c in range(mid_x + 1, x2):
                grid_char[y2][c] = "─"
                grid_style[y2][c] = line_color

    # 5. Plot the discrete lesson markers (● for Attended, ✖ for Skipped)
    for i, pt in enumerate(display_pts):
        x, y = xs[i], ys[i]
        if x >= width:
            continue
        status = pt[3]
        if status == LessonStatus.ATTENDED:
            grid_char[y][x] = "●"
            grid_style[y][x] = f"bold {line_color}"
        else:
            grid_char[y][x] = "✖"
            grid_style[y][x] = "bold yellow"

    # 6. Annotate the latest point with its exact percentage value
    if display_pts:
        last_x, last_y = xs[-1], ys[-1]
        last_pct_str = f" {display_pts[-1][2]:.0f}%"
        if last_x + len(last_pct_str) < width:
            for offset, ch in enumerate(last_pct_str, start=1):
                grid_char[last_y][last_x + offset] = ch
                grid_style[last_y][last_x + offset] = f"bold {line_color}"

    # 7. Assemble the Rich Text canvas
    canvas = Text()
    for r in range(height):
        pct_val = 100 - r * 10
        axis_tick = "┼" if r == thresh_row else "┤"
        y_lbl_style = f"bold {thresh_color}" if r == thresh_row else "bright_black"
        canvas.append(f" {pct_val:3d}% {axis_tick}", style=y_lbl_style)
        for c in range(width):
            canvas.append(grid_char[r][c], style=grid_style[r][c])
        canvas.append("\n")

    # 8. X-axis with ticks aligned directly under each lesson column
    x_Set = set(xs)
    canvas.append("      └", style="bright_black")
    for c in range(width):
        canvas.append("┬" if c in x_Set else "─", style="bright_black")
    canvas.append("►\n", style="bright_black")

    if display_pts:
        # Row 1 of X-axis: Lesson numbers (L1, L2, ...)
        lbl_row = [" " for _ in range(width)]
        date_row = [" " for _ in range(width)]
        for i, (l_lbl, d_lbl, _, _) in enumerate(display_pts):
            col = xs[i]
            l_start = max(0, col - len(l_lbl) // 2)
            for k, ch in enumerate(l_lbl):
                if l_start + k < width:
                    lbl_row[l_start + k] = ch
            d_start = max(0, col - len(d_lbl) // 2)
            for k, ch in enumerate(d_lbl):
                if d_start + k < width:
                    date_row[d_start + k] = ch

        canvas.append("       " + "".join(lbl_row).rstrip() + "\n", style="bold bright_white")
        canvas.append("       " + "".join(date_row).rstrip() + "\n", style="dim")
    else:
        canvas.append(
            "       No completed lessons yet — points will appear as lessons occur.\n",
            style="italic dim",
        )

    # 9. Compact legend & summary footer
    canvas.append("\n  ")
    canvas.append("● Attended", style=f"bold {line_color}")
    canvas.append("   ")
    canvas.append("✖ Skipped", style="bold yellow")
    canvas.append("   ")
    canvas.append(f"┄ Target {comp.threshold_pct:.0f}%", style=thresh_color)
    canvas.append(
        f"   │   Now: {comp.current_attendance_pct:.1f}%  •  "
        f"Hours: {comp.attended_hours:.1f}h/{comp.total_expected_hours:.0f}h  •  "
        f"Max: {comp.max_possible_pct:.1f}%",
        style="bright_white",
    )

    return Panel(
        canvas,
        title=f"[bold {line_color}]{comp.lesson_type.label} — {course_name}[/]",
        box=box.ROUNDED,
        border_style=line_color,
    )


def render_course_cartesian_chart(
    course_stats: CourseStats,
    width: int = 56,
    height: int = 11,
) -> Panel:
    """Render clean Cartesian plot(s) for a course (Theory + optional Lab)."""
    has_lab = course_stats.course.has_lab and course_stats.lab is not None
    theory_pts = _build_component_points(
        course_stats.lessons,
        LessonType.THEORY if has_lab else None,
    )
    theory_panel = _render_component_plot(
        course_name=course_stats.course.name,
        comp=course_stats.theory,
        points=theory_pts,
        line_color="cyan",
    )

    if not has_lab or course_stats.lab is None:
        return theory_panel

    lab_pts = _build_component_points(
        course_stats.lessons,
        LessonType.LAB,
    )
    lab_panel = _render_component_plot(
        course_name=course_stats.course.name,
        comp=course_stats.lab,
        points=lab_pts,
        line_color="magenta",
    )

    return Panel(
        Columns([theory_panel, lab_panel], expand=True),
        title=f"[bold bright_white]Attendance Charts — {course_stats.course.name}[/]",
        box=box.ROUNDED,
        border_style="bright_blue",
    )
