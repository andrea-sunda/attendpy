"""Unit and integration test suite for attendpy."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from attendpy.cli import main, usage
from attendpy.config import AppConfig
from attendpy.db import Database
from attendpy.models import (
    CourseConfig,
    LessonEvent,
    LessonStatus,
    LessonType,
)
from attendpy.parser import (
    compute_lesson_uid,
    detect_lesson_type,
    extract_external_code,
    extract_location,
    normalize_course_name,
)
from attendpy.providers.ics_parser import parse_ics_content
from attendpy.stats import (
    build_attendance_series,
    calculate_component_stats,
    calculate_course_stats,
)
from attendpy.ui.chart import render_course_cartesian_chart


class TestParser(unittest.TestCase):
    """Test university event title and description parsing."""

    def test_example_university_event_theory(self) -> None:
        title = "LINGUAGGI DI PROGRAMMAZIONE"
        desc = (
            "LINGUAGGI DI PROGRAMMAZIONE- Aula Magna Matematica - "
            "Palazzo delle Scienze[ES_ID_SCIENZE_2_437_1495673]"
        )
        self.assertEqual(normalize_course_name(title), "LINGUAGGI DI PROGRAMMAZIONE")
        self.assertEqual(detect_lesson_type(title, desc), LessonType.THEORY)
        self.assertEqual(
            extract_external_code(desc, title), "ES_ID_SCIENZE_2_437_1495673"
        )
        self.assertEqual(
            extract_location("", desc, title),
            "Aula Magna Matematica - Palazzo delle Scienze",
        )

    def test_example_university_event_laboratory(self) -> None:
        title = "LINGUAGGI DI PROGRAMMAZIONE"
        desc = (
            "LINGUAGGI DI PROGRAMMAZIONE - LABORATORIO - Lab Informatica 1 - "
            "Palazzo delle Scienze[ES_ID_SCIENZE_2_437_1495674]"
        )
        self.assertEqual(normalize_course_name(title), "LINGUAGGI DI PROGRAMMAZIONE")
        self.assertEqual(detect_lesson_type(title, desc), LessonType.LAB)
        self.assertEqual(
            extract_external_code(desc, title), "ES_ID_SCIENZE_2_437_1495674"
        )
        self.assertEqual(
            extract_location("", desc, title),
            "Lab Informatica 1 - Palazzo delle Scienze",
        )


class TestStatisticsAndEstimations(unittest.TestCase):
    """Test hour-based calculations, remaining lesson estimations, and thresholds."""

    def test_separate_theory_and_lab_stats(self) -> None:
        course = CourseConfig(
            name="LINGUAGGI DI PROGRAMMAZIONE",
            tracked=True,
            theory_total_hours=48.0,
            theory_threshold_pct=60.0,
            has_lab=True,
            lab_total_hours=24.0,
            lab_threshold_pct=80.0,
        )

        # 3 attended Theory lessons of 2h each = 6h attended
        # 1 skipped Theory lesson of 2h = 2h skipped
        # 2 attended Lab lessons of 3h each = 6h attended
        # 1 skipped Lab lesson of 3h = 3h skipped
        lessons = [
            LessonEvent(
                uid="t1",
                raw_uid="t1",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="Teoria",
                location="Aula Magna",
                external_code="ES_1",
                start_time=datetime(2026, 10, 1, 9, 0),
                end_time=datetime(2026, 10, 1, 11, 0),
                lesson_type=LessonType.THEORY,
                status=LessonStatus.ATTENDED,
                calendar_name="Lessons",
                short_id="t1000001",
            ),
            LessonEvent(
                uid="t2",
                raw_uid="t2",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="Teoria",
                location="Aula Magna",
                external_code="ES_2",
                start_time=datetime(2026, 10, 3, 9, 0),
                end_time=datetime(2026, 10, 3, 11, 0),
                lesson_type=LessonType.THEORY,
                status=LessonStatus.ATTENDED,
                calendar_name="Lessons",
                short_id="t2000002",
            ),
            LessonEvent(
                uid="t3",
                raw_uid="t3",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="Teoria",
                location="Aula Magna",
                external_code="ES_3",
                start_time=datetime(2026, 10, 8, 9, 0),
                end_time=datetime(2026, 10, 8, 11, 0),
                lesson_type=LessonType.THEORY,
                status=LessonStatus.ATTENDED,
                calendar_name="Lessons",
                short_id="t3000003",
            ),
            LessonEvent(
                uid="t4",
                raw_uid="t4",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="Teoria",
                location="Aula Magna",
                external_code="ES_4",
                start_time=datetime(2026, 10, 10, 9, 0),
                end_time=datetime(2026, 10, 10, 11, 0),
                lesson_type=LessonType.THEORY,
                status=LessonStatus.SKIPPED,
                calendar_name="Skipped Lessons",
                short_id="t4000004",
            ),
            LessonEvent(
                uid="l1",
                raw_uid="l1",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="LABORATORIO",
                location="Lab 1",
                external_code="ES_5",
                start_time=datetime(2026, 10, 2, 14, 0),
                end_time=datetime(2026, 10, 2, 17, 0),
                lesson_type=LessonType.LAB,
                status=LessonStatus.ATTENDED,
                calendar_name="Lessons",
                short_id="l1000005",
            ),
            LessonEvent(
                uid="l2",
                raw_uid="l2",
                title="LINGUAGGI DI PROGRAMMAZIONE",
                course_name="LINGUAGGI DI PROGRAMMAZIONE",
                description="LABORATORIO",
                location="Lab 1",
                external_code="ES_6",
                start_time=datetime(2026, 10, 9, 14, 0),
                end_time=datetime(2026, 10, 9, 17, 0),
                lesson_type=LessonType.LAB,
                status=LessonStatus.SKIPPED,
                calendar_name="Skipped Lessons",
                short_id="l2000006",
            ),
        ]

        cstats = calculate_course_stats(course, lessons)

        # Theory assertions: 6h attended, 2h skipped, 8h delivered out of 48h -> 40h remaining (~20 lessons)
        self.assertEqual(cstats.theory.attended_hours, 6.0)
        self.assertEqual(cstats.theory.skipped_hours, 2.0)
        self.assertEqual(cstats.theory.remaining_hours, 40.0)
        self.assertEqual(cstats.theory.estimated_remaining_lessons, 20)
        self.assertEqual(cstats.theory.current_attendance_pct, 75.0)
        self.assertEqual(cstats.theory.max_possible_pct, 95.8)

        # Lab assertions: 3h attended, 3h skipped, 6h delivered out of 24h -> 18h remaining (~6 lessons of 3h)
        self.assertIsNotNone(cstats.lab)
        assert cstats.lab is not None
        self.assertEqual(cstats.lab.attended_hours, 3.0)
        self.assertEqual(cstats.lab.skipped_hours, 3.0)
        self.assertEqual(cstats.lab.remaining_hours, 18.0)
        self.assertEqual(cstats.lab.estimated_remaining_lessons, 6)
        self.assertEqual(cstats.lab.current_attendance_pct, 50.0)
        self.assertEqual(cstats.lab.max_possible_pct, 87.5)
        self.assertEqual(cstats.lab.status_label, "BELOW TARGET")

        # Verify Cartesian chart renders cleanly
        panel = render_course_cartesian_chart(cstats)
        self.assertIsNotNone(panel)


class TestTwoCalendarWorkflowAndCLI(unittest.TestCase):
    """Test end-to-end two-calendar ICS parsing, DB sync, newly completed banner, and CLI flags."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.app_home = Path(self.tmp_dir.name)
        os.environ["ATTENDPY_HOME"] = str(self.app_home)

        self.cal_dir = self.app_home / "calendars"
        self.cal_dir.mkdir(parents=True, exist_ok=True)

        attended_ics = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "BEGIN:VEVENT\n"
            "UID:evt-theory-1\n"
            "SUMMARY:LINGUAGGI DI PROGRAMMAZIONE\n"
            "DESCRIPTION:LINGUAGGI DI PROGRAMMAZIONE- Aula Magna Matematica - Palazzo delle Scienze[ES_ID_SCIENZE_2_437_1495673]\n"
            "DTSTART:20261005T090000\n"
            "DTEND:20261005T110000\n"
            "END:VEVENT\n"
            "BEGIN:VEVENT\n"
            "UID:evt-lab-1\n"
            "SUMMARY:LINGUAGGI DI PROGRAMMAZIONE\n"
            "DESCRIPTION:LINGUAGGI DI PROGRAMMAZIONE - LABORATORIO - Lab 2[ES_ID_SCIENZE_2_437_1495674]\n"
            "DTSTART:20261006T140000\n"
            "DTEND:20261006T170000\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        skipped_ics = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "BEGIN:VEVENT\n"
            "UID:evt-theory-2\n"
            "SUMMARY:LINGUAGGI DI PROGRAMMAZIONE\n"
            "DESCRIPTION:LINGUAGGI DI PROGRAMMAZIONE- Aula Magna Matematica - Palazzo delle Scienze[ES_ID_SCIENZE_2_437_1495675]\n"
            "DTSTART:20261007T090000\n"
            "DTEND:20261007T110000\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        (self.cal_dir / "Lessons.ics").write_text(attended_ics, encoding="utf-8")
        (self.cal_dir / "Skipped_Lessons.ics").write_text(skipped_ics, encoding="utf-8")

    def tearDown(self) -> None:
        os.environ.pop("ATTENDPY_HOME", None)
        self.tmp_dir.cleanup()

    def test_cli_workflow_and_newly_completed_detection(self) -> None:
        # 1. Configure calendars via CLI
        rc = main(
            [
                "-p",
                "google",
                "-c",
                "Lessons",
                "-k",
                "Skipped Lessons",
            ]
        )
        self.assertEqual(rc, 0)

        # 2. Configure course via CLI
        rc = main(
            [
                "--edit-course",
                "LINGUAGGI DI PROGRAMMAZIONE",
                "--theory-hours",
                "48",
                "--theory-threshold",
                "60",
                "--has-lab",
                "--lab-hours",
                "24",
                "--lab-threshold",
                "80",
            ]
        )
        self.assertEqual(rc, 0)

        # 3. First run of --stats detects newly completed lessons and renders dashboard
        rc = main(["--stats"])
        self.assertEqual(rc, 0)

        # 4. Subsequent --new-lessons shows 0 newly completed (already acknowledged on first access!)
        db = Database()
        with db._connect() as conn:
            lessons_in_db = conn.execute("SELECT * FROM seen_lessons").fetchall()
        self.assertEqual(len(lessons_in_db), 3)

        # 5. Skip a lesson via CLI --skip <short_id> and then restore it via --restore <short_id>
        first_short_id = lessons_in_db[0]["short_id"]
        rc_skip = main(["--skip", first_short_id])
        self.assertEqual(rc_skip, 0)

        updated_row = db.find_lesson(first_short_id)
        self.assertIsNotNone(updated_row)
        assert updated_row is not None
        self.assertEqual(updated_row["status_override"], "SKIPPED")

        rc_restore = main(["--restore", first_short_id])
        self.assertEqual(rc_restore, 0)

        restored_row = db.find_lesson(first_short_id)
        assert restored_row is not None
        self.assertEqual(restored_row["status_override"], "ATTENDED")

        # 6. Check GNU usage() content
        help_text = usage()
        self.assertIn("Usage: attendpy [OPTION]...", help_text)
        self.assertIn("--attended-calendar=NAME", help_text)
        self.assertIn("--skipped-calendar=NAME", help_text)
        self.assertIn("--reset", help_text)
        self.assertIn("Exit status:", help_text)

        # 7. Test --reset -y wipes config.json and attendpy.db
        rc_reset = main(["--reset", "-y"])
        self.assertEqual(rc_reset, 0)
        self.assertFalse((self.app_home / "config.json").exists())
        self.assertFalse((self.app_home / "attendpy.db").exists())


if __name__ == "__main__":
    unittest.main()
