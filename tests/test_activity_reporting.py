import os
import re
import sqlite3
import tempfile
import unittest
from datetime import date

import add_shift_activities_table
import app


class ActivityReportingTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "activities-report.db")
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, password_hash TEXT, full_name TEXT,
                role TEXT, active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                comment TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE management_notes (
                management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                visibility TEXT NOT NULL,
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                shared_at TEXT,
                shared_by_user_id INTEGER
            );
            CREATE TABLE action_items (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                title TEXT,
                status TEXT,
                priority TEXT,
                created_at TEXT,
                assigned_to_user_id INTEGER
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Director', 'Director', 1),
                (3, 'manager', 'hash', 'Manager', 'Program Manager', 1),
                (4, 'consultant', 'hash', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'worker', 'hash', 'Worker', 'Support Worker', 1),
                (6, 'inactive', 'hash', 'Inactive', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Active Client', 1), (2, 'Other Client', 0),
                (3, 'Inactive Client', 0);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-01', 'Day', 'Open'),
                (11, 1, '2026-08-02', 'Afternoon', 'Open'),
                (12, 1, '2026-08-03', 'Overnight', 'Open'),
                (13, 1, '2026-08-04', 'Unexpected', 'Open'),
                (20, 2, '2026-08-01', 'Day', 'Open');
        """)
        add_shift_activities_table.migrate(conn)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Report User"

    def insert_activity(
        self, activity_id, *, shift_id=10, status="Recorded",
        start="10:00", end="11:00", a=0, t=0, ls=0,
        description="Report activity", user_id=5, bypass_checks=False
    ):
        if not any((a, t, ls)):
            a = 1
        conn = sqlite3.connect(app.DB_NAME)
        try:
            if bypass_checks:
                conn.execute("PRAGMA ignore_check_constraints = ON")
            completed = (
                ("2026-08-05T19:00:00Z", user_id)
                if status == "Completed" else (None, None)
            )
            conn.execute("""
                INSERT INTO shift_activities (
                    shift_activity_id, shift_id, recorded_by_user_id,
                    start_time, end_time, a_selected, t_selected,
                    ls_selected, activity_description, status,
                    completed_at_utc, completed_by_user_id, version_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                activity_id, shift_id, user_id, start, end, a, t, ls,
                description, status, completed[0], completed[1]
            ))
            conn.commit()
        finally:
            conn.close()

    def insert_shift(self, shift_id, shift_date, shift_type="Day"):
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute(
                "INSERT INTO shifts VALUES (?, 1, ?, ?, 'Open')",
                (shift_id, shift_date, shift_type),
            )
            conn.commit()
        finally:
            conn.close()

    def report_context(self, from_date, to_date, group_by="Daily"):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return app._activity_report_context(
                conn,
                1,
                date.fromisoformat(from_date),
                date.fromisoformat(to_date),
                group_by,
            )
        finally:
            conn.close()

    def report(self, query=""):
        return self.client.get("/reports/activities" + query)

    def test_reporting_access_is_database_backed(self):
        for user_id, role in (
            (1, "Admin"), (2, "Director"), (3, "Program Manager"),
            (4, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 200)

        for user_id, role in ((5, "Support Worker"), (6, "Admin"), (5, "Admin")):
            with self.subTest(user_id=user_id, role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 403)

    def test_active_client_is_explicitly_scoped(self):
        self.insert_activity(1, shift_id=10, description="Included")
        self.insert_activity(2, shift_id=20, description="Other client")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Total Activities", page)
        self.assertIn(">1</dd>", page)
        self.assertIn("Included", page)
        self.assertNotIn("Other client", page)

    def test_controls_validate_and_default(self):
        self.login()
        response = self.report("?from_date=2026-08-02&to_date=2026-08-01")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.report("?from_date=not-a-date").status_code, 400
        )
        self.assertEqual(
            self.report("?group_by=Hourly").status_code, 400
        )
        controls = app._activity_report_controls({}, today=date(2026, 8, 12))
        self.assertEqual(controls["from_value"], "2026-08-01")
        self.assertEqual(controls["to_value"], "2026-08-12")

    def test_finalized_summary_categories_overlap_and_in_progress_is_excluded(self):
        self.insert_activity(1, a=1, t=1, ls=0, status="Recorded")
        self.insert_activity(
            2, shift_id=11, a=0, t=0, ls=1, status="Completed"
        )
        self.insert_activity(3, a=1, t=1, ls=1, status="In Progress")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Total Activities</dt><dd>1", page)
        self.assertIn("Activity</dt><dd>1", page)
        self.assertIn("Tangible</dt><dd>1", page)
        self.assertIn("Lifeskill</dt><dd>0", page)
        self.assertIn(
            "An Activity entry may include more than one category",
            page,
        )
        self.assertNotIn("a_selected", page)
        self.assertNotIn("t_selected", page)
        self.assertNotIn("ls_selected", page)

    def test_categories_have_user_facing_labels_and_grouped_chart_series(self):
        self.insert_activity(1, a=1, t=1, ls=1)
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Activity Categories Over Time", page)
        for label in ("Activity", "Tangible", "Lifeskill"):
            self.assertIn(f'data-category="{label}"', page)
        self.assertIn('<th>Activity</th>', page)
        self.assertIn('<th>Tangible</th>', page)
        self.assertIn('<th>Lifeskill</th>', page)
        self.assertNotIn("a_selected", page)
        self.assertNotIn("t_selected", page)
        self.assertNotIn("ls_selected", page)

    def test_calendar_grouping_and_zero_periods_are_preserved(self):
        self.insert_activity(1, shift_id=10)
        self.insert_activity(2, shift_id=11)
        self.login()
        cases = (
            ("Daily", "2026-08-01", "2026-08-03", "2026-08-03"),
            ("Weekly", "2026-08-01", "2026-08-31", "2026-08-10"),
            ("Monthly", "2026-08-01", "2026-12-31", "2026-09-01"),
            ("Annual", "2026-01-01", "2028-12-31", "2027-01-01"),
        )
        for grouping, from_date, to_date, zero_period in cases:
            with self.subTest(grouping=grouping):
                page = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                ).data.decode()
                self.assertRegex(
                    page,
                    rf'<th scope="row">{zero_period}</th>\s*<td>0</td>',
                )

    def test_duration_is_derived_and_missing_is_not_zero(self):
        self.insert_activity(1, start="10:00", end="10:30")
        self.insert_activity(2, start="11:00", end="12:00")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Average Activity Duration</dt><dd>45.0", page)
        self.assertIn("Longest Activity Duration</dt><dd>60", page)
        self.assertIn('data-chart="activity-duration-chart"', page)
        self.assertIn('data-value="45.0"', page)
        self.assertIsNone(app._activity_report_duration("10:00", None))
        self.assertIsNone(app._activity_report_duration("10:00", "09:00"))

    def test_actual_shift_groups_and_invalid_shift_is_reconciled(self):
        self.insert_activity(1, shift_id=10, start="23:00", end="23:30")
        self.insert_activity(2, shift_id=11)
        self.insert_activity(3, shift_id=12)
        self.insert_activity(4, shift_id=13)
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-04"
        ).data.decode()
        self.assertIn("Day</dt><dd>1", page)
        self.assertIn("Afternoon</dt><dd>1", page)
        self.assertIn("Overnight</dt><dd>1", page)
        self.assertIn("Unassigned</dt><dd>1", page)
        self.assertIn("Unassigned / Invalid Shift", page)
        self.assertIn('data-shift="Unassigned"', page)
        self.assertIn('data-value="1"', page)
        self.assertIn('data-total="1">1', page)

    def test_detail_table_includes_selected_range_rows_and_review_links(self):
        self.insert_activity(1, description="Visible finalized")
        self.insert_activity(2, status="In Progress", description="Visible draft")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Visible finalized", page)
        self.assertIn("Visible draft", page)
        self.assertIn("Activity Description", page)
        self.assertIn("Review detail", page)
        self.assertIn("Status", page)

    def test_completed_activity_contributes_to_formal_report_series(self):
        self.insert_activity(
            1, shift_id=11, status="Completed", a=1, start="10:00", end="11:00"
        )

        report = self.report_context("2026-08-02", "2026-08-02")

        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["summary"]["days"], 1)
        self.assertEqual(report["summary"]["shifts"]["Afternoon"], 1)
        self.assertEqual(report["summary"]["categories"]["Activity"], 1)
        self.assertEqual(report["shift_series"][0]["count"], 1)
        self.assertEqual(report["category_series"][0]["counts"]["Activity"], 1)
        self.assertEqual(report["duration_series"][0]["average"], 60)

    def test_days_with_activities_count_finalized_dates_not_rows_or_drafts(self):
        self.insert_activity(1, shift_id=10)
        self.insert_activity(2, shift_id=10, start="12:00", end="13:00")
        self.insert_activity(3, shift_id=11)
        self.insert_activity(4, shift_id=12, status="In Progress")

        summary = self.report_context("2026-08-01", "2026-08-03")["summary"]

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["days"], 2)

    def test_missing_duration_is_excluded_from_overall_and_period_averages(self):
        self.insert_activity(1, shift_id=10, start="10:00", end="11:00")
        self.insert_activity(
            2, shift_id=11, start="10:00", end=None, bypass_checks=True
        )

        report = self.report_context("2026-08-01", "2026-08-02")

        self.assertEqual(report["summary"]["average_duration"], 60)
        self.assertEqual(report["summary"]["longest_duration"], 60)
        self.assertEqual(report["duration_series"][0]["average"], 60)
        self.assertIsNone(report["duration_series"][1]["average"])
        self.assertNotEqual(report["duration_series"][1]["average"], 0)

    def test_invalid_duration_is_excluded_from_duration_metrics(self):
        self.insert_activity(
            1, shift_id=12, start="11:00", end="10:00", bypass_checks=True
        )

        report = self.report_context("2026-08-03", "2026-08-03")

        self.assertIsNone(report["summary"]["average_duration"])
        self.assertIsNone(report["summary"]["longest_duration"])
        self.assertIsNone(report["duration_series"][0]["average"])

    def test_selected_date_boundaries_exclude_adjacent_activity_rows(self):
        self.insert_shift(9, "2026-07-31", "Overnight")
        self.insert_shift(14, "2026-08-05", "Afternoon")
        self.insert_activity(
            1, shift_id=9, t=1, description="Before selected range"
        )
        self.insert_activity(
            2, shift_id=10, a=1, description="On from date"
        )
        self.insert_activity(
            3, shift_id=13, a=1, description="On to date"
        )
        self.insert_activity(
            4, shift_id=14, t=1, description="After selected range"
        )

        report = self.report_context("2026-08-01", "2026-08-04")

        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["days"], 2)
        self.assertEqual(report["summary"]["categories"]["Activity"], 2)
        self.assertEqual(report["summary"]["categories"]["Tangible"], 0)
        self.assertEqual(report["summary"]["shifts"]["Day"], 1)
        self.assertEqual(report["summary"]["shifts"]["Unassigned"], 1)
        self.assertEqual(
            [item["activity_description"] for item in report["occurrences"]],
            ["On from date", "On to date"],
        )
        self.assertEqual(
            [item["count"] for item in report["shift_series"]], [1, 0, 0, 1]
        )

    def test_empty_report_range_renders_zero_and_missing_duration_safely(self):
        self.login()

        response = self.report(
            "?from_date=2026-09-01&to_date=2026-09-03"
        )
        page = response.data.decode()
        report = self.report_context("2026-09-01", "2026-09-03")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Activities</dt><dd>0", page)
        self.assertIn("Days With Activities</dt><dd>0", page)
        self.assertRegex(
            page,
            r'<th scope="row">2026-09-02</th>\s*<td>0</td>',
        )
        self.assertFalse(report["duration_chart"]["has_values"])
        self.assertTrue(
            all(item["average"] is None for item in report["duration_series"])
        )
        self.assertNotIn("Average Activity Duration</dt><dd>0", page)
        self.assertIn("&mdash;", page)

    def test_weekly_bucket_starts_on_monday_containing_shift_date(self):
        report = self.report_context("2026-08-02", "2026-08-08", "Weekly")

        self.assertEqual(report["trend"][0]["period"], "2026-07-27")
        self.assertEqual(
            app._activity_report_period_start(
                date(2026, 8, 2), "Weekly"
            ),
            date(2026, 7, 27),
        )

        self.insert_activity(1, shift_id=11)
        report = self.report_context("2026-08-02", "2026-08-08", "Weekly")
        self.assertEqual(report["trend"][0]["total"], 1)
        self.login()
        page = self.report(
            "?from_date=2026-08-02&to_date=2026-08-08&group_by=Weekly"
        ).data.decode()
        self.assertIn("Week of Jul 27", page)

    def test_category_counts_are_independent_for_all_groupings(self):
        self.insert_activity(1, shift_id=10, a=1, t=1)
        self.insert_activity(2, shift_id=11, t=1, ls=1)
        self.insert_activity(3, shift_id=12, a=1, ls=1)

        expected = {"Activity": 2, "Tangible": 2, "Lifeskill": 2}
        for grouping, from_date, to_date in (
            ("Daily", "2026-08-01", "2026-08-03"),
            ("Weekly", "2026-08-01", "2026-08-31"),
            ("Monthly", "2026-08-01", "2026-12-31"),
            ("Annual", "2026-01-01", "2028-12-31"),
        ):
            with self.subTest(grouping=grouping):
                report = self.report_context(from_date, to_date, grouping)
                totals = {
                    label: sum(
                        period["counts"][label]
                        for period in report["category_series"]
                    )
                    for label in expected
                }
                self.assertEqual(totals, expected)
                self.assertEqual(report["summary"]["total"], 3)

    def test_activity_report_details_link_resolves_for_authorized_role(self):
        self.insert_activity(1, description="Reviewable activity")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        match = re.search(r'href="(/manager-review/activities/1)"', page)

        self.assertIsNotNone(match)
        detail = self.client.get(match.group(1))
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Reviewable activity", detail.data.decode())


if __name__ == "__main__":
    unittest.main()
