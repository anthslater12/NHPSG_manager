import os
import sqlite3
import sys
import tempfile
import unittest

from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_behaviour_occurrences_table
import app


class BehaviourReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "reporting.db")
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
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Dana Director', 'Director', 1),
                (3, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (4, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1),
                (5, 'consultant', 'hash', 'Case Consultant', 'Behaviour Consultant', 1),
                (6, 'inactive', 'hash', 'Inactive Admin', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Client One', 1),
                (2, 'Other Client', 0);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-01', 'Day', 'Open'),
                (11, 1, '2026-08-01', 'Afternoon', 'Open'),
                (12, 1, '2026-08-01', 'Overnight', 'Open'),
                (20, 2, '2026-08-01', 'Day', 'Open');
        """)
        add_behaviour_occurrences_table.migrate(conn)
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
            session["full_name"] = "Report Tester"

    def insert_occurrence(
        self,
        occurrence_id,
        local_date,
        local_time="10:00",
        record_format="V1",
        status="Recorded",
        client_id=1,
        duration=None,
        category="aggression_towards_others",
        shift_id=10,
    ):
        local = datetime.strptime(
            f"{local_date} {local_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=app.VANCOUVER_TIMEZONE)
        occurred_at = app.serialize_behaviour_utc(local)
        values = {
            "behaviour_occurrence_id": occurrence_id,
            "client_id": client_id,
            "shift_id": shift_id,
            "occurred_at_utc": occurred_at,
            "recorded_by_user_id": 1,
            "recorded_at_utc": occurred_at,
            "submission_token": f"report-{occurrence_id}",
            "status": status,
            "record_format": record_format,
        }
        if record_format == "ABC":
            values["behaviour_crying_yelling_screaming"] = 1
            values["duration_until_calm_minutes"] = duration
        else:
            values[category] = 1
            values["notes"] = f"Report occurrence {occurrence_id}"
        if status == "Completed":
            values["completed_at_utc"] = app.serialize_behaviour_utc(
                local.astimezone(timezone.utc).replace(microsecond=0)
            )
            values["completed_by_user_id"] = 1
        if status == "Voided":
            values["voided_at_utc"] = "2026-08-20T19:00:00Z"
            values["voided_by_user_id"] = 1
            values["void_reason"] = "Reporting test void"

        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute(
                f"INSERT INTO behaviour_occurrences "
                f"({', '.join(columns)}) VALUES ({placeholders})",
                [values[column] for column in columns],
            )
            conn.commit()
        finally:
            conn.close()

    def report(self, query=""):
        return self.client.get("/reports/behaviour" + query)

    def test_authorized_roles_and_unauthorized_users(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Director"),
            (3, "Program Manager"),
            (5, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 200)

        for user_id, role in ((4, "Support Worker"), (6, "Admin")):
            with self.subTest(user_id=user_id):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 403)

    def test_single_active_client_is_resolved_and_queries_are_client_scoped(self):
        self.insert_occurrence(1, "2026-08-05", client_id=1)
        self.insert_occurrence(2, "2026-08-05", client_id=2)
        self.login()
        response = self.report("?from_date=2026-08-01&to_date=2026-08-31")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Client One", page)
        self.assertNotIn("Other Client", page)
        self.assertIn("Total Behaviour Occurrences</dt><dd>1", page)

    def test_invalid_dates_and_grouping_are_rejected(self):
        self.login()
        self.assertEqual(
            self.report("?from_date=2026-08-03&to_date=2026-08-01").status_code,
            400,
        )
        self.assertEqual(
            self.report("?from_date=not-a-date&to_date=2026-08-01").status_code,
            400,
        )
        self.assertEqual(
            self.report("?group_by=Hourly").status_code,
            400,
        )

    def test_date_range_uses_vancouver_calendar_boundaries(self):
        self.insert_occurrence(1, "2026-07-31", "23:59")
        self.insert_occurrence(2, "2026-08-01", "00:00")
        self.insert_occurrence(3, "2026-08-01", "23:30")
        self.insert_occurrence(4, "2026-08-02", "00:00")
        self.login()
        response = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        )
        page = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Behaviour Occurrences</dt><dd>2", page)
        self.assertIn("Days With Behaviour</dt><dd>1", page)
        self.assertIn("<dt>Day</dt><dd>2</dd>", page)
        self.assertIn("<th scope=\"row\">2026-08-01</th><td>2</td>", page)
        self.assertIn("&mdash;", page)
        self.assertNotIn("â", page)
        self.assertNotIn("Report occurrence 1", page)
        self.assertNotIn("Report occurrence 4", page)

    def test_summary_excludes_voided_and_separates_formats_and_duration(self):
        self.insert_occurrence(1, "2026-08-01", record_format="V1")
        self.insert_occurrence(
            2, "2026-08-02", record_format="ABC", status="Completed", duration=10
        )
        self.insert_occurrence(
            3, "2026-08-03", record_format="ABC", duration=20
        )
        self.insert_occurrence(4, "2026-08-04", status="Voided")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-04"
        ).data.decode()
        self.assertIn("Total Behaviour Occurrences</dt><dd>3", page)
        self.assertIn("ABC records</dt><dd>2", page)
        self.assertIn("Legacy-format records</dt><dd>1", page)
        self.assertIn("Voided occurrences</dt><dd>1", page)
        self.assertIn("ABC records with duration</dt><dd>2", page)
        self.assertIn("Average Duration Until Calm (minutes)</dt><dd>15.0", page)
        self.assertIn("Median duration until calm (minutes)</dt><dd>15.0", page)
        self.assertIn("Shortest duration until calm (minutes)</dt><dd>10", page)
        self.assertIn("Longest Duration Until Calm (minutes)</dt><dd>20", page)

    def test_only_finalized_occurrences_contribute_to_report_statistics(self):
        self.insert_occurrence(1, "2026-08-01", status="In Progress")
        self.insert_occurrence(
            2, "2026-08-02", record_format="ABC", status="In Progress", duration=99
        )
        self.insert_occurrence(3, "2026-08-01", status="Recorded")
        self.insert_occurrence(
            4, "2026-08-02", record_format="ABC", status="Completed", duration=10
        )
        self.insert_occurrence(5, "2026-08-03", status="Voided")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-03&group_by=Daily"
        ).data.decode()
        self.assertIn("Total Behaviour Occurrences</dt><dd>2", page)
        self.assertIn("ABC records</dt><dd>1", page)
        self.assertIn("Legacy-format records</dt><dd>1", page)
        self.assertIn("Voided occurrences</dt><dd>1", page)
        self.assertIn("ABC records with duration</dt><dd>1", page)
        self.assertIn("Average Duration Until Calm (minutes)</dt><dd>10", page)
        self.assertIn("Median duration until calm (minutes)</dt><dd>10", page)
        self.assertIn("<th scope=\"row\">2026-08-01</th><td>1</td>", page)
        self.assertIn("<th scope=\"row\">2026-08-02</th><td>1</td>", page)
        self.assertNotIn("<th scope=\"row\">2026-08-03</th><td>1</td>", page)
        self.assertIn("In Progress", page)
        self.assertIn("Voided", page)

    def test_charts_use_finalized_occurrences_and_valid_abc_durations(self):
        self.insert_occurrence(1, "2026-08-01", status="In Progress")
        self.insert_occurrence(
            2, "2026-08-01", record_format="ABC", status="In Progress", duration=99
        )
        self.insert_occurrence(
            3, "2026-08-01", record_format="ABC", status="Recorded", duration=10
        )
        self.insert_occurrence(
            4, "2026-08-01", record_format="ABC", status="Completed", duration=20
        )
        self.insert_occurrence(5, "2026-08-01", status="Voided")
        self.insert_occurrence(6, "2026-08-01", status="Recorded")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()

        self.assertIn("Behaviour Occurrences Over Time", page)
        self.assertIn("Average Duration Until Calm Over Time", page)
        self.assertIn('data-period="2026-08-01" data-total="3">3', page)
        self.assertNotIn('data-period="2026-08-01" data-value="5"', page)
        self.assertIn('data-period="2026-08-01" data-value="15.0"', page)
        self.assertNotIn('data-period="2026-08-01" data-value="99"', page)
        self.assertIn("Total Behaviour Occurrences</dt><dd>3", page)
        self.assertIn("Average Duration Until Calm (minutes)</dt><dd>15.0", page)
        self.assertNotIn("<dt>V1 occurrences", page)
        self.assertIn("Additional report statistics", page)

    def test_report_dashboard_presentation_keeps_primary_cards_and_supporting_data(self):
        self.insert_occurrence(1, "2026-08-01")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()

        self.assertEqual(page.count('class="behaviour-report-summary-card"'), 4)
        for label in (
            "Total Behaviour Occurrences",
            "Days With Behaviour",
            "Average Duration Until Calm",
            "Longest Duration Until Calm",
        ):
            self.assertIn(label, page)
        self.assertIn("Actual Shift Breakdown", page)
        self.assertIn("View daily trend data", page)
        self.assertIn("behaviour-report-table-scroll", page)

    def test_short_charts_use_the_report_width_and_shift_colours_are_distinct(self):
        series = [{"period": "2026-08-01", "count": 1}]
        chart = app._behaviour_report_chart_context(
            series, "count", "test-chart", "Test chart", "Occurrences"
        )
        self.assertEqual(chart["width"], 960)
        self.assertNotEqual(
            app.BEHAVIOUR_REPORT_SHIFT_COLORS["Overnight"],
            app.BEHAVIOUR_REPORT_SHIFT_COLORS["Unassigned"],
        )
        self.assertIn("#4c1d95", app.BEHAVIOUR_REPORT_SHIFT_COLORS.values())
        self.assertIn("#6b7280", app.BEHAVIOUR_REPORT_SHIFT_COLORS.values())

    def test_occurrence_chart_stacks_finalized_behaviour_by_nhpsg_shift(self):
        self.insert_occurrence(1, "2026-08-01", "06:59", shift_id=10)
        self.insert_occurrence(2, "2026-08-01", "07:30", shift_id=10)
        self.insert_occurrence(3, "2026-08-01", "15:29", shift_id=11)
        self.insert_occurrence(4, "2026-08-01", "15:30", shift_id=11)
        self.insert_occurrence(5, "2026-08-01", "22:59", shift_id=12)
        self.insert_occurrence(6, "2026-08-01", "23:00", shift_id=12)
        self.insert_occurrence(7, "2026-08-01", "12:00", status="In Progress")
        self.insert_occurrence(8, "2026-08-01", "12:01", status="Voided")
        self.insert_occurrence(9, "2026-08-02", "12:00")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-02&group_by=Daily"
        ).data.decode()

        self.assertIn("Total Behaviour Occurrences</dt><dd>7", page)
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" data-period="2026-08-01" '
            'data-shift="Day" data-value="2"',
            page,
        )
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" data-period="2026-08-01" '
            'data-shift="Afternoon" data-value="2"',
            page,
        )
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" data-period="2026-08-01" '
            'data-shift="Overnight" data-value="2"',
            page,
        )
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" data-period="2026-08-01" '
            'data-total="6">6',
            page,
        )
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" data-period="2026-08-02" '
            'data-total="1">1',
            page,
        )
        for shift in ("Day", "Afternoon", "Overnight"):
            self.assertIn(shift, page)
        self.assertNotIn("Report period", page)

    def test_actual_shift_classification_handles_unassigned_and_invalid_links(self):
        self.insert_occurrence(1, "2026-08-01", "12:00", shift_id=10)
        self.insert_occurrence(2, "2026-08-01", "12:01", shift_id=11)
        self.insert_occurrence(3, "2026-08-01", "12:02", shift_id=12)
        self.insert_occurrence(4, "2026-08-01", "12:03", shift_id=None)
        self.insert_occurrence(5, "2026-08-01", "12:04", shift_id=20)
        self.insert_occurrence(6, "2026-08-01", "12:05", shift_id=999)
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()

        self.assertIn(
            'data-period="2026-08-01" data-shift="Day" data-value="1"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-shift="Afternoon" data-value="1"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-shift="Overnight" data-value="1"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-shift="Unassigned" data-value="3"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-total="6">6',
            page,
        )
        self.assertIn("Unassigned / No Shift", page)
        self.assertIn("Unassigned / Invalid Shift Link", page)
        self.assertIn("<dt>Unassigned</dt><dd>3</dd>", page)
        self.assertNotIn("Night", page)
        self.assertNotIn("Evening", page)

    def test_charts_follow_daily_weekly_monthly_and_annual_grouping(self):
        for occurrence_id, local_date in (
            (1, "2026-08-01"),
            (2, "2026-08-02"),
            (3, "2026-08-10"),
            (4, "2026-09-01"),
            (5, "2027-01-01"),
        ):
            self.insert_occurrence(occurrence_id, local_date)
        self.login()

        cases = (
            ("Daily", "2026-08-01", "2027-01-01", "2026-08-01", 1),
            ("Weekly", "2026-08-01", "2026-08-31", "2026-07-27", 2),
            ("Monthly", "2026-08-01", "2026-12-31", "2026-08-01", 3),
            ("Annual", "2026-01-01", "2027-12-31", "2026-01-01", 4),
        )
        for grouping, from_date, to_date, first_period, count in cases:
            with self.subTest(grouping=grouping):
                page = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                ).data.decode()
                self.assertIn(
                    f'data-period="{first_period}" data-total="{count}">{count}',
                    page,
                )

    def test_chart_period_labels_are_user_friendly_for_each_grouping(self):
        self.insert_occurrence(1, "2026-08-01")
        self.login()
        cases = (
            ("Daily", "2026-08-01", "2026-08-01", "Aug 01"),
            ("Weekly", "2026-08-01", "2026-08-31", "Week of Jul 27"),
            ("Monthly", "2026-08-01", "2026-08-31", "Aug 2026"),
            ("Annual", "2026-01-01", "2026-12-31", "2026"),
        )
        for grouping, from_date, to_date, label in cases:
            with self.subTest(grouping=grouping):
                page = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                ).data.decode()
                self.assertIn(f">{label}</text>", page)
                self.assertNotIn("Report period</text>", page)

    def test_occurrence_series_preserves_zero_periods_for_every_grouping(self):
        self.insert_occurrence(1, "2026-08-01")
        self.insert_occurrence(2, "2026-08-05")
        self.login()

        cases = (
            (
                "Daily", "2026-08-01", "2026-08-05",
                '<th scope="row">2026-08-02</th><td>0</td>',
            ),
            (
                "Weekly", "2026-08-01", "2026-08-31",
                '<th scope="row">2026-08-10</th><td>0</td>',
            ),
            (
                "Monthly", "2026-08-01", "2026-12-31",
                '<th scope="row">2026-09-01</th><td>0</td>',
            ),
            (
                "Annual", "2026-01-01", "2028-12-31",
                '<th scope="row">2027-01-01</th><td>0</td>',
            ),
        )
        for grouping, from_date, to_date, zero_period in cases:
            with self.subTest(grouping=grouping):
                page = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                ).data.decode()
                self.assertIn(zero_period, page)

    def test_duration_chart_marks_periods_without_qualifying_values_as_missing(self):
        self.insert_occurrence(
            1, "2026-08-01", record_format="ABC", duration=10
        )
        self.insert_occurrence(
            2, "2026-08-03", record_format="ABC", duration=20
        )
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-03&group_by=Daily"
        ).data.decode()
        self.assertIn(
            'data-chart="behaviour-duration-chart" '
            'data-period="2026-08-01" data-value="10.0"',
            page,
        )
        self.assertIn(
            'data-chart="behaviour-duration-chart" '
            'data-period="2026-08-03" data-value="20.0"',
            page,
        )
        self.assertNotIn(
            'data-chart="behaviour-duration-chart" '
            'data-period="2026-08-02"',
            page,
        )
        self.assertIn(
            '<text x="', page
        )
        self.assertIn("&mdash;", page)

    def test_long_daily_chart_uses_readable_natural_width_and_scroll_layout(self):
        series = [
            {
                "period": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "count": 1,
            }
            for index in range(40)
        ]
        chart = app._behaviour_report_chart_context(
            series, "count", "test-chart", "Test chart", "Occurrences"
        )
        self.assertGreaterEqual(chart["width"], 960)
        self.assertEqual(len(chart["bars"]), 40)
        self.assertGreaterEqual(
            chart["bars"][1]["x"] - chart["bars"][0]["x"], 72
        )
        with open(
            os.path.join(ROOT, "templates", "behaviour_report.html"),
            encoding="utf-8",
        ) as template_file:
            self.assertIn("overflow-x: auto", template_file.read())

    def test_duration_chart_averages_multiple_records_and_excludes_missing(self):
        self.insert_occurrence(
            1, "2026-08-01", record_format="ABC", duration=10
        )
        self.insert_occurrence(
            2, "2026-08-01", record_format="ABC", duration=20
        )
        self.insert_occurrence(3, "2026-08-01", duration=30)
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()
        self.assertIn('data-period="2026-08-01" data-value="15.0"', page)
        self.assertNotIn('data-period="2026-08-01" data-value="0"', page)
        self.assertIn("ABC records with duration</dt><dd>2", page)

        conn = sqlite3.connect(app.DB_NAME)
        row = conn.execute(
            "SELECT * FROM behaviour_occurrences WHERE behaviour_occurrence_id = 1"
        ).fetchone()
        columns = [column[1] for column in conn.execute(
            "PRAGMA table_info(behaviour_occurrences)"
        ).fetchall()]
        conn.close()
        missing_duration = dict(zip(columns, row))
        missing_duration["duration_until_calm_minutes"] = None
        self.assertEqual(
            app._behaviour_report_duration_series(
                [missing_duration], "Daily"
            ),
            [],
        )

    def test_empty_chart_ranges_render_safely(self):
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()
        self.assertIn(
            "No finalized ABC duration values are available for this chart.",
            page,
        )
        self.assertIn(
            'data-chart="behaviour-occurrence-chart" '
            'data-period="2026-08-01" data-total="0">0',
            page,
        )
        self.assertEqual(page.count("<svg"), 1)

    def test_report_uses_linked_shift_not_timestamp_band(self):
        self.insert_occurrence(1, "2026-08-01", "06:59", shift_id=11)
        self.insert_occurrence(2, "2026-08-01", "07:30", shift_id=12)
        self.insert_occurrence(3, "2026-08-01", "15:30", shift_id=10)
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn(
            'data-period="2026-08-01" data-shift="Day" data-value="1"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-shift="Afternoon" data-value="1"',
            page,
        )
        self.assertIn(
            'data-period="2026-08-01" data-shift="Overnight" data-value="1"',
            page,
        )
        self.assertIn("<th>Shift</th>", page)
        self.assertNotIn("Operational Band", page)
        self.assertNotIn("Night", page)
        self.assertNotIn("Evening", page)

    def test_date_range_handles_vancouver_spring_dst_transition(self):
        self.insert_occurrence(1, "2026-03-07", "23:59")
        self.insert_occurrence(2, "2026-03-08", "00:00")
        self.insert_occurrence(3, "2026-03-08", "23:59")
        self.insert_occurrence(4, "2026-03-09", "00:00")
        self.login()
        response = self.report(
            "?from_date=2026-03-08&to_date=2026-03-08&group_by=Daily"
        )
        page = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Behaviour Occurrences</dt><dd>2", page)
        self.assertIn("<th scope=\"row\">2026-03-08</th><td>2</td>", page)
        self.assertNotIn("Report occurrence 1", page)
        self.assertNotIn("Report occurrence 4", page)

    def test_daily_weekly_monthly_and_annual_series(self):
        rows = (
            (1, "2026-08-01"), (2, "2026-08-02"),
            (3, "2026-08-10"), (4, "2026-09-01"),
            (5, "2027-01-01"),
        )
        for occurrence_id, local_date in rows:
            self.insert_occurrence(occurrence_id, local_date)
        self.login()

        cases = (
            ("Daily", "2026-08-01", "2027-01-01", "2026-08-01", 1),
            ("Weekly", "2026-08-01", "2026-08-31", "2026-07-27", 2),
            ("Monthly", "2026-08-01", "2026-12-31", "2026-08-01", 3),
            ("Annual", "2026-01-01", "2027-12-31", "2026-01-01", 4),
        )
        for grouping, from_date, to_date, first_period, count in cases:
            with self.subTest(grouping=grouping):
                response = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                )
                page = response.data.decode()
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f"<th scope=\"row\">{first_period}</th><td>{count}</td>",
                    page,
                )


if __name__ == "__main__":
    unittest.main()
