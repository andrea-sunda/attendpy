"""Unicode/ASCII Cartesian coordinate chart renderer for Rich."""

from __future__ import annotations

from typing import Optional

from attendpy.models import CourseStats, LessonType
from attendpy.stats import build_attendance_series
from attendpy.ui.rich_compat import Panel, Text, box


def _interpolate_points(
    values: list[float],
    target_width: int,
) -> list[Optional[float]]:
    """Map a sequence of Y values onto `target_width` X columns."""
    if not values or target_width <= 0:
        return [None] * max(target_width, 0)
    if len(values) == 1:
        return [values[0]] * target_width

    result: list[Optional[float]] = []
    n = len(values)
    for col in range(target_width):
        pos = (col / max(1, target_width - 1)) * (n - 1)
        idx_low = int(pos)
        idx_high = min(n - 1, idx_low + 1)
        frac = pos - idx_low
        interp = values[idx_low] * (1.0 - frac) + values[idx_high] * frac
        result.append(interp)
    return result


def render_course_cartesian_chart(
    course_stats: CourseStats,
    width: int = 56,
    height: int = 13,
) -> Panel:
    """Render a Cartesian chart (X = Timeline, Y = Attendance % 0..100) for a course.

    Displays:
      - Y-axis: Attendance Percentage (0% to 100%)
      - X-axis: Lesson timeline dates
      - Theory attendance trajectory (cyan '●' / '─')
      - Laboratory attendance trajectory (magenta '◆' / '─') if lab is enabled
      - Horizontal dashed threshold lines (e.g., 60% Theory, 80% Lab)
    """
    theory_series = build_attendance_series(
        course_stats.lessons,
        LessonType.THEORY if course_stats.course.has_lab else None,
        course_stats.theory.total_expected_hours,
    )
    lab_series = (
        build_attendance_series(
            course_stats.lessons,
            LessonType.LAB,
            course_stats.lab.total_expected_hours,
        )
        if course_stats.course.has_lab and course_stats.lab
        else []
    )

    theory_vals = [pt[1] for pt in theory_series]
    lab_vals = [pt[1] for pt in lab_series]

    all_dates = [pt[0] for pt in (theory_series or lab_series)]
    start_date_lbl = all_dates[0] if all_dates else "Start"
    mid_date_lbl = all_dates[len(all_dates) // 2] if all_dates else "Mid"
    end_date_lbl = all_dates[-1] if all_dates else "Now"

    # Prepare 2D character & style grid: row 0 is 100%, row (height-1) is 0%
    grid_char: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]
    grid_style: list[list[str]] = [["" for _ in range(width)] for _ in range(height)]

    def pct_to_row(pct: float) -> int:
        clamped = max(0.0, min(100.0, pct))
        return int(round((1.0 - clamped / 100.0) * (height - 1)))

    # 1. Subtle horizontal grid lines at 25%, 50%, 75%, 100%
    for g_pct in (25.0, 50.0, 75.0, 100.0):
        r = pct_to_row(g_pct)
        for c in range(width):
            grid_char[r][c] = "·"
            grid_style[r][c] = "dim bright_black"

    # 2. Theory threshold line (default 60%)
    t_thresh = course_stats.course.theory_threshold_pct
    t_row = pct_to_row(t_thresh)
    for c in range(width):
        grid_char[t_row][c] = "┄"
        grid_style[t_row][c] = "yellow"

    # 3. Lab threshold line (default 80%) if course has lab
    if course_stats.course.has_lab and course_stats.lab:
        l_thresh = course_stats.course.lab_threshold_pct
        l_row = pct_to_row(l_thresh)
        for c in range(width):
            grid_char[l_row][c] = "┈"
            grid_style[l_row][c] = "red"

    # 4. Plot Theory curve
    if theory_vals:
        mapped_t = _interpolate_points(theory_vals, width)
        knot_cols_t = {
            int(round(i / max(1, len(theory_vals) - 1) * (width - 1)))
            for i in range(len(theory_vals))
        }
        for c, val in enumerate(mapped_t):
            if val is None:
                continue
            r = pct_to_row(val)
            is_knot = c in knot_cols_t or len(theory_vals) == 1
            grid_char[r][c] = "●" if is_knot else "─"
            grid_style[r][c] = "bold cyan" if is_knot else "cyan"

    # 5. Plot Lab curve
    if lab_vals:
        mapped_l = _interpolate_points(lab_vals, width)
        knot_cols_l = {
            int(round(i / max(1, len(lab_vals) - 1) * (width - 1)))
            for i in range(len(lab_vals))
        }
        for c, val in enumerate(mapped_l):
            if val is None:
                continue
            r = pct_to_row(val)
            is_knot = c in knot_cols_l or len(lab_vals) == 1
            if grid_char[r][c] in ("●", "─"):
                grid_char[r][c] = "◈"
                grid_style[r][c] = "bold bright_white"
            else:
                grid_char[r][c] = "◆" if is_knot else "═"
                grid_style[r][c] = "bold magenta" if is_knot else "magenta"

    # Assemble Rich Text with Y-axis labels and X-axis
    canvas = Text()
    for r in range(height):
        pct_val = int(round((1.0 - r / max(1, height - 1)) * 100))
        if r in (0, height // 4, height // 2, (3 * height) // 4, height - 1) or r == t_row:
            y_label = f"{pct_val:3d}% ┤"
        else:
            y_label = "     │"
        canvas.append(y_label, style="bold bright_black")

        for c in range(width):
            ch = grid_char[r][c]
            st = grid_style[r][c]
            canvas.append(ch, style=st)
        canvas.append("\n")

    # X-axis baseline
    canvas.append("     └", style="bold bright_black")
    canvas.append("─" * width, style="bold bright_black")
    canvas.append("► Time\n", style="bold bright_black")

    # X-axis date ticks
    pad_mid = max(1, (width // 2) - len(start_date_lbl) - (len(mid_date_lbl) // 2))
    pad_end = max(
        1,
        width - len(start_date_lbl) - pad_mid - len(mid_date_lbl) - len(end_date_lbl),
    )
    x_labels = (
        "      "
        + start_date_lbl
        + (" " * pad_mid)
        + mid_date_lbl
        + (" " * pad_end)
        + end_date_lbl
        + "\n"
    )
    canvas.append(x_labels, style="dim")

    # Legend
    canvas.append("\n      Legend: ", style="bold")
    canvas.append("●─● Theory % ", style="bold cyan")
    canvas.append(f"(┄┄ Threshold {t_thresh:.0f}%)  ", style="yellow")
    if course_stats.course.has_lab and course_stats.lab:
        l_thresh = course_stats.course.lab_threshold_pct
        canvas.append("◆═◆ Lab % ", style="bold magenta")
        canvas.append(f"(┈┈ Threshold {l_thresh:.0f}%)", style="red")

    if not theory_vals and not lab_vals:
        canvas.append(
            "\n      [No completed lessons yet — chart will populate as lessons occur]",
            style="italic dim",
        )

    return Panel(
        canvas,
        title=f"[bold bright_white]Cartesian Attendance Chart — {course_stats.course.name}[/]",
        subtitle="[dim]Y: Attendance % (on delivered hours) | X: Lesson Timeline[/]",
        box=box.ROUNDED,
        border_style="bright_blue",
    )
